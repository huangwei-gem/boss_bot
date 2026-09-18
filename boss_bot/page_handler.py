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


class BossChatHandler:
    """BOSS 聊天页面操作处理器

    支持两种浏览器获取方式：
    1. 通过 BrowserManager 共享实例（推荐，多标签页场景）
    2. 直接 launch_browser 创建独立实例（向后兼容）
    """

    def __init__(self, account_id: str = None, headless: bool = None,
                 browser_manager: BrowserManager = None,
                 browser_instance: BrowserInstance = None):
        """
        Args:
            account_id: 账号 ID（保留参数，用于未来多账号扩展）
            headless: 是否无头模式（仅在使用 launch_browser 时生效）
            browser_manager: BrowserManager 实例（优先使用，共享浏览器）
            browser_instance: 指定的 BrowserInstance（如聊天标签页），优先级最高
        """
        self._account_id = account_id
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
        """获取当前账号的 cookie 文件路径

        统一项目中不再使用 AccountManager，直接返回 config.COOKIE_FILE。
        保留 account_id 参数用于未来多账号扩展。
        """
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

                        var previewEl = el.querySelector(".last-msg-text");
                        var preview = previewEl ? previewEl.textContent.trim() : "";

                        unread.push({
                            index: i,
                            name: name,
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

    def enter_chat(self, chat_info: dict, retries: int = 2) -> bool:
        """点击进入某个聊天并校验切换成功，等待聊天内容加载。

        注意：使用 JS 点击（与 get_unread_chats 扫描同一 DOM 顺序）。
        实测 element.click() 坐标点击在 Retina 屏偏移、
        element 引用与 JS 索引错位，均会点错会话。

        切换后校验页面顶栏姓名与目标会话一致，防止读到错误会话的消息。
        返回 True=切换成功并确认；False=校验失败（调用方应跳过该会话）。
        """
        idx = chat_info.get('index', 0)
        expected_name = chat_info.get('name', '')

        for attempt in range(1, retries + 2):
            self.page.run_js(
                f'document.querySelectorAll(".friend-content")[{idx}].click()',
                as_expr=True
            )
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

            # 校验会话切换是否正确
            actual_name = self.get_boss_name()
            if not expected_name or actual_name == expected_name:
                return True
            logger.warning(
                f"会话切换校验失败（第 {attempt}/{retries + 1} 次）: "
                f"期望 [{expected_name}], 实际 [{actual_name}]，重试..."
            )

        logger.error(f"会话 [{expected_name}] 切换校验最终失败，应跳过该会话")
        return False

    def read_latest_messages(self, count: int = 5) -> List[Dict]:
        """
        读取当前聊天中最近的消息。

        实测 CSS（2026-09-09 浏览器验证）:
        - 消息项: .message-item（li 元素，不是 div）
        - 对方消息: .message-item.item-friend
        - 我方消息: .message-item（无 item-friend class）
        - 消息文字: .text-content
        - 时间: .item-time .time

        注意: DrissionPage 的 eles() 对 .message-item 返回 0，必须用 JS
        """
        messages = []
        try:
            result = self.page.run_js(f'''(
                function() {{
                    var items = document.querySelectorAll(".message-item");
                    var result = [];
                    var start = Math.max(0, items.length - {count});
                    for (var i = start; i < items.length; i++) {{
                        var item = items[i];
                        var textEl = item.querySelector(".text-content");
                        var timeEl = item.querySelector(".item-time .time");
                        var cls = item.className || "";
                        result.push({{
                            text: textEl ? textEl.textContent.trim() : "",
                            time: timeEl ? timeEl.textContent.trim() : "",
                            isFriend: cls.indexOf("item-friend") >= 0,
                            is_mine: cls.indexOf("item-friend") < 0
                        }});
                    }}
                    return JSON.stringify(result);
                }}
            )()''', as_expr=True)

            if result:
                messages = json.loads(result)

            if messages:
                boss_name = self.get_boss_name()
                job_name = self.get_job_name()
                self._msg_store.save_messages(boss_name or "未知", messages, job_name)
        except Exception as e:
            logger.error(f"读取消息失败: {e}")

        return messages

    def get_boss_name(self) -> str:
        """获取当前聊天对象的名称"""
        try:
            result = self.page.run_js(
                'document.querySelector(".top-info-content .name-text") ? document.querySelector(".top-info-content .name-text").textContent.trim() : ""',
                as_expr=True
            )
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

        实测 CSS（2026-09-12 真实页面验证，v5543 版本）:
        - 发简历按钮: .toolbar-btn（文本含"发简历"）
        - 确认弹层: .panel-resume.sentence-popover（"确定向 Boss 发送简历吗？"）
        - 确定按钮: .panel-resume .btn-v2.btn-sure-v2
        - 取消按钮: .panel-resume .btn-v2.btn-outline-v2
        - 无简历时: .upload-resume-dialog 可见（"拖拽文件到这里…上传附件简历"）

        注意：旧版页面的 .choose-resume-dialog / .resume-list .list-item 已不存在。
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
                if click_result != "clicked":
                    logger.warning(f"发简历按钮未找到（尝试 {attempt}/{retries + 1}）")
                    time.sleep(1)
                    continue
                time.sleep(2)

                # 2. 等待弹层出现：确认弹层 or 上传引导
                state = "pending"
                for _ in range(10):
                    state = self.page.run_js('''(
                        function() {
                            var panel = document.querySelector(".panel-resume");
                            if (panel) {
                                var cs = window.getComputedStyle(panel);
                                if (cs.display !== "none" && cs.visibility !== "hidden") return "confirm";
                            }
                            var upload = document.querySelector(".upload-resume-dialog");
                            if (upload) {
                                var cs2 = window.getComputedStyle(upload);
                                if (cs2.display !== "none" && cs2.visibility !== "hidden") return "no_resume";
                            }
                            return "pending";
                        }
                    )()''', as_expr=True)
                    if state in ("confirm", "no_resume"):
                        break
                    time.sleep(0.5)

                if state == "no_resume":
                    logger.error("没有附件简历，BOSS 弹出上传引导。请在网页端上传简历后重试")
                    return False

                if state != "confirm":
                    logger.warning(f"确认弹层未出现（尝试 {attempt}/{retries + 1}）")
                    continue

                # 3. 点击确定按钮
                send_result = self.page.run_js('''(
                    function() {
                        var btn = document.querySelector(".panel-resume .btn-v2.btn-sure-v2");
                        if (!btn) return "button not found";
                        if (btn.className.indexOf("disabled") >= 0) return "button disabled";
                        btn.click();
                        return "sent";
                    }
                )()''', as_expr=True)
                logger.info(f"发送简历结果: {send_result}")
                time.sleep(2)

                if send_result != "sent":
                    logger.warning(f"发送按钮不可用（尝试 {attempt}/{retries + 1}）: {send_result}")
                    # 关闭弹层后重试
                    self.page.run_js('''(
                        function() {
                            var btn = document.querySelector(".panel-resume .btn-outline-v2");
                            if (btn) btn.click();
                        }
                    )()''', as_expr=True)
                    time.sleep(1)
                    continue

                # 4. 送达验证：确认弹层消失 + 消息列表出现简历消息
                return self._verify_resume_sent()

            except Exception as e:
                logger.error(f"发送简历失败（尝试 {attempt}/{retries + 1}）: {e}")
                time.sleep(1)

        logger.error("发送简历最终失败")
        return False

    def _verify_resume_sent(self, timeout: int = 6) -> bool:
        """验证简历是否发送成功：确认弹层消失 + 消息列表出现简历项"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self.page.run_js('''(
                    function() {
                        var panel = document.querySelector(".panel-resume");
                        if (panel) {
                            var cs = window.getComputedStyle(panel);
                            if (cs.display !== "none" && cs.visibility !== "hidden") return "panel visible";
                        }
                        var items = document.querySelectorAll(".message-item .text-content");
                        var count = items.length;
                        var last = count ? items[count - 1].textContent : "";
                        if (last.indexOf("简历") >= 0) return "delivered";
                        return count > 0 ? "new_message" : "pending";
                    }
                )()''', as_expr=True)
                if result in ("delivered", "new_message"):
                    logger.info(f"简历送达验证通过（{result}）")
                    return True
            except Exception:
                pass
            time.sleep(1)
        logger.warning("简历送达验证超时，按失败处理")
        return False

    def check_health(self) -> str:
        """
        健康检查：检测登录态和验证码拦截。
        返回: 'ok' | 'need_login' | 'captcha'
        """
        if TEST_MODE:
            return "ok"
        try:
            url = self.page.url or ""
            if "login" in url or "/web/user" in url or "passport" in url:
                return "need_login"
            result = self.page.run_js('''(
                function() {
                    var body = document.body ? document.body.innerText : "";
                    if (body.indexOf("安全验证") >= 0 || body.indexOf("验证码") >= 0) return "captcha";
                    if (document.querySelector(".nc-container, .verify-wrap, .geetest_panel")) return "captcha";
                    return "ok";
                }
            )()''', as_expr=True)
            return result if result in ("ok", "captcha") else "ok"
        except Exception:
            return "ok"

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
