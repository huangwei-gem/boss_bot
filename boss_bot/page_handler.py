"""
BOSS 自动回复机器人 - 页面操作模块

封装 DrissionPage 的所有页面操作。
CSS 选择器基于 BOSS 直聘聊天页面实际结构（已通过浏览器实测验证）。
支持 macOS / Windows / Linux 多平台自动检测系统 Chrome。

适配统一项目架构：
- 使用 BrowserManager 共享浏览器实例（可选），或直接 launch_browser（向后兼容）
- 移除 AccountManager 依赖，直接使用 config.COOKIE_FILE
"""

import os
import re
import sys
import time
import json
import logging
import platform
import threading
from typing import List, Optional, Dict
from pathlib import Path

from boss_bot.config import CHAT_URL, COOKIE_FILE, TEST_MODE, TEST_PAGE, HEADLESS
from boss_bot.browser_launcher import launch_browser, BrowserInstance, BrowserManager
from boss_bot.message_store import MessageStore

logger = logging.getLogger(__name__)

# 基础目录
BASE_DIR = Path(__file__).parent


# ── 验证码判据（全项目只此一处，打招呼侧/回复侧/归因侧共用）──
# 只认「页面上真有一张要人操作的东西」：可见的挑战容器，或强文案。
# 以前还认两条现在已知的误判来源，2026-09-30 12:20 就是被它们停的打招呼：
#   1) URL 里的 `?_security_check=1_…`——BOSS 的静默风控参数，页面照样渲染、
#      照样点得到"立即沟通"（12:08:24 落地带这参数，12:08:31 招呼语已发出），
#      而且它不会自己消失，60 秒闸门因此永远等不到"已恢复"。
#   2) 正文里裸着"验证码"三个字——岗位 JD、HR 消息、登录框的"获取验证码"
#      按钮里都有这三个字。
CAPTCHA_BOX_SELECTORS = (".nc-container", ".verify-wrap", ".geetest_panel",
                         ".verify-box", ".captcha-box", ".security-check",
                         ".verify-panel", "#tcaptcha", ".vc-captcha")
CAPTCHA_STRONG_WORDS = ("安全验证", "请完成验证", "拖动滑块", "滑动验证",
                        "人机验证", "图形验证")

# 探针只报证据（候选容器的实测尺寸 + 命中的文案），定罪留给 python：
# 同一份判据既能被单测覆盖，也能被写进日志自证。
CAPTCHA_PROBE_JS = '''(function () {
    var sels = %s;
    var strong = %s;
    var boxes = [];
    for (var i = 0; i < sels.length; i++) {
        var els = document.querySelectorAll(sels[i]);
        for (var j = 0; j < els.length; j++) {
            var r = els[j].getBoundingClientRect();
            var s = window.getComputedStyle(els[j]);
            boxes.push({sel: sels[i], w: Math.round(r.width), h: Math.round(r.height),
                        display: s.display, visibility: s.visibility});
        }
    }
    var body = document.body ? (document.body.innerText || "") : "";
    var words = [];
    for (var k = 0; k < strong.length; k++) {
        if (body.indexOf(strong[k]) >= 0) words.push(strong[k]);
    }
    return JSON.stringify({boxes: boxes, words: words});
})()''' % (json.dumps(list(CAPTCHA_BOX_SELECTORS)),
           json.dumps(list(CAPTCHA_STRONG_WORDS), ensure_ascii=False))


def _probe_data(probe):
    """探针返回值 → dict；读不到/不是 JSON 一律 None（判不了健康）。"""
    if isinstance(probe, dict):
        return probe
    if isinstance(probe, str):
        try:
            data = json.loads(probe)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


def _box_challenges(box: dict) -> bool:
    """占地方、看得见，才算一张真的要人操作的验证。"""
    try:
        w = float(box.get("w") or 0)
        h = float(box.get("h") or 0)
    except (TypeError, ValueError):
        return False
    return (w > 4 and h > 4
            and box.get("display") not in (None, "", "none")
            and box.get("visibility") != "hidden")


def captcha_evidence(probe) -> tuple:
    """探针结果 → (是否确实在验证, 给人看的判据)。"""
    data = _probe_data(probe)
    if data is None:
        return False, ""
    boxes = sorted({b.get("sel", "?") for b in (data.get("boxes") or [])
                    if isinstance(b, dict) and _box_challenges(b)})
    words = [w for w in (data.get("words") or []) if w]
    if boxes:
        return True, "可见挑战容器 " + "、".join(boxes)
    if words:
        return True, "页面文案 " + "、".join(words)
    return False, ""


def classify_health(url: str, probe) -> str:
    """把"页面地址 + 探针证据"归成 ok / need_login / captcha / unknown。

    验证优先于登录：风控滑块常盖在登录框上，判成 need_login 会去动 Cookie 文件、
    逼人重新扫码，而实情只差过一次验证。
    探针拿不到证据时不兜底成 ok——旧代码就是这么漏掉纯图形验证页的。
    """
    is_captcha, _why = captcha_evidence(probe)
    if is_captcha:
        return "captcha"
    u = url or ""
    if "login" in u or "/web/user" in u or "passport" in u:
        return "need_login"
    return "ok" if _probe_data(probe) is not None else "unknown"


class BossChatHandler:
    """BOSS 聊天页面操作处理器

    支持两种浏览器获取方式：
    1. 通过 BrowserManager 共享实例（推荐，多标签页场景）
    2. 直接 launch_browser 创建独立实例（向后兼容）
    """

    def __init__(self, account_id: str = None, headless: bool = None,
                 browser_manager: BrowserManager = None,
                 browser_instance: BrowserInstance = None,
                 cookie_file: str = None):
        """
        Args:
            account_id: 账号 ID（保留参数，用于未来多账号扩展）
            headless: 是否无头模式（仅在使用 launch_browser 时生效）
            browser_manager: BrowserManager 实例（优先使用，共享浏览器）
            browser_instance: 指定的 BrowserInstance（如聊天标签页），优先级最高
            cookie_file: 本账号专属的 Cookie 文件；不传则退回全局那一份
        """
        self._account_id = account_id
        self._cookie_file_override = cookie_file or ""
        self._headless = headless if headless is not None else HEADLESS
        self._browser_manager = browser_manager

        if browser_instance is not None:
            # 使用指定的 BrowserInstance（如聊天标签页）
            self.browser: BrowserInstance = browser_instance
            logger.info("使用指定 BrowserInstance（标签页）")
        elif browser_manager is not None:
            # 使用 BrowserManager 共享浏览器实例
            self.browser: BrowserInstance = browser_manager.launch()
            logger.info("使用 BrowserManager 共享浏览器实例")
        else:
            # 向后兼容：直接创建浏览器实例
            self.browser: BrowserInstance = launch_browser(headless=self._headless)
            logger.info("使用独立浏览器实例（launch_browser）")

        self.page = self.browser.page
        self._logged_in = False
        self._login_event = threading.Event()
        self._msg_store = MessageStore()

    def login(self, timeout: int = 300) -> bool:
        """
        检查登录状态。
        返回 True = 已登录，False = 需要手动登录。
        """
        if TEST_MODE:
            # 测试模式：直接打开本地 mock 页面，视为已登录
            url = TEST_PAGE if TEST_PAGE.startswith("file:") else f"file://{TEST_PAGE}"
            self.page.get(url)
            time.sleep(1)
            self._logged_in = True
            logger.info("[TEST_MODE] 已打开 mock 页面，视为已登录")
            return True

        # 先访问主站，确保 Cookie 作用域正确
        self.page.get("https://www.zhipin.com")
        time.sleep(2)

        # 处理首次访问弹窗
        self._dismiss_login_popup()

        # 尝试加载已保存的 cookies
        if self._load_cookies():
            self.page.get(CHAT_URL)
            time.sleep(3)
            if not self._is_login_page():
                logger.info("通过 Cookie 自动登录成功")
                self._logged_in = True
                return True

        # 需要手动登录
        logger.info("需要登录，正在跳转到登录页面...")
        self.page.get("https://www.zhipin.com/web/user/?ka=header-login")
        logger.info("请在浏览器中手动登录 BOSS 直聘，登录完成后点击网页上的「我已登录」按钮")
        self._logged_in = False
        return False

    def wait_for_login_confirm(self, timeout: int = 300) -> bool:
        """等待用户点击「我已登录」按钮（由 API 端点调用 confirm_login 来唤醒）。"""
        if self._login_event.wait(timeout=timeout):
            self._login_event.clear()
            return True
        return False

    def confirm_and_save(self) -> bool:
        """前端「我已登录」按钮调用 — 保存 Cookie 并通知机器人继续。"""
        try:
            logger.info("用户确认登录，正在保存 Cookie...")
            # 导航到主站确保 Cookie 作用域正确
            self.page.get("https://www.zhipin.com")
            time.sleep(2)
            self._dismiss_login_popup()
            time.sleep(0.5)
            # 保存 Cookie
            self._save_cookies()
            self._logged_in = True
            # 唤醒等待中的机器人线程
            self._login_event.set()
            logger.info("✅ Cookie 已保存，登录完成")
            return True
        except Exception as e:
            logger.error(f"确认登录失败: {e}")
            return False

    def _dismiss_login_popup(self):
        """处理 BOSS 首页首次访问弹窗"""
        try:
            close_btn = self.page.ele("text:关闭", timeout=3)
            if close_btn:
                close_btn.click()
                logger.info("已关闭首页弹窗")
                time.sleep(0.5)
        except Exception:
            pass

    def _is_login_page(self) -> bool:
        """判断当前是否需要登录"""
        try:
            url = self.page.url or ""
            if "login" in url or "/web/user" in url or "passport" in url:
                return True
            if "chat" in url or "geek" in url:
                # 检查是否有聊天列表
                result = self.page.run_js(
                    'document.querySelector(".friend-content") ? "ok" : "no"',
                    as_expr=True
                )
                return result != "ok"
            return True
        except Exception:
            return True

    def _get_cookie_file(self) -> str:
        """本账号的 Cookie 文件路径。

        以前这里无条件返回全局 config.COOKIE_FILE，于是账号2 确认登录时会把
        自己的会话写进主账号那份文件里，两个号的登录态互相覆盖。
        """
        if self._cookie_file_override:
            from boss_bot.unified_config import resolve_path
            return str(resolve_path(self._cookie_file_override))
        return COOKIE_FILE

    def _save_cookies(self):
        """保存 cookies 到文件（使用 CDP 获取完整 Cookie，包括 HttpOnly）"""
        try:
            cookies = self._get_all_cookies()
            cookie_file = self._get_cookie_file()
            if cookies:
                with open(cookie_file, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, ensure_ascii=False, indent=2)
                logger.info(f"Cookie 已保存到 {cookie_file} ({len(cookies)} 个)")
            else:
                self.browser.save_cookies(cookie_file)
                logger.info(f"Cookie 已保存到 {cookie_file}（兜底方式）")
        except Exception as e:
            logger.error(f"保存 Cookie 失败: {e}")

    def _get_all_cookies(self) -> list:
        """获取所有 Cookie（包括 HttpOnly）
        使用 CDP Storage.getCookies 而非 tab.cookies，因为需要跨域名获取所有 cookie。"""
        try:
            browser = self._get_browser_obj()
            if browser is not None:
                result = browser._run_cdp('Storage.getCookies')
                return list(result.get('cookies', []))
        except Exception:
            pass
        # 兜底
        try:
            return list(self.browser.cookies())
        except Exception:
            return []

    def _get_browser_obj(self):
        """获取底层 Chromium 对象"""
        if hasattr(self.browser, 'browser'):
            return self.browser.browser
        if hasattr(self.browser, '_chromium'):
            return self.browser._chromium
        return None

    def _load_cookies(self) -> bool:
        """从文件加载 cookies"""
        try:
            cookie_file = self._get_cookie_file()
            result = self.browser.load_cookies(cookie_file)
            if result:
                logger.info(f"已从 {cookie_file} 加载 Cookie")
            return result
        except Exception as e:
            logger.error(f"加载 Cookie 失败: {e}")
            return False

    def save_cookies_manual(self) -> bool:
        """手动保存 Cookie（用户点击"已登录"按钮后调用）"""
        try:
            # 先导航到主站确保 cookie 作用域正确
            self.page.get("https://www.zhipin.com")
            time.sleep(1)
            self._dismiss_login_popup()
            time.sleep(0.5)
            self._save_cookies()
            return True
        except Exception as e:
            logger.error(f"手动保存 Cookie 失败: {e}")
            return False

    def go_to_chat(self):
        """导航到聊天页面"""
        if TEST_MODE:
            url = TEST_PAGE if TEST_PAGE.startswith("file:") else f"file://{TEST_PAGE}"
            if not (self.page.url or "").startswith("file:"):
                self.page.get(url)
                time.sleep(1)
            return
        current_url = self.page.url or ""
        if CHAT_URL not in current_url:
            self.page.get(CHAT_URL)
            time.sleep(3)

    def get_unread_chats(self) -> List[Dict]:
        """
        获取所有未读聊天会话列表。

        实测 CSS（2026-09-09 浏览器验证）:
        - 聊天项容器: .friend-content（不是 ul[role='group'] > li[role='listitem']）
        - 未读标记: .notice-badge（在 .figure 内）
        - 名称: .name-text（在 .title-box .name-box 内）
        - 最后一条消息: .last-msg-text（在 .gray.last-msg 内）
        """
        self.go_to_chat()
        time.sleep(1)

        unread_chats = []
        try:
            # 用 JS 获取未读聊天信息（DrissionPage eles 对 li[role=listitem] 返回 0）
            result = self.page.run_js('''(
                function() {
                    var friendEls = document.querySelectorAll(".friend-content");
                    var unread = [];
                    for (var i = 0; i < friendEls.length; i++) {
                        var el = friendEls[i];
                        // 检查是否有未读标记（必须可见且有计数文本）
                        var badge = el.querySelector(".notice-badge");
                        if (!badge) continue;
                        if (badge.offsetParent === null) continue;
                        var countText = badge.textContent.trim();
                        if (!countText) continue;
                        var count = parseInt(countText) || 1;

                        var nameEl = el.querySelector(".name-text");
                        var name = nameEl ? nameEl.textContent.trim() : "未知";

                        // 公司：重名昵称唯一可靠的判据（实测 34 行 (姓名,公司) 全唯一，
                        // 只用姓名有 4 组撞车），且它就挂在行上，不依赖"点开的是谁"
                        var box = el.querySelector(".name-box");
                        var company = "";
                        if (box) {
                            var spans = [];
                            for (var k = 0; k < box.children.length; k++) {
                                var c = box.children[k];
                                if (c.tagName === "SPAN") {
                                    var t = (c.textContent || "").trim();
                                    if (t) spans.push(t);
                                }
                            }
                            company = spans.length > 1 ? spans[1] : "";
                        }

                        var previewEl = el.querySelector(".last-msg-text");
                        var preview = previewEl ? previewEl.textContent.trim() : "";

                        unread.push({
                            index: i,
                            name: name,
                            company: company,
                            preview: preview,
                            unread_count: count
                        });
                    }
                    return JSON.stringify(unread);
                }
            )()''', as_expr=True)

            if result:
                unread_chats = json.loads(result)
            # 注意：不保存 DrissionPage element 引用。
            # 实测 eles() 返回数量与 DOM 不一致（会话卡片 active 时漏元素），
            # 统一用 JS 按 index 点击（与扫描同一 DOM 顺序，索引绝对一致）。

        except Exception as e:
            logger.error(f"获取未读聊天列表失败: {e}")

        logger.debug(f"发现 {len(unread_chats)} 个未读会话")
        return unread_chats

    # 滚动探针回报"当前位置/总高"，读不出这个格式就说明这页没有可滚的列表
    _SIDEBAR_POS_RE = re.compile(r"^\d+/\d+$")

    # 侧栏是虚拟列表：一次只渲染可视区那几十行，不滚就够不着靠后的会话
    _SIDEBAR_SCROLL_JS = '''(
        function() {
            var box = document.querySelector(".user-list-content");
            if (!box) return "no-box";
            box.scrollTop = box.scrollTop + 700;
            box.dispatchEvent(new Event("scroll", {bubbles: true}));
            return String(Math.round(box.scrollTop)) + "/" + String(box.scrollHeight);
        }
    )()'''

    def get_all_chats(self, max_rounds: int = 30) -> List[Dict]:
        """获取侧栏全部会话（不看红点），用于启动后的一次性全量同步。

        与 get_unread_chats 同一套 CSS 判据（.friend-content / .name-text /
        .name-box / .last-msg-text），只是不做"有没有未读标记"的过滤。
        边滚边收、按 姓名+公司 去重：实测账号2 本地存了 68 个会话，
        不滚只读到 40 个，没渲染的那半截既进不了全量同步也进不了补发简历。
        """
        self.go_to_chat()
        time.sleep(1)

        merged = {}
        prev_pos = None
        for _ in range(max_rounds):
            for row in self._sidebar_rows():
                merged.setdefault((row.get("name", ""), row.get("company", "")), row)
            pos = str(self.page.run_js(self._SIDEBAR_SCROLL_JS, as_expr=True))
            time.sleep(0.6)
            if not self._SIDEBAR_POS_RE.match(pos):
                break                      # 这页没容器可滚，读到多少算多少
            if pos == prev_pos:
                break                      # 已经到底
            prev_pos = pos
        all_chats = list(merged.values())
        logger.debug(f"侧栏收集完成：{len(all_chats)} 个会话")
        return all_chats

    # 探针：目标那一行现在渲染出来没有。__WANT__ 由 json 填，
    # HAS_ROW / var want = {...} 这两处字样是 tests/test_pending_resume.py 的假页面认的约定
    _SIDEBAR_HAS_ROW_JS = '''(
        function() {
            /* HAS_ROW */
            var want = __WANT__;
            var items = document.querySelectorAll(".friend-content");
            for (var i = 0; i < items.length; i++) {
                var el = items[i];
                var n = el.querySelector(".name-text");
                var name = n ? n.textContent.trim() : "";
                var box = el.querySelector(".name-box"), comp = "";
                if (box) {
                    var spans = [];
                    for (var k = 0; k < box.children.length; k++) {
                        var c = box.children[k];
                        if (c.tagName === "SPAN") {
                            var t = (c.textContent || "").trim();
                            if (t) spans.push(t);
                        }
                    }
                    comp = spans.length > 1 ? spans[1] : "";
                }
                if (name === want.n && (!want.c || comp === want.c)) return "yes";
            }
            return "no";
        }
    )()'''

    # 回顶部：滚到底之后直接找靠前的会话是找不到的，必须先归零再往下找
    _SIDEBAR_RESET_JS = '''(
        function() {
            var box = document.querySelector(".user-list-content");
            /* RESET */
            if (!box) return "0";
            box.scrollTop = 0;
            box.dispatchEvent(new Event("scroll", {bubbles: true}));
            return "0";
        }
    )()'''

    def _sidebar_has_row(self, name: str, company: str) -> bool:
        js = self._SIDEBAR_HAS_ROW_JS.replace(
            "__WANT__", json.dumps({"n": name, "c": company}, ensure_ascii=False))
        try:
            return self.page.run_js(js, as_expr=True) == "yes"
        except Exception as e:
            logger.debug(f"侧栏行探针失败: {e}")
            return False

    def _scroll_to_chat_row(self, name: str, company: str, max_steps: int = 30) -> bool:
        """把侧栏滚到"姓名+公司"那一行，滚到底还没有就返回 False。

        虚拟列表只渲染可视区那几十行，"当前 DOM 里没有"不等于"没这个会话"：
        实测补扫时列表已被收集流程滚到底，8 个欠简历的目标全被判成
        "会话切换校验失败"，一个都没点开。
        """
        try:
            self.page.run_js(self._SIDEBAR_RESET_JS, as_expr=True)
            time.sleep(0.4)
            prev_pos = None
            for _ in range(max_steps):
                if self._sidebar_has_row(name, company):
                    return True
                pos = str(self.page.run_js(self._SIDEBAR_SCROLL_JS, as_expr=True))
                time.sleep(0.5)
                if not self._SIDEBAR_POS_RE.match(pos):
                    # 这页没有可滚的容器（或探针被换掉了）：盲滚 30 轮只会白等
                    return False
                if pos == prev_pos:        # 位置不动了 = 已经到底
                    return False
                prev_pos = pos
        except Exception as e:
            logger.debug(f"侧栏滚动定位失败 [{name}]: {e}")
        return False

    def _sidebar_rows(self) -> List[Dict]:
        """读一次当前渲染出来的侧栏行（不滚动）"""
        all_chats = []
        try:
            result = self.page.run_js('''(
                function() {
                    var friendEls = document.querySelectorAll(".friend-content");
                    var rows = [];
                    for (var i = 0; i < friendEls.length; i++) {
                        var el = friendEls[i];
                        var nameEl = el.querySelector(".name-text");
                        var name = nameEl ? nameEl.textContent.trim() : "未知";
                        var box = el.querySelector(".name-box");
                        var company = "";
                        if (box) {
                            var spans = [];
                            for (var k = 0; k < box.children.length; k++) {
                                var c = box.children[k];
                                if (c.tagName === "SPAN") {
                                    var t = (c.textContent || "").trim();
                                    if (t) spans.push(t);
                                }
                            }
                            company = spans.length > 1 ? spans[1] : "";
                        }
                        var previewEl = el.querySelector(".last-msg-text");
                        var preview = previewEl ? previewEl.textContent.trim() : "";
                        rows.push({
                            index: i,
                            name: name,
                            company: company,
                            preview: preview,
                            unread_count: 0
                        });
                    }
                    return JSON.stringify(rows);
                }
            )()''', as_expr=True)

            if result:
                all_chats = json.loads(result)

        except Exception as e:
            logger.error(f"获取全部会话列表失败: {e}")
        return all_chats

    def read_selected_row(self) -> Dict:
        """读侧栏里当前真正带 selected 态的那一行（身份以它为准，不靠点击时的索引）

        BOSS 会随时重排侧栏：按索引点第 i 行之后，第 i 行可能已经不是刚才那个人，
        而"选中"落在别的行上。所以点完必须回头看 selected 落在哪，用它身上的
        姓名+公司当身份 —— 这两个字段就在行里，不需要点开，也就不会张冠李戴。
        """
        try:
            result = self.page.run_js('''(
                function() {
                    var items = document.querySelectorAll(".friend-content");
                    for (var i = 0; i < items.length; i++) {
                        var it = items[i];
                        if ((it.className || "").indexOf("selected") < 0) continue;
                        var q = function(s) {
                            var e = it.querySelector(s);
                            return e ? (e.textContent || "").trim() : "";
                        };
                        // .name-box 里依次是 姓名 / 公司 / 头衔（后两个是裸 span）
                        var box = it.querySelector(".name-box");
                        var spans = [];
                        if (box) {
                            for (var k = 0; k < box.children.length; k++) {
                                var c = box.children[k];
                                if (c.tagName === "SPAN") {
                                    var t = (c.textContent || "").trim();
                                    if (t) spans.push(t);
                                }
                            }
                        }
                        return JSON.stringify({
                            index: i,
                            name: q(".name-text"),
                            company: spans.length > 1 ? spans[1] : "",
                            title: spans.length > 2 ? spans[2] : ""
                        });
                    }
                    return "";
                }
            )()''', as_expr=True)
            return json.loads(result) if result else {}
        except Exception as e:
            logger.debug(f"读取选中会话行失败: {e}")
            return {}

    def enter_chat(self, chat_info: dict, retries: int = 2) -> bool:
        """点击进入某个聊天并校验切换成功，等待聊天内容加载。

        校验不能只看顶栏姓名：实测侧栏 34 行里有 4 组重名昵称（两个陈女士分属
        小智时代科技/艾秒广告），姓名一样，点错一个照样"姓名对得上"。所以要求
        点击的那一行确实变成 selected，且 selected 行的姓名与目标一致。
        返回 True=切换成功并确认；False=校验失败（调用方应跳过该会话）。
        """
        idx = chat_info.get('index', 0)
        expected_name = chat_info.get('name', '')
        expected_company = chat_info.get('company', '')

        # 先确认目标行真的在渲染出来的那几十行里，不在就滚过去。
        # 侧栏是虚拟列表：账号2 实测 171 行只渲染 ~20 行，采集时记下的 index
        # 到点击时早就不是同一个人了（按旧 index 点"孙先生|沐数科技"，那位置
        # 上已经是"马女士|掌门教育"，三次重试全点在错的人身上）。
        if not self._sidebar_has_row(expected_name, expected_company):
            if not self._scroll_to_chat_row(expected_name, expected_company):
                logger.warning(
                    f"侧栏滚到底也没有会话 [{expected_name}|{expected_company}]，跳过")
                return False

        for attempt in range(1, retries + 2):
            # 按 姓名+公司 定位行：实测 34 行里 (姓名,公司) 唯一 34/34，
            # 只用姓名则有 4 组撞车。公司字段为空的行退到按索引点，再靠下面
            # 的 selected 复核兜住。
            click_result = self.page.run_js(f'''(
                function() {{
                    var friends = document.querySelectorAll(".friend-content");
                    var want = {{n: {json.dumps(expected_name, ensure_ascii=False)},
                                c: {json.dumps(expected_company, ensure_ascii=False)}}};
                    function info(el) {{
                        var e = el.querySelector(".name-text");
                        var name = e ? e.textContent.trim() : "";
                        var box = el.querySelector(".name-box"), comp = "";
                        if (box) {{
                            var spans = [];
                            for (var k = 0; k < box.children.length; k++) {{
                                var c = box.children[k];
                                if (c.tagName === "SPAN") {{
                                    var t = (c.textContent || "").trim();
                                    if (t) spans.push(t);
                                }}
                            }}
                            comp = spans.length > 1 ? spans[1] : "";
                        }}
                        return {{name: name, company: comp}};
                    }}
                    for (var i = 0; i < friends.length; i++) {{
                        var got = info(friends[i]);
                        if (want.n && got.name === want.n &&
                            (!want.c || got.company === want.c)) {{
                            friends[i].click();
                            return "ok";
                        }}
                    }}
                    if (friends[{idx}]) {{ friends[{idx}].click(); return "by_index"; }}
                    return "not_found";
                }}
            )()''', as_expr=True)
            if click_result == "not_found":
                if self._scroll_to_chat_row(expected_name, expected_company):
                    logger.debug(f"侧栏目标行未渲染，滚到 [{expected_name}] 那一行后重试")
                    continue
                logger.warning(f"侧栏滚到底也找不到会话 [{expected_name}]，跳过")
                return False
            time.sleep(3)

            # 等待输入框加载
            for _ in range(10):
                ready = self.page.run_js(
                    'document.querySelector("#chat-input") ? "ready" : "not ready"',
                    as_expr=True
                )
                if ready == 'ready':
                    break
                time.sleep(0.5)

            # 校验"选中的就是我要的那一行"：selected 行的姓名/公司 与 顶栏姓名 三方对齐
            sel = self.read_selected_row()
            header_name = self.get_boss_name()
            head_ok = (not header_name or not expected_name
                       or header_name == expected_name)
            if not sel:
                # 读到 0 行 selected（BOSS 改了类名等）时不能整轮罢工：
                # 这时只剩顶栏姓名可核，同名风险由调用方按岗位/公司再判
                logger.debug(f"侧栏没有 selected 标记，退回顶栏姓名核对 [{expected_name}]")
                if head_ok:
                    return True
            else:
                sel_ok = (not expected_name or sel.get("name") == expected_name) \
                    and (not expected_company or sel.get("company") == expected_company)
                if sel_ok and head_ok:
                    if click_result == "by_index":
                        logger.debug(f"按索引点开会话 [{expected_name}]，selected 复核通过")
                    return True
            logger.warning(
                f"会话切换校验失败（第 {attempt}/{retries + 1} 次）: "
                f"期望[{expected_name}|{expected_company}], "
                f"selected={sel.get('index')}/{sel.get('name')}|{sel.get('company')}, "
                f"顶栏={header_name!r}，重试..."
            )

        logger.error(f"会话 [{expected_name}|{expected_company}] 切换校验最终失败，应跳过该会话")
        return False

    # 实测线上结构（2026-09-27 抓取 .chat-record 子树）：
    #   ul.im-list > li.message-item[data-mid]（.item-myself / .item-friend）
    #     .item-time > .time
    #     .message-content > .text > i.message-status.status-delivery|status-read
    #                        > p > .text-content
    # 非文字消息（图片/简历卡片/系统条）没有 .text-content，旧实现因此读出空串。
    _READ_ITEMS_JS = r"""
    function one(root, sel) { return root ? root.querySelector(sel) : null; }
    function textOf(el) { return el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
    function statusOf(item) {
        if (one(item, '.message-status.status-read')) return 'read';
        if (one(item, '.message-status.status-delivery')) return 'delivery';
        if (one(item, '.message-status.status-unread')) return 'unread';
        return '';
    }
    function classBlob(item) {
        var s = '';
        item.querySelectorAll('*').forEach(function(e){
            s += ' ' + (e.className || '').toString();
        });
        return s;
    }
    function classify(item, text) {
        // 有 .text-content 就是普通文字 —— 必须先判，否则正文里出现
        // "看了你的简历" 会被当成简历卡片
        if (text) return 'text';
        var cb = classBlob(item);
        if (/竞争者|PK情况|职位详情/.test(textOf(item))
            || /job-card|position|pk-|compet/i.test(cb)) return 'job_card';
        if (/resume|attachment|file-item|send-file|doc-|pdf/i.test(cb)) return 'resume';
        var img = one(item, '.message-content img, .text img');
        if (img && !/avatar|figure|head/i.test(img.className || '')) return 'image';
        if (one(item, 'audio, [class*="voice"]')) return 'voice';
        if (one(item, '[class*="emoji"], [class*="sticker"]')) return 'emoji';
        return 'other';
    }
    var out = [];
    var nodes = document.querySelectorAll('.message-item, .message-tip-bar, .chat-record [class*="tip-bar"]');
    for (var i = 0; i < nodes.length; i++) {
        var it = nodes[i];
        var cls = (it.className || '').toString();
        if (/tip-bar/i.test(cls)) {
            var tip = textOf(it);
            if (tip) {   // 没有文字的系统条是占位噪音，丢掉
                out.push({type: 'system', text: tip, time: '', is_mine: false,
                          isFriend: false, status: '', mid: '', media: ''});
            }
            continue;
        }
        var body = textOf(one(it, '.text-content'));
        var time = textOf(one(it, '.item-time .time')) || textOf(one(it, '.time'));
        var img = one(it, '.message-content img');
        var full = textOf(one(it, '.message-content')) || textOf(it);
        var type = classify(it, body);
        out.push({
            type: type,
            // 非文字消息退回到整条 innerText，不再留空白；卡片只保留标题行
            text: body || (type === 'job_card' || type === 'resume'
                           ? full.slice(0, 120) : full),
            time: time,
            isFriend: cls.indexOf('item-friend') >= 0,
            is_mine: cls.indexOf('item-friend') < 0,
            status: statusOf(it),
            mid: it.getAttribute('data-mid') || '',
            media: (img && img.src && type === 'image') ? img.src : ''
        });
    }
    return JSON.stringify(out);
    """

    def read_latest_messages(self, count: int = 5) -> List[Dict]:
        """读取当前聊天中最近的消息（结构见 _READ_ITEMS_JS 注释）。

        Returns:
            每条含 type/text/time/is_mine/status/mid/media；type 为
            text|image|resume|job_card|emoji|voice|other|system
        """
        messages = []
        try:
            result = self.page.run_js(f'''(
                function() {{
                    {self._READ_ITEMS_JS}
                }}
            )()''', as_expr=True)

            if result:
                all_items = json.loads(result)
                messages = all_items[-count:] if count else all_items

            if messages:
                boss_name = self.get_boss_name()
                job_name = self.get_job_name()
                self._msg_store.save_messages(boss_name or "未知", messages, job_name)
        except Exception as e:
            logger.error(f"读取消息失败: {e}")

        return messages

    def read_all_messages(self, max_scroll_rounds: int = 5,
                          scroll_wait_ms: int = 800) -> List[Dict]:
        """
        读取当前聊天中所有可见消息（完整聊天记录同步）。

        与 read_latest_messages 的区别：
        - 先向上滚动聊天区域加载更多历史消息（滚动到顶部等待加载，
          重复几次直到消息数量不再增加或达到最大滚动次数）
        - 然后读取所有 .message-item 元素（不只最新 count 条）
        - 每条消息包含：text, time, is_mine, isFriend
        - 不调用 save_messages（避免覆盖式保存丢失旧消息），
          由调用方通过 message_store.merge_messages 合并去重

        Args:
            max_scroll_rounds: 向上滚动加载历史的最大轮次（默认5）
            scroll_wait_ms: 每次滚动后等待加载的毫秒数（默认800）

        Returns:
            完整消息列表（按页面顺序，旧消息在前，新消息在后）
        """
        messages = []
        try:
            # 第一步：向上滚动加载更多历史消息
            try:
                self.page.run_js(f'''(
                    function() {{
                        var chatContent = document.querySelector(".chat-content")
                                     || document.querySelector(".message-list")
                                     || document.querySelector(".chat-message-wrap")
                                     || document.querySelector(".message-wrap");
                        if (!chatContent) return JSON.stringify({{ok: false, reason: "no_container"}});
                        var prevCount = -1;
                        var rounds = 0;
                        // 同步滚动若干次（每次滚动后由 Python 侧等待异步加载）
                        while (rounds < {max_scroll_rounds}) {{
                            var curCount = document.querySelectorAll(".message-item").length;
                            if (curCount === prevCount) {{
                                // 数量未变，可能已加载完所有历史
                                break;
                            }}
                            prevCount = curCount;
                            // 滚动到顶部触发加载更多
                            chatContent.scrollTop = 0;
                            rounds++;
                        }}
                        return JSON.stringify({{ok: true, rounds: rounds, finalCount: prevCount}});
                    }}
                )()''', as_expr=True)
            except Exception as e:
                logger.debug(f"滚动加载历史异常（不影响后续读取）: {e}")

            # 每次滚动后等待页面异步加载更多历史消息
            for _ in range(max_scroll_rounds):
                time.sleep(scroll_wait_ms / 1000.0)
                # 检查消息数量是否还在增长，若不再增长则提前结束等待
                try:
                    cur_count_js = self.page.run_js(
                        'document.querySelectorAll(".message-item").length', as_expr=True
                    )
                    if cur_count_js is None:
                        break
                except Exception:
                    break

            # 第二步：读取所有 .message-item 元素
            # mid 与 block 必须一起取：mid 是线上每条都有的唯一键（顺序与去重靠它），
            # block 是整块文本 —— 卡片类消息 .text-content 是空的，只取 text 就会
            # 存成空气泡，界面上多出一行线上看不到的空内容
            result = self.page.run_js('''(
                function() {
                    var items = document.querySelectorAll(".message-item");
                    var result = [];
                    for (var i = 0; i < items.length; i++) {
                        var item = items[i];
                        var textEl = item.querySelector(".text-content");
                        var timeEl = item.querySelector(".item-time .time")
                                  || item.querySelector(".time");
                        var cls = item.className || "";
                        var sys = cls.indexOf("item-system") >= 0;
                        result.push({
                            text: textEl ? textEl.textContent.trim() : "",
                            time: timeEl ? timeEl.textContent.trim() : "",
                            isFriend: cls.indexOf("item-friend") >= 0,
                            is_mine: cls.indexOf("item-friend") < 0 && !sys,
                            is_system: sys,
                            mid: item.getAttribute("data-mid") || "",
                            block: (item.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 300)
                        });
                    }
                    return JSON.stringify(result);
                }
            )()''', as_expr=True)

            if result:
                messages = json.loads(result)

            logger.info(f"[read_all_messages] 读取到完整消息数: {len(messages)}")
        except Exception as e:
            logger.error(f"读取所有消息失败: {e}")

        return messages

    def read_messages_rich(self, max_scroll_rounds: int = 5,
                           scroll_wait_ms: int = 800) -> List[Dict]:
        """读取消息并返回完整元数据（type/status/timestamp/sender/msg_id）。

        基于 tools/chat_page_structure.json 提取的真实 DOM 结构：
        - HR 消息: .message-item.item-friend（左侧气泡）
        - 我方消息: .message-item.item-myself（右侧气泡）
        - 系统消息: .message-item.item-system（居中）
        - 消息 ID: data-mid 属性（每条消息唯一）
        - 送达状态: .message-status.status-delivery（文本"[送达]"）
        - 已读状态: .message-status.status-read（文本"[已读]"）
        - 时间戳: .item-time .time（格式如 "19:37" / "昨天 21:35"）
        - 文本内容: .text-content

        与 read_all_messages 的区别：
        - read_all_messages 仅返回 text/time/is_mine/isFriend 四字段
        - read_messages_rich 返回 sender/msg_id/status/msg_type 等完整元数据
        - sender 用三态互斥 class（item-friend/item-myself/item-system）精确区分
        - status 仅我方消息有值（delivered/read），HR/系统消息为 None

        Args:
            max_scroll_rounds: 向上滚动加载历史的最大轮次（默认 5）
            scroll_wait_ms: 每次滚动后等待加载的毫秒数（默认 800）

        Returns:
            消息列表（按页面顺序，旧消息在前，新消息在后），每条包含：
            - text: 消息文本（.text-content 的 textContent，已 trim）
            - time: 时间戳（.item-time .time 的 textContent，已 trim）
            - sender: "hr" / "me" / "system"
            - msg_id: data-mid 属性值（无则空字符串）
            - status: "delivered" / "read" / None（仅我方消息有值）
            - msg_type: "text" / "image" / "resume" / "system"
        """
        messages: List[Dict] = []
        try:
            # 第一步：向上滚动加载更多历史消息（与 read_all_messages 同策略）
            try:
                self.page.run_js(f'''(
                    function() {{
                        var chatContent = document.querySelector(".chat-content")
                                     || document.querySelector(".message-list")
                                     || document.querySelector(".chat-message-wrap")
                                     || document.querySelector(".message-wrap");
                        if (!chatContent) return JSON.stringify({{ok: false, reason: "no_container"}});
                        var prevCount = -1;
                        var rounds = 0;
                        while (rounds < {max_scroll_rounds}) {{
                            var curCount = document.querySelectorAll(".message-item").length;
                            if (curCount === prevCount) break;
                            prevCount = curCount;
                            chatContent.scrollTop = 0;
                            rounds++;
                        }}
                        return JSON.stringify({{ok: true, rounds: rounds, finalCount: prevCount}});
                    }}
                )()''', as_expr=True)
            except Exception as e:
                logger.debug(f"滚动加载历史异常（不影响后续读取）: {e}")

            for _ in range(max_scroll_rounds):
                time.sleep(scroll_wait_ms / 1000.0)
                try:
                    cur_count_js = self.page.run_js(
                        'document.querySelectorAll(".message-item").length', as_expr=True
                    )
                    if cur_count_js is None:
                        break
                except Exception:
                    break

            # 第二步：用 JS 提取完整元数据
            # 注意：sender 用三态互斥 class 精确区分（item-friend/item-myself/item-system）
            #       status 仅我方消息有值，HR/系统消息保持 None
            #       msg_type 判定优先级：system > image > resume > text
            result = self.page.run_js('''(
                function() {
                    var items = document.querySelectorAll('.message-item');
                    var result = [];
                    for (var i = 0; i < items.length; i++) {
                        var item = items[i];
                        var cls = item.className || "";
                        var textEl = item.querySelector('.text-content');
                        var timeEl = item.querySelector('.item-time .time');

                        // 判断发送者（三态互斥）
                        var sender = "system";
                        if (cls.indexOf('item-friend') >= 0) sender = "hr";
                        else if (cls.indexOf('item-myself') >= 0) sender = "me";

                        // 判断消息状态（仅我方消息）
                        var status = null;
                        if (sender === "me") {
                            var statusEl = item.querySelector('.message-status');
                            if (statusEl) {
                                var statusCls = statusEl.className || "";
                                if (statusCls.indexOf('status-read') >= 0) status = "read";
                                else if (statusCls.indexOf('status-delivery') >= 0) status = "delivered";
                            }
                        }

                        // 判断消息类型
                        var msgType = "text";
                        if (sender === "system") msgType = "system";
                        else if (item.querySelector('img.image-circle')) msgType = "image";
                        else if (item.querySelector('[class*="resume"]')) msgType = "resume";

                        result.push({
                            text: textEl ? textEl.textContent.trim() : "",
                            time: timeEl ? timeEl.textContent.trim() : "",
                            sender: sender,
                            msg_id: item.getAttribute('data-mid') || "",
                            status: status,
                            msg_type: msgType
                        });
                    }
                    return JSON.stringify(result);
                }
            )()''', as_expr=True)

            if result:
                messages = json.loads(result)

            logger.info(f"[read_messages_rich] 读取到完整元数据消息数: {len(messages)}")
        except Exception as e:
            logger.error(f"读取消息元数据失败: {e}")

        return messages

    def get_chat_conversations(self) -> List[Dict]:
        """获取聊天列表侧边栏所有会话。

        基于 tools/chat_page_structure.json 提取的真实 DOM 结构：
        - 列表容器: .user-list
        - 会话项: .friend-content
        - 会话名称: .name-text
        - 最后消息: .last-msg-text
        - 未读数: .notice-badge（如 <span class="notice-badge">3</span>）
        - 选中状态: .friend-content.selected

        与 get_unread_chats 的区别：
        - get_unread_chats 仅返回有未读标记的会话
        - get_chat_conversations 返回所有会话（含已读/未读/选中状态）
        - 额外返回 is_selected 字段，便于定位当前正在聊的会话

        Returns:
            会话列表（按侧边栏顺序），每个含：
            - index: 在侧边栏中的索引（用于 enter_chat）
            - name: 会话名称（.name-text 的 textContent，已 trim）
            - last_message: 最后一条消息预览（.last-msg-text，已 trim）
            - unread_count: 未读消息数（int，0 表示无未读）
            - is_selected: 是否为当前选中会话（bool）
        """
        self.go_to_chat()
        time.sleep(1)

        conversations: List[Dict] = []
        try:
            result = self.page.run_js('''(
                function() {
                    var friendEls = document.querySelectorAll(".friend-content");
                    var result = [];
                    for (var i = 0; i < friendEls.length; i++) {
                        var el = friendEls[i];
                        var cls = el.className || "";

                        var nameEl = el.querySelector(".name-text");
                        var name = nameEl ? nameEl.textContent.trim() : "未知";

                        // 公司挂在 .name-box 的裸 span 上（姓名/公司/头衔三截）：
                        // 重名昵称靠它区分，实测 34 行 (姓名,公司) 唯一
                        var box = el.querySelector(".name-box");
                        var company = "";
                        if (box) {
                            var spans = [];
                            for (var k = 0; k < box.children.length; k++) {
                                var c = box.children[k];
                                if (c.tagName === "SPAN") {
                                    var t = (c.textContent || "").trim();
                                    if (t) spans.push(t);
                                }
                            }
                            company = spans.length > 1 ? spans[1] : "";
                        }

                        var previewEl = el.querySelector(".last-msg-text");
                        var lastMsg = previewEl ? previewEl.textContent.trim() : "";

                        // 未读数：.notice-badge 文本为数字时计入，否则 0
                        var unread = 0;
                        var badge = el.querySelector(".notice-badge");
                        if (badge && badge.offsetParent !== null) {
                            var countText = badge.textContent.trim();
                            if (countText) {
                                var parsed = parseInt(countText);
                                unread = isNaN(parsed) ? 1 : parsed;
                            }
                        }

                        // 选中状态：.friend-content.selected
                        var isSelected = cls.indexOf("selected") >= 0;

                        result.push({
                            index: i,
                            name: name,
                            company: company,
                            last_message: lastMsg,
                            unread_count: unread,
                            is_selected: isSelected
                        });
                    }
                    return JSON.stringify(result);
                }
            )()''', as_expr=True)

            if result:
                conversations = json.loads(result)

        except Exception as e:
            logger.error(f"获取聊天会话列表失败: {e}")

        logger.debug(f"[get_chat_conversations] 共 {len(conversations)} 个会话")
        return conversations

    def get_boss_name(self) -> str:
        """获取当前聊天对象的名称

        尝试多个选择器，适配 BOSS 直聘不同版本页面结构。
        """
        try:
            result = self.page.run_js('''(
                function() {
                    // 多个选择器兜底
                    var sels = [
                        ".top-info-content .name-text",
                        ".top-info-box .name-text",
                        ".chat-header .name-text",
                        ".user-info .name-text",
                        ".header-content .name-text",
                        ".top-info .name",
                        ".boss-name",
                        ".chat-title .name"
                    ];
                    for (var i = 0; i < sels.length; i++) {
                        var el = document.querySelector(sels[i]);
                        if (el && el.textContent.trim()) {
                            return el.textContent.trim();
                        }
                    }
                    return "";
                }
            )()''', as_expr=True)
            return result or ""
        except Exception:
            return ""

    def get_job_name(self) -> str:
        """获取当前聊天对应的岗位名称"""
        try:
            result = self.page.run_js(
                'document.querySelector(".chat-position-content .position-content") ? document.querySelector(".chat-position-content .position-content").textContent.trim() : ""',
                as_expr=True
            )
            return result or ""
        except Exception:
            return ""

    def send_text(self, text: str, retries: int = 3) -> bool:
        """
        在当前聊天中输入并发送文字消息。

        实测 CSS（2026-09-09 浏览器验证）:
        - 输入框: #chat-input.chat-input（contenteditable div）
        - 发送按钮: .btn-v2.btn-sure-v2.btn-send（注意有 disabled class 时不可点击）
        - 输入方式: textContent + dispatchEvent('input') 才能触发 Vue 响应
        """
        for attempt in range(1, retries + 1):
            try:
                # 转义特殊字符
                escaped_text = text.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')

                # 输入文字
                type_result = self.page.run_js(f'''(
                    function() {{
                        var input = document.querySelector("#chat-input");
                        if (!input) return "not found";
                        input.focus();
                        input.textContent = `{escaped_text}`;
                        input.dispatchEvent(new Event("input", {{bubbles: true}}));
                        return "typed";
                    }}
                )()''', as_expr=True)

                if type_result != 'typed':
                    logger.warning(f"输入框未找到（尝试 {attempt}/{retries}）")
                    time.sleep(1)
                    continue

                time.sleep(0.5)

                # 点击发送按钮
                send_result = self.page.run_js('''(
                    function() {
                        var btn = document.querySelector(".btn-send");
                        if (!btn) return "button not found";
                        if (btn.classList.contains("disabled")) return "button disabled";
                        btn.click();
                        return "sent";
                    }
                )()''', as_expr=True)

                if send_result == 'sent':
                    logger.info(f"已发送文字: {text[:30]}...")
                    time.sleep(0.5)
                    return True
                else:
                    logger.warning(f"发送按钮不可用（尝试 {attempt}/{retries}）: {send_result}")
                    time.sleep(1)

            except Exception as e:
                logger.error(f"发送文字失败（尝试 {attempt}/{retries}）: {e}")
                time.sleep(1)

        logger.error(f"发送文字最终失败: {text[:30]}...")
        return False

    def send_resume(self, retries: int = 2) -> bool:
        """
        点击发送简历按钮，确认发送。

        实测 CSS（2026-09-20 破解浏览器实测，用户黄维账号）:
        - 发简历按钮: .toolbar-btn（文本含"发简历"）
        - 简历选择弹层（情况A，已上传简历）: .choose-resume-dialog（标题"请选择要发送的简历"）
          - 容器: .boss-popup__wrapper.boss-dialog.boss-dialog__wrapper.dialog-default.choose-resume-dialog
          - 简历列表: .resume-list > li.list-item
          - 简历名: .resume-name
          - 发送按钮: .btn-v2.btn-sure-v2.btn-confirm（文本"发送"，选中简历后 enabled）
          - 管理附件: .manage-btn
        - 上传弹层（情况B，未上传简历）: .upload-resume-dialog 或 .upload-select-dialog 可见
        - 旧版确认弹层（兜底）: .panel-resume.sentence-popover（"确定向 Boss 发送简历吗？"）

        关键流程:
        1. 点击 .toolbar-btn（文本含"发简历"）
        2. 等待 .choose-resume-dialog 弹层出现
        3. 点击 .resume-list .list-item 选中简历（发送按钮才会 enabled）
        4. 点击 .btn-v2.btn-sure-v2.btn-confirm 发送简历
        5. 验证弹层消失 + 消息列表出现简历消息

        注意：
        - .btn-v2.btn-sure-v2.btn-send 是另一个弹层的按钮，始终 disabled，不要用！
        - 必须先点击简历项选中，btn-confirm 才会从 disabled 变成 enabled
        """
        for attempt in range(1, retries + 2):
            try:
                # 1. 点击"发简历"按钮
                click_result = self.page.run_js('''(
                    function() {
                        var btns = document.querySelectorAll(".toolbar-btn");
                        for (var i = 0; i < btns.length; i++) {
                            if (btns[i].textContent.trim().indexOf("发简历") >= 0) {
                                btns[i].click();
                                return "clicked";
                            }
                        }
                        return "not found";
                    }
                )()''', as_expr=True)
                logger.info(f"点击发简历按钮结果: {click_result}（尝试 {attempt}/{retries + 1}）")
                if click_result != "clicked":
                    logger.warning(f"发简历按钮未找到（尝试 {attempt}/{retries + 1}）")
                    time.sleep(1)
                    continue
                logger.info("已点击发简历按钮，等待简历选择弹层...")
                time.sleep(2)

                # 2. 等待弹层出现：简历选择弹层（情况A） or 上传引导（情况B）
                state = "pending"
                for wait_idx in range(15):  # 15次×0.5秒=7.5秒
                    state = self.page.run_js('''(
                        function() {
                            // 优先检测简历选择弹层 .choose-resume-dialog（实测确认）
                            var chooseDialog = document.querySelector(".choose-resume-dialog");
                            if (chooseDialog) {
                                var cs0 = window.getComputedStyle(chooseDialog);
                                if (cs0.display !== "none" && cs0.visibility !== "hidden") return "choose_resume";
                            }
                            // 备选：.dialog-wrap.active（弹层容器）
                            var wrapActive = document.querySelector(".dialog-wrap.active");
                            if (wrapActive) {
                                var csW = window.getComputedStyle(wrapActive);
                                if (csW.display !== "none" && csW.visibility !== "hidden") {
                                    // 检查是否含"请选择要发送的简历"文本
                                    if (wrapActive.textContent.indexOf("请选择要发送的简历") >= 0) return "choose_resume";
                                }
                            }
                            // 旧版确认弹层（兜底）: .panel-resume.sentence-popover
                            var exactPanel = document.querySelector(".panel-resume.sentence-popover");
                            if (exactPanel) {
                                var cs1 = window.getComputedStyle(exactPanel);
                                if (cs1.display !== "none" && cs1.visibility !== "hidden" && cs1.opacity !== "0") return "confirm";
                            }
                            // 检测上传引导（情况B：未上传简历）
                            var uploadSels = [
                                ".upload-resume-dialog",
                                ".upload-select-dialog",
                                "[class*='upload-resume']",
                                "[class*='upload-select']"
                            ];
                            for (var j = 0; j < uploadSels.length; j++) {
                                var upload = document.querySelector(uploadSels[j]);
                                if (upload) {
                                    var cs2 = window.getComputedStyle(upload);
                                    if (cs2.display !== "none" && cs2.visibility !== "hidden") return "no_resume";
                                }
                            }
                            // 检测"确定向Boss发送简历吗"文字弹层（旧版兜底）
                            var allDivs = document.querySelectorAll("div");
                            for (var k = 0; k < allDivs.length; k++) {
                                var d = allDivs[k];
                                if (d.textContent && d.textContent.indexOf("确定向") >= 0 && d.textContent.indexOf("发送简历") >= 0) {
                                    var cs3 = window.getComputedStyle(d);
                                    if (cs3.display !== "none" && cs3.visibility !== "hidden") return "confirm";
                                }
                            }
                            return "pending";
                        }
                    )()''', as_expr=True)
                    if state in ("choose_resume", "confirm", "no_resume"):
                        logger.info(f"弹层状态检测: {state}（等待 {wait_idx + 1} 次）")
                        break
                    time.sleep(0.5)

                if state == "no_resume":
                    logger.error("请先在BOSS直聘上传简历：当前账号未上传附件简历，BOSS 弹出上传引导弹层（.upload-resume-dialog 或 .upload-select-dialog），无法自动发送")
                    return False

                if state == "pending":
                    # 调试：打印页面上所有可见弹层信息
                    debug_info = self.page.run_js('''(
                        function() {
                            var info = [];
                            var candidates = document.querySelectorAll("[class*='panel'], [class*='popover'], [class*='dialog'], [class*='modal'], [class*='resume'], [class*='upload'], [class*='choose']");
                            for (var i = 0; i < Math.min(candidates.length, 20); i++) {
                                var el = candidates[i];
                                var cs = window.getComputedStyle(el);
                                if (cs.display !== "none" && cs.visibility !== "hidden") {
                                    info.push((el.className || "").toString().substring(0, 80) + " | text: " + (el.textContent || "").substring(0, 50));
                                }
                            }
                            return info.join(" || ");
                        }
                    )()''', as_expr=True)
                    logger.warning(f"弹层未出现（尝试 {attempt}/{retries + 1}），页面可见弹层: {debug_info}")
                    continue

                # 3. 根据弹层类型执行不同的发送流程
                if state == "choose_resume":
                    # 新版流程：选择简历弹层
                    # 3a. 点击简历项选中简历
                    select_result = self.page.run_js('''(
                        function() {
                            // 查找简历列表项
                            var itemSels = [
                                ".choose-resume-dialog .resume-list .list-item",
                                ".resume-list .list-item",
                                ".choose-resume-dialog .list-item",
                                ".dialog-wrap.active .resume-list .list-item"
                            ];
                            for (var i = 0; i < itemSels.length; i++) {
                                var items = document.querySelectorAll(itemSels[i]);
                                if (items.length > 0) {
                                    items[0].click();
                                    return "selected:" + itemSels[i] + " count=" + items.length;
                                }
                            }
                            return "no resume item";
                        }
                    )()''', as_expr=True)
                    logger.info(f"选中简历项: {select_result}")
                    time.sleep(1)

                    if not select_result.startswith("selected:"):
                        logger.warning(f"简历项未找到（尝试 {attempt}/{retries + 1}）: {select_result}")
                        self._close_resume_dialog()
                        time.sleep(1)
                        continue

                    # 3b. 点击发送按钮 .btn-v2.btn-sure-v2.btn-confirm
                    send_result = self.page.run_js('''(
                        function() {
                            // 优先选择 choose-resume-dialog 内的 btn-confirm（实测可用按钮）
                            var btnSels = [
                                ".choose-resume-dialog .btn-v2.btn-sure-v2.btn-confirm",
                                ".dialog-wrap.active .btn-v2.btn-sure-v2.btn-confirm",
                                ".btn-v2.btn-sure-v2.btn-confirm",
                                ".choose-resume-dialog .btn-v2.btn-sure-v2",
                                ".choose-resume-dialog button.btn-v2"
                            ];
                            for (var i = 0; i < btnSels.length; i++) {
                                var btn = document.querySelector(btnSels[i]);
                                if (!btn) continue;
                                var cs = window.getComputedStyle(btn);
                                if (cs.display === "none" || cs.visibility === "hidden") continue;
                                var cls = (btn.className || "").toString();
                                var disabled = btn.disabled || cls.indexOf("disabled") >= 0;
                                if (disabled) {
                                    return "button disabled: " + btnSels[i] + " class=" + cls;
                                }
                                btn.click();
                                return "sent:" + btnSels[i];
                            }
                            return "button not found";
                        }
                    )()''', as_expr=True)
                    logger.info(f"发送简历结果: {send_result}")
                    time.sleep(2)

                    if not send_result.startswith("sent:"):
                        logger.warning(f"发送按钮不可用（尝试 {attempt}/{retries + 1}）: {send_result}")
                        self._close_resume_dialog()
                        time.sleep(1)
                        continue

                else:
                    # 旧版流程：confirm 弹层（.panel-resume.sentence-popover）
                    # 点击确定按钮 .btn-v2.btn-sure-v2.btn-send
                    send_result = self.page.run_js('''(
                        function() {
                            var btnSels = [
                                ".btn-v2.btn-sure-v2.btn-send",
                                ".panel-resume .btn-v2.btn-sure-v2.btn-send",
                                ".panel-resume.sentence-popover .btn-v2.btn-sure-v2.btn-send",
                                ".panel-resume .btn-v2.btn-sure-v2",
                                ".panel-resume.sentence-popover .btn-v2.btn-sure-v2",
                                ".btn-sure-v2"
                            ];
                            for (var i = 0; i < btnSels.length; i++) {
                                var btn = document.querySelector(btnSels[i]);
                                if (!btn) continue;
                                var cs = window.getComputedStyle(btn);
                                if (cs.display === "none" || cs.visibility === "hidden") continue;
                                var cls = (btn.className || "").toString();
                                var disabled = btn.disabled || cls.indexOf("disabled") >= 0;
                                if (disabled) return "button disabled: " + btnSels[i];
                                btn.click();
                                return "sent:" + btnSels[i];
                            }
                            return "button not found";
                        }
                    )()''', as_expr=True)
                    logger.info(f"发送简历结果（旧版弹层）: {send_result}")
                    time.sleep(2)

                    if not send_result.startswith("sent:"):
                        logger.warning(f"发送按钮不可用（尝试 {attempt}/{retries + 1}）: {send_result}")
                        # 关闭旧版弹层
                        self.page.run_js('''(
                            function() {
                                var cancelSels = [
                                    ".panel-resume .btn-v2.btn-outline-v2",
                                    ".panel-resume.sentence-popover .btn-v2.btn-outline-v2",
                                    ".panel-resume .btn-outline-v2"
                                ];
                                for (var i = 0; i < cancelSels.length; i++) {
                                    var btn = document.querySelector(cancelSels[i]);
                                    if (btn) { btn.click(); return; }
                                }
                            }
                        )()''', as_expr=True)
                        time.sleep(1)
                        continue

                # 4. 送达验证：弹层消失 + 消息列表出现简历消息
                return self._verify_resume_sent()

            except Exception as e:
                logger.error(f"发送简历失败（尝试 {attempt}/{retries + 1}）: {e}")
                time.sleep(1)

        logger.error("发送简历最终失败")
        return False

    def _close_resume_dialog(self) -> None:
        """关闭简历选择弹层（.choose-resume-dialog）"""
        try:
            self.page.run_js('''(
                function() {
                    // 优先点击弹层右上角的关闭按钮（×）
                    var closeSels = [
                        ".choose-resume-dialog .boss-dialog__close",
                        ".choose-resume-dialog .dialog-close",
                        ".choose-resume-dialog .close",
                        ".dialog-wrap.active .boss-dialog__close",
                        ".dialog-wrap.active .close"
                    ];
                    for (var i = 0; i < closeSels.length; i++) {
                        var btn = document.querySelector(closeSels[i]);
                        if (btn) { btn.click(); return; }
                    }
                    // 备选：点击遮罩层关闭
                    var mask = document.querySelector(".dialog-wrap.active .dialog-mask, .boss-popup__mask");
                    if (mask) { mask.click(); return; }
                    // 最后备选：点击"管理附件"按钮（会跳转到简历管理页面，不理想但能关闭弹层）
                    var manage = document.querySelector(".choose-resume-dialog .manage-btn");
                    if (manage) { manage.click(); return; }
                }
            )()''', as_expr=True)
        except Exception:
            pass

    def _verify_resume_sent(self, timeout: int = 8) -> bool:
        """验证简历是否发送成功：弹层消失 + 消息列表出现简历项

        实测送达消息格式: "附件简历请求已发送" + "数据分析简历-黄维.docx点击预览附件简历"
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self.page.run_js('''(
                    function() {
                        // 检测简历选择弹层是否仍可见
                        var dialogSels = [".choose-resume-dialog", ".dialog-wrap.active", ".panel-resume.sentence-popover", ".panel-resume"];
                        var dialogVisible = false;
                        for (var i = 0; i < dialogSels.length; i++) {
                            var panel = document.querySelector(dialogSels[i]);
                            if (panel) {
                                var cs = window.getComputedStyle(panel);
                                if (cs.display !== "none" && cs.visibility !== "hidden") {
                                    dialogVisible = true;
                                    break;
                                }
                            }
                        }
                        // 检测消息列表是否出现简历消息
                        var items = document.querySelectorAll(".message-item .text-content, [class*='message-item']");
                        var count = items.length;
                        // 检查最后几条消息是否含简历相关文本
                        for (var j = Math.max(0, count - 3); j < count; j++) {
                            var txt = items[j].textContent || "";
                            if (txt.indexOf("简历") >= 0 || txt.indexOf("附件简历") >= 0 || txt.indexOf(".docx") >= 0 || txt.indexOf("简历请求已发送") >= 0) {
                                return "delivered";
                            }
                        }
                        if (dialogVisible) return "dialog visible";
                        return "pending";
                    }
                )()''', as_expr=True)
                # 只认「消息列表里出现简历条目」这一种证据。
                # 早先版本把"聊天里有任意消息"也当成功（new_message），于是任何有
                # 历史消息的会话都会立刻返回 True → mark_resume_sent() 记下已发送 →
                # resume_send_once 生效，那个会话再也收不到简历，而实际什么都没发出去。
                if result == "delivered":
                    logger.info("简历送达验证通过（消息列表出现简历条目）")
                    return True
            except Exception:
                pass
            time.sleep(1)
        logger.warning("简历送达验证超时，按失败处理")
        return False

    def check_health(self) -> str:
        """
        健康检查：检测登录态和验证码拦截。
        返回: 'ok' | 'need_login' | 'captcha' | 'unknown'

        'unknown' 表示页面读不到（标签页已关/被跳转/JS 失败）。这种情况
        不能当成 'ok'，否则风控拦截页会被误判为健康并继续自动操作。
        """
        if TEST_MODE:
            return "ok"
        try:
            url = self.page.url or ""
            probe = self.page.run_js(CAPTCHA_PROBE_JS, as_expr=True)
        except Exception:
            return "unknown"
        verdict = classify_health(url, probe)
        _is_c, why = captcha_evidence(probe)
        # 判据随行：会话侧也报"验证码"时，日志要能自证是哪条命中的
        logger.debug(f"会话页判据={verdict}（{why or '无挑战证据'}）URL={url[:90]}")
        return verdict

    def close(self):
        """关闭浏览器

        注意：如果使用 BrowserManager 共享实例，不应关闭浏览器，
        应由 BrowserManager 统一管理生命周期。
        """
        if self._browser_manager is not None:
            logger.info("使用 BrowserManager 共享实例，不关闭浏览器（由 BrowserManager 管理生命周期）")
            return
        try:
            self.browser.quit()
            logger.info("浏览器已关闭")
        except Exception:
            pass
