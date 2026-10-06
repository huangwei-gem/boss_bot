"""
自动打招呼/投递引擎

从 auto_boss 项目迁移，负责在 BOSS 直聘上自动搜索职位、
发送打招呼消息和投递简历。

核心功能：
- 岗位搜索（关键词 + 城市 + 滚动翻页）
- 岗位浏览和打招呼（自动点击沟通按钮、输入消息、发送）
- AI 匹配分析（多 AI 容灾链，岗位匹配度评分）
- Cookie 管理（保存/加载/清除）
- 反爬策略（随机间隔、User-Agent 轮换）
- 去重管理（持久化已投递记录）
- 重试与容错（指数退避重试装饰器）
- 城市编码映射（硬编码 + API 捕获）
"""

import json
import os
import re
import random
import time
import threading
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
from urllib.request import Request, urlopen
from urllib.error import URLError

from boss_bot.unified_config import DEFAULT_GREETING, UnifiedConfig, BASE_DIR, resolve_path, write_json_atomic
from boss_bot.greeting import (account_greeting_mode, effective_account_greeting,
                               sanitize_ai_greeting)
from boss_bot.unified_config import strip_default_greeting
from boss_bot.browser_launcher import BrowserManager
from boss_bot.reply_record import (GreetRecord, classify_greet_skip,
                                   _get_greet_store)

# ─────────────────────────────────────────────
# 路径常量
# ─────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHATTED_DB_FILE = DATA_DIR / "chatted_jobs.json"
CITY_DICT_FILE = DATA_DIR / "city_dict.json"
AI_CACHE_FILE = DATA_DIR / "ai_cache.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# 找不到聊天输入框时的页面快照与归因
# ─────────────────────────────────────────────
# 历史 172 条跳过记录都是同一句"未找到输入框"，把 logs 里 116 次的现场逐条
# 分类后其实是三件不同的事：61 次页面被换成手机+短信验证码登录框（登录态掉了）、
# 45 次沟通抽屉压根没在页内渲染、8 次标签页连接已断。处置方式完全不同，
# 所以失败原因必须按现场分档写出来。
#
# 快照必须连 contenteditable 一起查：BOSS 的聊天输入框是
# #chat-input.chat-input 这个 contenteditable div，旧 dump 只抓 input/textarea，
# 于是"页面上没输入框"这个结论本身可能是瞎的。
CHAT_SNAPSHOT_JS = r'''
return (function(){
  function cls(el){
    var c = el.className;
    if (typeof c === 'string' && c.trim()) return c.trim();
    return (el.getAttribute && el.getAttribute('type')) || '';
  }
  // 页面上真看得见才算证据：BOSS 的岗位详情页常驻一份隐藏的注册/登录抽屉模板，
  // 只看"类名在不在 DOM 里"会把登录态正常的号判成"要求重新登录"
  function vis(el){
    if (!el.getClientRects || !el.getClientRects().length) return false;   // display:none（含祖先）
    var s = window.getComputedStyle(el);
    return s.visibility !== 'hidden' && s.opacity !== '0';
  }
  var inputs = [];
  var nodes = document.querySelectorAll('input, textarea');
  for (var i = 0; i < nodes.length && inputs.length < 30; i++) {
    var v = cls(nodes[i]);
    if (v) inputs.push({cls: v, visible: vis(nodes[i])});
  }
  var sels = ['#chat-input', '.chat-input', '[contenteditable="true"]', '.input-area',
              '.chat-container', '.chat-popup', '.drawer', '.modal-content', '.send-message'];
  var found = [];
  for (var j = 0; j < sels.length; j++) {
    var e = document.querySelector(sels[j]);
    if (e && vis(e)) found.push(sels[j]);
  }
  var btns = [];
  var bs = document.querySelectorAll('button, a.btn, div.btn, [role="button"]');
  for (var k = 0; k < bs.length && btns.length < 8; k++) {
    var b = bs[k], bt = (b.innerText || "").trim().slice(0, 16);
    if (bt) btns.push(bt + "|" + cls(b).slice(0, 30));
  }
  var notice = "";
  var ns = document.querySelectorAll('.job-expired, .notice-text, .empty-job, '
                                      + '.job-disabled, .tips, .common-error, .resume-tip');
  for (var n = 0; n < ns.length; n++) {
    var t = (ns[n].innerText || "").trim();
    if (t) { notice = t.slice(0, 60); break; }
  }
  // 浮层提示：BOSS 说"今日沟通次数已用完"这类话只出现在 toast 里，
  // 不抓下来，日志就只剩一句"抽屉没出现"，谁也分不清是额度、风控还是页面卡住。
  // 选择器要收窄：第一版写了 [class*="limit"] / [class*="tip-txt"] 这类宽匹配，
  // 结果抓到过顶导航"南通招聘"，等于给原因串塞进一句无关的话。
  var toast = "";
  var ts = document.querySelectorAll('[class*="toast"], [role="alert"], [class*="dialog-msg"], '
                                      + '.chat-limit-tips, .limit-tips');
  for (var q = 0; q < ts.length; q++) {
    var el = ts[q], tt = (el.innerText || "").trim();
    if (!tt || tt.length > 40 || !vis(el)) continue;
    var pos = getComputedStyle(el).position;
    if (pos !== "fixed" && pos !== "absolute") continue;
    toast = tt; break;
  }
  return JSON.stringify({url: location.href, inputs: inputs, chat_elements: found,
                         buttons: btns, notice: notice, toast: toast});
})();
'''

# 快照里"确实能打字发消息"的那几个元素，与只能证明抽屉存在的那些
# ─────────────────────────────────────────────
# 找不到「沟通按钮」时的归因
# ─────────────────────────────────────────────
# logs/greet_engine.log 里 20 次 pre-click 命中的"导航到 / 导航后URL"逐条比对，
# 同一句"未找到沟通按钮"其实是三件不同的事：9 次落地在 /web/geek/chat（岗位此前
# 已沟通，BOSS 把详情页直接跳成会话页）、9 次停在同一岗位页但按钮没渲染、
# 2 次被重定向到另一个职位（原岗位已下线）。处置方式不同，原因必须分开写。
CHAT_REDIRECT_REASON = "该岗位此前已沟通：BOSS 把详情页直接跳成了会话页"
OFFLINE_JOB_REASON = "岗位已下线：BOSS 把详情页重定向到了另一个职位"
GREETING_MISSING_REASON = ("未配置招呼语：这个账号没写话术、岗位里也没有，"
                           "而按本账号信息（城市/方向/简历画像）也拼不出默认话术")
LOGIN_WALL_REASON = "BOSS 要求重新登录（页面出现手机号+短信验证码框），登录态已失效"
CAPTCHA_REASON = "BOSS 弹出人机验证，需要人工在浏览器窗口完成（超时会自动跳过）"
DISCONNECTED_REASON = "聊天页与浏览器连接已断开（标签页被关或被别的线程抢走）"
# 点了沟通既没抽屉也没新标签页：BOSS 把当日沟通额度用完时就是这个表现
# （实测账号2 今天投到 118 单后从 14:32 起 36 连败，全在这一条上）
NO_DRAWER_REASON = "点了「立即沟通」但聊天抽屉没在这个标签页里出现（URL 仍停在岗位详情页）"
NO_DRAWER_STREAK_LIMIT = 3
NO_DRAWER_COOLDOWN_SEC = 30 * 60


_INPUTISH = ("#chat-input", ".chat-input", '[contenteditable="true"]', ".input-area")
_LOGIN_CLS = ("ipt-phone", "ipt-sms")


def _visible_inputs(snap):
    """快照里的输入框类名，只算页面上真看得见的。

    BOSS 的岗位详情页常驻一份隐藏的注册/登录抽屉模板，ipt-phone / ipt-sms
    就挂在里面 —— 只看"类名在不在 DOM 里"，登录态好好的也会被判成"要求重新登录"
    （2026-10-04 一天 44 次误判，取证见 tests/test_greet_failure_reason.py）。
    老快照传的是裸类名、没有可见性字段，那种按原样算，不放过真墙。
    """
    out = []
    for c in (snap.get("inputs") or []):
        if isinstance(c, dict):
            if not c.get("visible"):
                continue
            c = c.get("cls")
        out.append(str(c or "").lower())
    return out


def chat_failure_reason(snap):
    """把失败瞬间的页面快照归成一句能照着修的原因。"""
    snap = snap or {}
    err = str(snap.get("error") or "")
    low = err.lower()
    if "断开" in err or "disconnect" in low or "connection" in low or "refused" in low:
        return DISCONNECTED_REASON

    inputs = _visible_inputs(snap)
    url = str(snap.get("url") or "")
    # 只认页面证据：BOSS 那个静默风控参数会挂在地址上，而带着它落地的那一页
    # 照样读到 JD、点中"立即沟通"、把招呼语发出去（判据的取证见 page_handler
    # 的 CAPTCHA_BOX_SELECTORS 上方注释）。拿地址定罪就会出现
    # "后端说验证码、页面上没影"，而且那参数不会自己消失，闸门永远等不到恢复。
    if snap.get("captcha"):
        return CAPTCHA_REASON
    if any(any(k in c for k in _LOGIN_CLS) for c in inputs):
        return LOGIN_WALL_REASON
    if "/web/user" in url:
        return "BOSS 要求重新登录（页面被送到登录页 %s），登录态已失效" % url[:50]

    chat = list(snap.get("chat_elements") or [])
    # 页面上飘过的提示一起带进原因串：光说"抽屉没出现"分不清是额度、风控还是卡页面
    tip = str(snap.get("toast") or snap.get("notice") or "").strip()
    suffix = ("；页面提示：" + tip) if tip else ""
    if chat and not any(c in _INPUTISH for c in chat):
        return "聊天抽屉容器已出现但输入框没渲染（页面卡在半成品状态）"
    if not chat:
        if "job_detail" in url:
            return NO_DRAWER_REASON + suffix
        return "页面已跳走且没有聊天元素（当前 URL: %s）%s" % (url[:60] or "未知", suffix)
    return "未找到聊天输入框，页面上有抽屉相关元素: %s" % ", ".join(chat[:4])


# BOSS 的第二种打招呼机制：点「立即沟通」后平台自己把招呼语发出去了，
# 弹一个「已向BOSS发送消息 / 留在此页 / 继续沟通」的对话框，没有可输入的抽屉。
AUTO_GREET_PROBE_JS = r'''
return (function(){
  var nodes = document.querySelectorAll("div,section");
  var best = null;
  for (var i = 0; i < nodes.length; i++) {
    var e = nodes[i], tx = e.innerText || "";
    if (tx.indexOf("已向BOSS发送") >= 0 && tx.indexOf("留在此页") >= 0) {
      if (!best || e.outerHTML.length < best.outerHTML.length) best = e;
    }
  }
  if (!best) return "";
  var btns = [];
  var bs = best.querySelectorAll("button, a, .btn, [role=button]");
  for (var j = 0; j < bs.length; j++) {
    btns.push(((bs[j].innerText || "").trim().slice(0, 20)) + "|" + (bs[j].className || ""));
  }
  return JSON.stringify({text: (best.innerText || "").slice(0, 200),
                         buttons: btns.slice(0, 8), cls: (best.className || "").slice(0, 120)});
})()'''

def parse_auto_greet_dialog(raw) -> dict:
    """探针返回的字符串 → 弹窗现场；不是弹窗就返回空 dict。"""
    if not raw or not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) and data.get("text") else {}


# 会话里我方最后一条已发出的气泡。自动发送那条到底发了什么，只能这样读出来，
# 猜"BOSS 应该发的就是我这句"会把重复发送和漏发都藏起来。
# 我方气泡的 class 是从 tools/chat_page_structure.json 的真实 dump 里核出来的
# （message-item item-myself + .text-content，对侧是 item-friend）。
LAST_MINE_BUBBLE_JS = r'''
return (function(){
  var all = document.querySelectorAll('.message-item');
  var mine = document.querySelectorAll('.message-item.item-myself');
  var text = '';
  if (mine.length) {
    var last = mine[mine.length - 1];
    var t = last.querySelector('.text-content') || last;
    text = (t.innerText || t.textContent || '').trim();
  }
  return JSON.stringify({chat_page: all.length > 0, total: all.length,
                         mine: mine.length, text: text.slice(0, 400)});
})()'''


def norm_greeting(text) -> str:
    return " ".join(str(text or "").split())


def auto_greet_matches(boss_sent, ours) -> bool:
    """BOSS 替我们发出去的那条，是不是就是本号要发的招呼语。

    只按空白折叠后比：气泡里会多出渲染用的空格换行，逐字符比会把"已经是我们
    这句"误判成不一致，于是对同一个 HR 发两遍。我方文案为空时必须返回 False，
    不然平台的预设文案会被当成本号自定义的那段而不再补发。
    """
    ours_n = norm_greeting(ours)
    return bool(ours_n) and norm_greeting(boss_sent) == ours_n


def pick_continue_btn(dialog) -> str:
    """弹窗里该点的是「继续沟通」；「留在此页」是什么都不做，不能点。"""
    for item in (dialog or {}).get("buttons") or []:
        text = str(item).split("|")[0].strip()
        if SELECTOR_START_CHAT_CONTINUE in text:
            return SELECTOR_START_CHAT_CONTINUE
    return ""


# 点完「立即沟通」只有三种结局：抽屉弹出来了、平台自己发了（弹窗）、什么都没发生。
# 原来的写法是把十个输入框选择器 each timeout=3 轮着试三轮，什么都没发生时要在
# 空等上花掉 3.5 分钟——而成功的岗位整条路只要 60~70 秒。改成先一次 JS 问清
# 楚结局，探测不到再退回原来的慢路径兜底。
DRAWER_READY_TIMEOUT_SEC = 20.0
DRAWER_READY_POLL_SEC = 1.5
# 点了「继续沟通」之后等会话渲染。留 4 次×2 秒：抽屉真出现时够读到气泡，
# 读不到就是它开在回复引擎那个标签页里（实测绝大多数如此），再多等只是空耗投递时间
CHAT_OPEN_POLLS = 4
CHAT_OPEN_POLL_SEC = 2.0
DRAWER_READY_JS = r'''
return (function(){
  var sel = ['#chat-input', '.chat-input', '.input-area',
             'textarea[placeholder*="回复"]', 'textarea[placeholder*="输入"]',
             '[contenteditable="true"]'];
  var drawer = false;
  for (var i = 0; i < sel.length; i++) {
    var nodes = document.querySelectorAll(sel[i]);
    for (var k = 0; k < nodes.length; k++) {
      var e = nodes[k], r = e.getBoundingClientRect(), s = getComputedStyle(e);
      if (r.width > 4 && r.height > 4 && s.display !== 'none' && s.visibility !== 'hidden') {
        drawer = true; break;
      }
    }
    if (drawer) break;
  }
  var dialog = false;
  if (!drawer) {
    var boxes = document.querySelectorAll("div,section");
    for (var j = 0; j < boxes.length; j++) {
      var tx = boxes[j].innerText || "";
      if (tx.indexOf("已向BOSS发送") >= 0 && tx.indexOf("留在此页") >= 0) { dialog = true; break; }
    }
  }
  return JSON.stringify({drawer: drawer, dialog: dialog});
})()'''


def parse_drawer_probe(raw) -> dict:
    """探测 JS 的返回值 → {"drawer": bool, "dialog": bool}；拿不到就当没发生。"""
    if not raw or not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {"drawer": bool(data.get("drawer")), "dialog": bool(data.get("dialog"))}


def input_lookup_attempts(probe_ready, configured_max) -> int:
    """探针已经说过"抽屉没来"时，兜底扫描只做一遍，不再乘重试配置。

    实测 10-06 两个号投满当日额度后，一单的时间线是
    11:59:15 点击 → 12:00:06 弹窗容器没有 → 12:00:51 扫 iframe
    → 12:01:51 遍历标签页 → 12:02:21 重试 2/3 → 12:03:57 又扫一遍，
    一单 250 秒，而探针 20 秒内就给了同样的结论。留一遍是给探针看不见的
    iframe / 新标签页兜底，BOSS 哪天换了弹窗形态也不至于突然全认不出来。
    """
    if not probe_ready:
        return 1
    return max(1, int(configured_max or 1))


# 补发时找输入框/发送按钮的选择器，与正常路径同源（会话输入框是 contenteditable）
AUTO_GREET_INPUT_SELECTORS = (
    "#chat-input", ".chat-input", ".input-area",
    'textarea[placeholder*="回复"]', 'textarea[placeholder*="输入"]',
    "[contenteditable=true]", 'div[contenteditable="true"]',
)
AUTO_GREET_SEND_SELECTORS = (
    ".btn-send", ".btn-v2.btn-sure-v2.btn-send", ".send-message", ".chat-send",
)




def job_id_of(url: str) -> str:
    m = re.search(r"job_detail/([0-9a-zA-Z]+)", url or "")
    return m.group(1) if m else ""


def same_job_page(requested: str, landed: str) -> bool:
    """落地页是不是就是请求的那个岗位：BOSS 会在 URL 后面加 ?lid= 等参数。"""
    rid = job_id_of(requested)
    return bool(rid) and rid == job_id_of(landed)


def chat_button_failure_reason(requested_url: str, landed_url: str, snap: dict):
    """按落地页与现场判"没有沟通按钮"到底是哪种情况。

    返回 (原因, 是否该标为已沟通)。跳会话页那类必须标已沟通，否则下一轮
    搜索还会同一个岗位再撞一次。
    """
    snap = snap or {}
    err = str(snap.get("error") or "")
    low = err.lower()
    if "断开" in err or "disconnect" in low or "connection" in low or "refused" in low:
        return DISCONNECTED_REASON, False

    landed = landed_url or str(snap.get("url") or "")
    if "/geek/chat" in landed or "/web/geek/chat" in landed:
        return CHAT_REDIRECT_REASON, True
    if any(k in landed for k in ("login", "passport", "/web/user")):
        return LOGIN_WALL_REASON, False
    if snap.get("captcha"):
        return CAPTCHA_REASON, False
    if "job_detail" in landed and not same_job_page(requested_url, landed):
        return OFFLINE_JOB_REASON, False

    inputs = _visible_inputs(snap)
    if any(any(k in c for k in _LOGIN_CLS) for c in inputs):
        return LOGIN_WALL_REASON, False

    detail = []
    notice = str(snap.get("notice") or "")
    if notice:
        detail.append("页面提示: " + notice)
    buttons = snap.get("buttons") or []
    if buttons:
        detail.append("页面上的按钮: " + ", ".join(str(b) for b in buttons[:4]))
    tail = ("；" + "；".join(detail)) if detail else "；页面上一个可见按钮都没抓到"
    return f"岗位页没有可点的沟通按钮（详情页已打开但按钮没渲染）{tail}", False


def pick_greeting(job_text: str, account_text: str, default_text: str, ai_text: str = ""):
    """这条招呼语用哪一段：岗位手写 > AI 按岗位定制 > 账号（默认或自写）> 没配置。

    岗位文案只有在被改过（不等于默认串）时才算定制——历史配置里每个岗位的
    greeting 都被填过同一份默认文案，一律优先会让账号级自定义永远不生效。

    AI 那一档是判分时按岗位名+公司+JD 现编的 suggested_greeting，调用前必须已经
    过 sanitize_ai_greeting；没过校验就是空串，落到账号那档，绝不"将就发一条"。

    account_text 由 greeting.effective_account_greeting 给出：账号自己没写时它是
    按这个账号的城市/方向/技能生成的默认话术（2026-10-03 口径）。以前"留空即不发"
    让两个号都没写那句话时一整轮 218 条全成「未配置招呼语，跳过」，
    用户看到的是"日志一片跳过、记录对不上"。
    """
    if job_text and job_text != default_text:
        return job_text, "岗位配置"
    if (ai_text or "").strip():
        return ai_text.strip(), "AI 按岗位定制"
    if (account_text or "").strip():
        return account_text.strip(), "账号自定义"
    return "", "未配置"


def account_greeting_ready(acc, resume=None, profile=None) -> bool:
    """这个账号跑一轮，能不能真发出去至少一条招呼语。

    2026-10-03 口径变了：账号没自己写招呼语时不再是"一条都不发"，而是按这个账号
    自己的城市/方向/技能先生成一条默认（界面可见可改），发送时再由 AI 按岗位现编。
    所以能不能发只剩一个前提——至少有一个启用中的岗位。
    """
    if acc is None:
        return False
    jobs = list(getattr(acc, "jobs", None) or [])
    if not any(getattr(j, "enabled", True) for j in jobs):
        return False
    return bool(effective_account_greeting(acc, resume, profile).strip())


# account_greeting_mode 直接从 boss_bot.greeting 导入（见文件头）：落进配置的默认
# 文本要还能被认成"系统生成的"，判据只留一份，别在这里再抄一遍简化版


# ─────────────────────────────────────────────
# 文件日志
# ─────────────────────────────────────────────
_file_handler = logging.FileHandler(str(LOG_DIR / "greet_engine.log"), encoding="utf-8")
# 时间必须带日期：只有 HH:MM:SS 时跨天的日志分不开，事后归因会把不同天的
# 同一现象当成一次（这次查"未找到输入框"就被这个坑过一次）
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s",
                                             datefmt="%Y-%m-%d %H:%M:%S"))
_file_logger = logging.getLogger("greet_engine_file")
_file_logger.setLevel(logging.INFO)
_file_logger.addHandler(_file_handler)
_file_logger.propagate = False

# ─────────────────────────────────────────────
# CSS 选择器常量
# ─────────────────────────────────────────────
SELECTOR_NAV = ".user-nav"
SELECTOR_START_CHAT_TEXT = "立即沟通"
SELECTOR_START_CHAT_CONTINUE = "继续沟通"
SELECTOR_INPUT_AREA = ".input-area"
SELECTOR_SEND_BTN = ".send-message"
SELECTOR_CLOSE = ".icon-close"
SELECTOR_BOSS_ACTIVE = ".boss-active-time"
SELECTOR_SCALE = ".icon-scale"
SELECTOR_REC_JOB_LIST = ".rec-job-list"
SELECTOR_JOB_NAME = ".job-name"

# ─────────────────────────────────────────────
# 默认 User-Agent 列表
# ─────────────────────────────────────────────
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ─────────────────────────────────────────────
# 热门城市编码映射（Boss直聘使用）
# ─────────────────────────────────────────────
CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
    "南京": "101190100", "武汉": "101200100", "西安": "101110100",
    "重庆": "101040100", "长沙": "101250100", "苏州": "101190400",
    "天津": "101030100", "郑州": "101180100", "东莞": "101281600",
    "青岛": "101120200", "沈阳": "101070100", "宁波": "101210400",
    "昆明": "101290100", "大连": "101070200", "厦门": "101230200",
    "合肥": "101220100", "佛山": "101280300", "福州": "101230100",
    "哈尔滨": "101050100", "济南": "101120100", "温州": "101210700",
    "长春": "101060100", "石家庄": "101090100", "常州": "101191100",
    "泉州": "101230500", "南宁": "101300100", "贵阳": "101260100",
    "南昌": "101240100", "太原": "101100100", "烟台": "101120500",
    "嘉兴": "101210300", "南通": "101190500", "金华": "101210900",
    "珠海": "101280700", "惠州": "101280300", "徐州": "101190800",
    "海口": "101310100", "乌鲁木齐": "101130100", "绍兴": "101210500",
    "中山": "101281700", "台州": "101210600", "兰州": "101160100",
    # 城市弹层里点"全国"实测落到 city=100010000（结果立刻跨省份）
    "全国": "100010000",
}

# 求职类型 → BOSS 的 jobType 参数（2026-10-03 在搜索页逐个点出来读 URL 实测）
JOB_TYPE_CODES = {"全职": "1901", "实习": "1902", "兼职": "1903"}


# ─────────────────────────────────────────────
# AI 分析器（多 AI 容灾链）
# ─────────────────────────────────────────────

class AIProviderConfig:
    """单个 AI 接口配置。"""

    def __init__(self, name: str, api_key: str, api_base: str, model: str, timeout: int = 30):
        self.name = name
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_valid(self) -> bool:
        return bool(self.api_key and self.api_base and self.model)


class AIResponseUnusable(Exception):
    """接口回了话，但没回可信判分（空正文/截断/没有 JSON/没有 score）。

    必须走异常而不是返回兜底 dict：兜底 dict 会被容灾链当成"这个接口成功了"，
    于是既不换下一个接口，又把坏接口回报成健康 —— AI 筛岗静默失效。
    """


# 整个进程共享的接口冷却表：provider.name -> 恢复时间戳。
# 两个号各自一份的时候（2026-09-29），主号已经把某家服务商打到 429，账号2 毫不知情
# 接着再撞，一次限流被踩成两次，还把容灾链里能用的接口一起烧进冷却。
_SHARED_COOLDOWN: dict = {}
_COOLDOWN_LOCK = threading.Lock()


def reset_shared_cooldown() -> None:
    """清空冷却表（给测试用，也给"改了 AI 配置后立即重测"用）。"""
    with _COOLDOWN_LOCK:
        _SHARED_COOLDOWN.clear()


# ── 判分复盘：AI 说"不符合"之后追问它到底卡在哪一条 ──
# 口径见 docs/superpowers/specs/2026-09-29-reject-reason-followup-design.md：
# 只问 AI 不问 HR、只追边界带、每号每轮限量、原因只出建议。
PROBE_BAND_DEFAULT = 15      # 阈值下方多宽算"差一点就过"
PROBE_LIMIT_DEFAULT = 5      # 每号每轮最多追问几条（一次追问≈一次判分，中位 5.4s）
PROBE_FIELD_MAX_LEN = 200    # 四段各自限长，别把 2MB 的记录文件继续撑大

_TRUE_WORDS = ("true", "yes", "y", "1", "是", "对", "可以", "能")
_FALSE_WORDS = ("false", "no", "n", "0", "否", "不", "没")


def in_probe_band(score, threshold: int, band: int = PROBE_BAND_DEFAULT) -> bool:
    """分数是否落在"差一点就过"的边界带：[阈值-band, 阈值)。

    上界是开区间——等于阈值本来就该打招呼，追问它是把预算花在必过的岗位上。
    """
    try:
        s = int(score)
        t = int(threshold)
    except (TypeError, ValueError):
        return False
    return t - band <= s < t


def select_probe_targets(items, threshold: int, band: int = PROBE_BAND_DEFAULT,
                         limit: int = PROBE_LIMIT_DEFAULT) -> list:
    """挑本轮真正要追问的几条：只取带内，并按分数从高到低截断。

    一轮里带内常有 20-30 条而配额只有 5：68 分是"补一句证据就能翻盘"，
    55 分附近多是外包/城市/学历明显不符，问出来的原因没有可操作性。
    """
    in_band = [it for it in items
               if in_probe_band(it.get("score"), threshold, band)]
    in_band.sort(key=lambda it: int(it.get("score") or 0), reverse=True)
    return in_band[:max(0, int(limit))]


def _as_bool(value) -> bool:
    """模型给的是 true/"是"/1 都说得通，认不出来就当 False。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    if any(text.startswith(w) for w in _TRUE_WORDS):
        return True
    if any(text.startswith(w) for w in _FALSE_WORDS):
        return False
    return False


def _clamp_int(value, lo: int = 0, hi: int = 100) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(lo, min(hi, n))


class AIAnalyzerChain:
    """多 AI 容灾链：按顺序尝试多个 AI 接口，自动切换。

    容灾链可能有二十几个接口，每个 30s 超时。不加约束的话，一个岗位最坏要
    串行等完全部接口（实测每个岗位 7~8 分钟），打招呼线程看起来就像卡死。
    因此这里限制单岗位的尝试数量与总耗时，并对连续失败的接口做冷却。
    """

    _cache_lock = threading.Lock()

    # 单个岗位最多尝试多少个接口、最多花多少秒
    MAX_ATTEMPTS_PER_JOB = 4
    JOB_BUDGET_SECONDS = 60
    # 带 thinking 的接口实测 1024 token 会被思考吃光（正文为空 + finish=length），
    # 默认给到 1600；真被截断时换接口，不再原地"默认通过"。
    DEFAULT_ANALYZE_MAX_TOKENS = 1600
    # 接口失败后的冷却时间（秒）：鉴权/额度类错误冷却更久
    COOLDOWN_AUTH_SECONDS = 1800
    COOLDOWN_OTHER_SECONDS = 300
    # 429 是"你打得太快"，不是接口坏了：按 5 分钟冷却会把本来一分钟就能用的
    # 接口整天剔掉（2026-09-29 两个号并发 5 分钟打出 33 次 429 就是这么烧的）
    COOLDOWN_RATE_SECONDS = 60

    def __init__(
        self,
        providers: list,
        match_threshold: int = 70,
        cache_enabled: bool = True,
        cache_ttl_hours: int = 24,
        log_callback: Optional[Callable] = None,
        custom_filter_keywords: list = None,
        custom_scoring_prompt: str = "",
        skip_unhealthy: bool = True,
        analyze_max_tokens: int = 0,
        fail_action: str = "default",
        veto_only_match: bool = False,
    ):
        self.providers = []
        for p in providers:
            if isinstance(p, dict):
                self.providers.append(AIProviderConfig(
                    name=p.get("name", "AI"),
                    api_key=p.get("api_key", p.get("key", "")),
                    api_base=p.get("api_base", p.get("url", "")),
                    model=p.get("model", ""),
                    timeout=p.get("timeout", 30),
                ))
            elif isinstance(p, AIProviderConfig):
                self.providers.append(p)
            elif hasattr(p, "name") and hasattr(p, "api_key"):
                # duck typing: 支持 AIProvider dataclass 或任何具有相同属性的对象
                self.providers.append(AIProviderConfig(
                    name=getattr(p, "name", "AI"),
                    api_key=getattr(p, "api_key", ""),
                    api_base=getattr(p, "api_base", ""),
                    model=getattr(p, "model", ""),
                    timeout=getattr(p, "timeout", 30),
                ))

        self.match_threshold = match_threshold
        self.cache_enabled = cache_enabled
        self.cache_ttl = cache_ttl_hours * 3600
        self.log_cb = log_callback
        self.custom_filter_keywords = custom_filter_keywords or []
        self.custom_scoring_prompt = custom_scoring_prompt or ""
        self.analyze_max_tokens = analyze_max_tokens or self.DEFAULT_ANALYZE_MAX_TOKENS
        # AI 全军覆没时的处置：default = 按"默认通过"继续投（盲投），
        # skip = 这一轮不投。以前判分链写死 default，配置里那个开关只对回复生效，
        # 于是接口全 429 的一轮会 score 50 一路放行，用户看到的是"没筛过却投了几十个"
        self.fail_action = fail_action if fail_action in ("skip", "default") else "default"
        # 只看否决词：AI 的分和结论不参与放行，只有命中自定义筛选词才拦
        self.veto_only_match = bool(veto_only_match)

        self.analyzed_count = 0
        self.match_count = 0
        self.cache_hit_count = 0
        # 没能真判分、落到"默认通过"的岗位数 —— 面板用它回答"AI 到底筛没筛"
        self.fallback_count = 0
        self._resume = None
        self._resume_hash = ""

        # 追踪最后一次分析的信息（供 GreetRecord 记录使用）
        self.last_system_prompt = None
        self.last_user_prompt = None
        self.last_model_name = ""
        # 判出上一条结果的接口——追问要回到同一个接口问，换接口问出来的是
        # "另一个人怎么判"，不是"你刚才为什么这么判"
        self.last_provider = None
        self.last_raw_response = None
        # 失败接口冷却表：provider.name -> 恢复时间戳
        self._cooldown_until: dict = _SHARED_COOLDOWN   # 跨账号共享，见模块注释
        # 是否按体检结果跳过已知不可用的接口
        self._skip_unhealthy = skip_unhealthy
        self._unhealthy_cache = None

    HEALTH_STALE_SECONDS = 24 * 3600

    def _unhealthy_names(self) -> set:
        """体检在 24h 内明确标记"不可用"的接口名（按文件版本缓存，避免每岗位读盘）。

        容灾链的相对顺序不变，只是不再把 30s 预算浪费在已知打不通的接口上；
        全部接口都被标记不可用时不生效（宁可慢，也不能完全不筛岗位）。

        缓存按体检文件的 mtime+大小失效，不按时间：两个号是两条独立的容灾链，
        按 5 分钟时间缓存时账号1 刚体检完"25/27 可用"，账号2 仍拿着旧清单
        报"跳过 10 个已知不可用"，日志和界面就对不上。
        """
        names: set = set()
        try:
            from boss_bot.ai_health import (HEALTH_FILE, STATUS_UNAVAILABLE,
                                            provider_key, load_health)
            try:
                stat = Path(HEALTH_FILE).stat()
                version = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                version = None
            if version is not None and self._unhealthy_cache and \
                    self._unhealthy_cache[0] == version:
                return self._unhealthy_cache[1]
            data = load_health()
            updated = data.get("updated_at") or ""
            try:
                from datetime import datetime as _dt
                age = time.time() - _dt.strptime(updated, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                age = self.HEALTH_STALE_SECONDS + 1  # 没有时间戳就当作过期
            results = data.get("results") or {}
            if age <= self.HEALTH_STALE_SECONDS and results:
                for p in self.providers:
                    entry = {"api_base": p.api_base, "model": p.model, "name": p.name}
                    hit = results.get(provider_key(entry)) or results.get(
                        provider_key(dict(entry, api_base=entry["api_base"] + "/")))
                    if hit and hit.get("status") == STATUS_UNAVAILABLE:
                        names.add(p.name)
            self._unhealthy_cache = (version, names)
        except Exception as e:
            self._log("DEBUG", f"读取 AI 体检结果失败，本次不跳过任何接口: {e}")
            self._unhealthy_cache = (None, set())
            names = set()
        return names

    def _cool_down(self, provider, error: Exception):
        """把失败的接口临时拉黑，避免每个岗位都重踩同一个坑。

        401/403/404/额度类错误短期内不会自己恢复，冷却时间长一些。
        """
        text = str(error).lower()
        permanent = any(k in text for k in
                        ("401", "403", "404", "free", "quota", "insufficient",
                         "unauthorized", "invalid_api_key", "not found"))
        throttled = any(k in text for k in ("429", "too many requests", "rate limit"))
        if permanent:
            seconds = self.COOLDOWN_AUTH_SECONDS
        elif throttled:
            seconds = self.COOLDOWN_RATE_SECONDS
        else:
            seconds = self.COOLDOWN_OTHER_SECONDS
        with _COOLDOWN_LOCK:
            self._cooldown_until[provider.name] = time.time() + seconds
        self._log("WARN", f"接口 [{provider.name}] 冷却 {seconds // 60} 分钟")

    def _report_health(self, provider, ok: bool, error: str = ""):
        """把真实调用的成败回写体检表（ping 得通、真提示词超时的接口靠这个揪出来）。"""
        try:
            from boss_bot.ai_health import report_runtime_result
            report_runtime_result(provider, ok, error)
            if not ok:
                self._unhealthy_cache = None   # 下一个岗位就按新结果跳过，不用等 5 分钟
        except Exception as e:
            self._log("DEBUG", f"回写 AI 体检结果失败: {e}")

    def _log(self, level: str, msg: str):
        if self.log_cb:
            self.log_cb(f"[AI] [{level}] {msg}")

    def set_resume(self, resume: dict):
        self._resume = resume
        self._resume_hash = hashlib.md5(
            json.dumps(resume, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _veto_keyword_hit(self, job: dict) -> str:
        """自定义筛选词直接在岗位文本里查，返回命中的那条（没命中返回空串）。

        这些词原来只写进提示词，靠模型自己填 veto_hit。开了"只看否决词"之后
        它是唯一拦人的依据，模型不填就等于什么都拦不住，所以代码再查一遍。
        """
        if not self.custom_filter_keywords:
            return ""
        text = "|".join(str(job.get(k) or "") for k in
                        ("job_name", "description", "requirements", "company"))
        for kw in self.custom_filter_keywords:
            word = str(kw or "").strip()
            if word and word in text:
                return word
        return ""

    def analyze_job(self, job: dict) -> dict:
        """分析单个岗位。依次尝试所有 provider，直到成功。

        AI 完全不可用时返回带 `ai_error` 标记的结果，调用方据此区分
        「AI 说这个岗位不匹配」和「AI 没给出判断」——两者的处理方式相反。
        """
        hit = self._veto_keyword_hit(job)
        if hit:
            # 命中否决词就不用问模型了：既拦住了，也省一次请求
            return {"score": 0, "is_match": False, "veto_hit": hit,
                    "reason": f"命中否决词「{hit}」（代码直查 JD）",
                    "strengths": [], "weaknesses": [], "suggested_greeting": ""}

        if not self.providers:
            self.fallback_count += 1
            return self._fallback_result("未配置 AI 接口")

        # 检查缓存
        if self.cache_enabled and self._resume_hash:
            cache_key = self._make_cache_key(job.get("url", ""), self._resume_hash)
            cache = self._load_cache()
            if cache_key in cache:
                self.cache_hit_count += 1
                self._log("INFO", f"缓存命中: {job.get('job_name', '')}")
                # 命中的记录也要带上当初判分的接口名，否则面板会把它当"没跑 AI"
                self.last_model_name = (cache[cache_key] or {}).get("model", "")
                self.last_raw_response = None
                return cache[cache_key]["result"]

        # 依次尝试 provider — 受单岗位尝试数与总耗时双重限制
        # 上一个岗位的接口名/原文不能留到这条记录上（by_model 统计会算错）
        self.last_model_name = ""
        self.last_raw_response = None
        prompt = self._build_prompt(job)
        # 保存 prompt 信息供 GreetRecord 记录使用
        self.last_system_prompt = prompt[0]["content"] if len(prompt) > 0 else None
        self.last_user_prompt = prompt[1]["content"] if len(prompt) > 1 else None
        last_error = None
        now = time.time()
        deadline = now + self.JOB_BUDGET_SECONDS
        attempts = 0
        skipped_cooling = 0
        unhealthy = self._unhealthy_names() if self._skip_unhealthy else set()
        if unhealthy and len(unhealthy) >= len(self.providers):
            # 一个都不剩说明体检结果本身不可信（或全部真挂了），照原顺序硬试
            self._log("WARN", "体检显示所有接口都不可用，本轮不跳过任何接口")
            unhealthy = set()
        for provider in self.providers:
            if not provider.is_valid():
                self._log("WARN", f"AI 接口 '{provider.name}' 配置无效，跳过")
                continue
            if provider.name in unhealthy:
                continue
            until = self._cooldown_until.get(provider.name, 0)
            if until > now:
                skipped_cooling += 1
                continue
            if attempts >= self.MAX_ATTEMPTS_PER_JOB:
                break
            if time.time() >= deadline:
                self._log("WARN", f"AI 分析超出 {self.JOB_BUDGET_SECONDS}s 预算，"
                                  f"放弃剩余接口（已试 {attempts} 个）")
                break
            attempts += 1
            try:
                self._log("INFO", f"通过 [{provider.name}] ({provider.model}) 分析...")
                result = self._call_provider_api(provider, prompt)
                self._cooldown_until.pop(provider.name, None)
                self._report_health(provider, ok=True)
                self.last_model_name = provider.model
                # 记下是哪个接口判的：追问要回到同一个接口，换接口问出来的
                # 是"另一个人怎么判"，不是"你刚才为什么这么判"
                self.last_provider = provider
                self.analyzed_count += 1
                if result.get("is_match", False):
                    self.match_count += 1

                # 写入缓存 —— 只缓存真判断，超时/解析失败的结果缓存 24 小时
                # 会让同一个岗位永远"默认通过"，等于悄悄跳过筛选
                if self.cache_enabled and self._resume_hash and not result.get("ai_error"):
                    cache_key = self._make_cache_key(job.get("url", ""), self._resume_hash)
                    cache = self._load_cache()
                    cache[cache_key] = {
                        "result": result,
                        "model": provider.model,
                        "cached_at": time.time(),
                        "_expires_at": time.time() + self.cache_ttl,
                    }
                    self._save_cache(cache)

                return result

            except Exception as e:
                last_error = e
                self._cool_down(provider, e)
                self._report_health(provider, ok=False, error=str(e))
                self._log("WARN", f"[{provider.name}] 失败: {e}，尝试下一个...")
                continue

        if skipped_cooling:
            # 说清楚冷却表是按服务商共享的：两个号用的是同一批 key，
            # 另一个号撞到的 429 这条链也照样要避开，不是本号自己的接口坏了
            self._log("INFO", f"{skipped_cooling} 个 AI 接口在冷却中，已跳过"
                              f"（冷却按服务商全进程共享，两个号一起摊同一份限流）")
        if unhealthy:
            self._log("INFO", f"按体检结果跳过 {len(unhealthy)} 个已知不可用的 AI 接口")

        # 全部失败
        self._log("ERROR", f"所有 AI 接口均失败，最后错误: {last_error}")
        self.fallback_count += 1
        return self._fallback_result(f"AI 分析异常: {last_error}")

    def _fallback_result(self, reason: str) -> dict:
        """AI 没给出判断时的结果：is_match 由 fail_action 决定，不再有\"默认通过\"写死。"""
        passed = self.fail_action != "skip"
        return {"score": 50 if passed else 0,
                "is_match": passed,
                "ai_error": True,
                "reason": f"{reason}，{'默认通过' if passed else '按配置跳过'}",
                "suggested_greeting": ""}

    def _build_probe_prompt(self, job: dict, verdict: dict) -> list:
        """追问提示词：只问"卡在哪一条"，不让它改判。"""
        resume = self._resume or {}
        system_msg = (
            "你刚才判定这个岗位与求职者的简历不匹配。现在只回答一件事：到底卡在哪一条。"
            "不要重新评分、不要改判、不要安慰性套话。说不出来就把 blocking_requirement 留空。"
        )
        user_msg = (
            "【你刚才的判定】\n"
            f"score：{verdict.get('score', '')}\n"
            f"reason：{verdict.get('reason', '')}\n\n"
            "【求职者简历】\n"
            f"教育背景：{resume.get('school', '')} {resume.get('major', '')} "
            f"{resume.get('degree', '')}\n"
            f"技能：{', '.join(resume.get('skills', []))}\n"
            f"工作经验：{resume.get('experience', '')}\n\n"
            "【岗位】\n"
            f"岗位名称：{job.get('job_name', '')}\n"
            f"任职要求：{job.get('requirements', '')}\n"
            f"岗位描述：{job.get('description', '')}\n\n"
            "请按以下 JSON 格式返回（不要包含其他内容）：\n"
            '{\n'
            '  "blocking_requirement": "没过的那一条硬性要求，尽量用任职要求里的原文",\n'
            '  "evidence_missing": "简历里缺什么证据（会做但没写清楚，写清楚在哪）",\n'
            '  "fixable_by_resume": true/false,\n'
            '  "score_if_fixed": 0-100\n'
            '}\n'
            "fixable_by_resume 为 true 表示求职者其实具备这条能力、只是简历没体现；"
            "为 false 表示确实不具备。score_if_fixed 是补齐这条证据之后你愿意给的分。"
        )
        return [{"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}]

    def probe_rejection(self, job: dict, verdict: dict):
        """AI 判"不符合"之后，回到判它的那个接口追问到底卡在哪一条。

        只问 AI，不给 HR 发任何消息（防骚扰红线：主动问 HR "哪里不合适" 是骚扰）。
        拿不到可信回答一律返回 None —— 追问是附加信息，不能变成打招呼轮的新炸点。
        """
        provider = self.last_provider or next(
            (p for p in self.providers if p.is_valid()), None)
        if provider is None:
            return None
        try:
            raw = self._call_provider_api(provider,
                                          self._build_probe_prompt(job, verdict or {}),
                                          normalize=False)
        except Exception as e:
            self._log("DEBUG", f"追问不匹配原因没问出来（{provider.name}）: {e}")
            return None
        if not isinstance(raw, dict):
            return None
        blocker = str(raw.get("blocking_requirement") or "").strip()
        if not blocker:
            # 说不出哪条硬性要求没过，就等于没问到：留着只会让复盘产出空话建议
            return None
        return {
            "blocking_requirement": blocker[:PROBE_FIELD_MAX_LEN],
            "evidence_missing": str(raw.get("evidence_missing") or "").strip()[:PROBE_FIELD_MAX_LEN],
            "fixable_by_resume": _as_bool(raw.get("fixable_by_resume")),
            "score_if_fixed": _clamp_int(raw.get("score_if_fixed")),
        }

    def _extract_json(self, text: str) -> dict:
        """从模型正文里抠出那个 JSON 对象。

        三种真实输出都得吃下（实测样本）：纯 JSON、```json 代码块、
        前后夹带中文说明。括号按字符串状态配对，避免尾随说明里的 "}"
        把截取区间带偏（旧实现用首 { 到末 }，遇到 "（详见附录}）" 直接解析失败）。
        """
        body = text.strip()
        fence = re.search(r"```(?:json)?\s*(.+?)\s*```", body, re.S)
        if fence:
            body = fence.group(1).strip()
        start = body.find("{")
        if start < 0:
            raise AIResponseUnusable(f"响应里没有 JSON（前 60 字：{body[:60]}）")

        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(body)):
            ch = body[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(body[start:i + 1])
                    except json.JSONDecodeError as e:
                        raise AIResponseUnusable(f"JSON 不完整: {e}")
        raise AIResponseUnusable(f"JSON 没有闭合（末 40 字：{body[-40:]}）")

    def _normalize_result(self, result) -> dict:
        """把模型给的 JSON 收敛成一份可信判分，缺判分就抛。"""
        if not isinstance(result, dict):
            raise AIResponseUnusable(f"返回的不是 JSON 对象：{str(result)[:60]}")

        score = None
        raw_score = result.get("score")
        if raw_score is not None:
            try:
                score = max(0, min(100, int(float(raw_score))))
            except (TypeError, ValueError):
                score = None

        is_match = result.get("is_match")
        if score is None and not isinstance(is_match, bool):
            raise AIResponseUnusable(
                "返回里没有可用的 score/is_match，无法判分"
                f"（前 60 字：{json.dumps(result, ensure_ascii=False)[:60]}）")

        if score is None:
            # 只给了结论没给分：按阈值折算一个分，别让调用方拿默认 50 误判
            score = self.match_threshold if is_match else max(0, self.match_threshold - 1)
        if not isinstance(is_match, bool):
            is_match = score >= self.match_threshold

        result["score"] = score
        result["is_match"] = is_match

        # 自定义筛选条件是硬否决：模型自己承认命中就不能因为分高而放行
        veto = str(result.get("veto_hit") or "").strip()
        if veto:
            result["is_match"] = False
            result["score"] = min(score, max(0, self.match_threshold - 1))
            result["reason"] = f"命中硬性筛选条件「{veto}」，不予通过。{result.get('reason', '')}"
        elif self.veto_only_match:
            # 开了"只看否决词"：没命中否决词就放行，AI 那句"与求职方向不符"
            # 不能拦人——基础提示词里带着简历/求职意向，模型照样会按方向打分，
            # 光改判分提示词压不住它（2026-10-04 实测：客服 10 分、视频剪辑 15 分）
            result["is_match"] = True
            result["score"] = max(score, 95)
            result["reason"] = "只看否决词：未命中否决条件，放行。" + str(result.get("reason", ""))
        return result

    def _call_provider_api(self, provider: AIProviderConfig, messages: list,
                           normalize: bool = True) -> dict:
        """调用指定 AI 接口，返回一份可信判分；拿不到就抛 AIResponseUnusable。

        normalize=False 是给"判分复盘"的追问用的：那回答里没有 score/is_match，
        套判分校验会被当成无效输出丢掉。
        """
        url = f"{provider.api_base}/chat/completions"
        payload = json.dumps({
            "model": provider.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": self.analyze_max_tokens,
            "chat_template_kwargs": {"enable_thinking": True},
        }).encode("utf-8")

        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {provider.api_key}")

        try:
            with urlopen(req, timeout=provider.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except URLError as e:
            raise Exception(f"API 请求失败: {e}")
        except TimeoutError:
            raise Exception(f"请求超时（{provider.timeout}s）")

        try:
            choice = data["choices"][0]
        except (KeyError, IndexError, TypeError):
            raise AIResponseUnusable(
                f"响应缺少字段 choices（{str(data.get('msg') or data)[:120]}）")

        return self._parse_completion(choice, normalize=normalize)

    def _parse_completion(self, choice: dict, normalize: bool = True) -> dict:
        """把一次 chat completion 的 choice 收敛成判分，不可用就抛。

        单独拆出来是为了让诊断脚本（tools/measure_ai_quality.py）用的是
        生产同一份判定，而不是自己抄一套"看起来能解析"。
        """
        message = choice.get("message") or {}
        finish = choice.get("finish_reason") or ""
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or "").strip()
        self.last_raw_response = content or reasoning

        if not content and finish == "length":
            # 推理型接口把预算全花在 thinking 上，正文一个字没写：
            # 这不是"这个岗位不匹配"，是这个接口这次没给出判断
            raise AIResponseUnusable(
                f"正文被截断（thinking 写了 {len(reasoning)} 字，"
                f"max_tokens={self.analyze_max_tokens} 不够）")

        body = content or reasoning
        if not body:
            raise AIResponseUnusable("模型未返回正文（content 与 reasoning_content 均为空）")

        parsed = self._extract_json(body)
        if not normalize:
            return parsed
        return self._normalize_result(parsed)

    def _build_prompt(self, job: dict) -> list:
        """构建 AI 分析提示词。"""
        resume = self._resume or {}
        system_msg = (
            "你是 Boss直聘智能投递助手的岗位匹配分析专家。你的任务是分析招聘岗位与求职者简历的匹配程度，"
            "给出评分和详细理由。请按 JSON 格式返回结果。"
        )
        if self.custom_scoring_prompt:
            system_msg += "\n\n【用户自定义打分要求】\n" + self.custom_scoring_prompt
        user_msg = (
            "【求职者简历】\n"
            f"教育背景：{resume.get('school', '')} "
            f"{resume.get('major', '')} "
            f"{resume.get('degree', '')}\n"
            f"技能：{', '.join(resume.get('skills', []))}\n"
            f"工作经验：{resume.get('experience', '')}\n"
            f"求职意向：{resume.get('target_position', '')}\n\n"
            "【招聘岗位】\n"
            f"岗位名称：{job.get('job_name', '')}\n"
            f"薪资：{job.get('salary', '')}\n"
            f"岗位描述：{job.get('description', '')}\n"
            f"任职要求：{job.get('requirements', '')}\n"
            f"公司：{job.get('company', '')}\n\n"
        )
        if self.custom_filter_keywords:
            keywords_str = "、".join(self.custom_filter_keywords)
            user_msg += (
                f"【用户硬性筛选条件】\n"
                f"以下是求职者设定的否决条件：{keywords_str}\n"
                "只要岗位命中其中任意一条（例如岗位是外包驻场、城市不在范围内、"
                "学历/经验要求不满足），is_match 必须为 false，"
                "并把命中的那条原样写进 veto_hit；没命中则 veto_hit 留空字符串。\n"
                "命中否决条件时不要因为薪资或其它方面不错而加分放行。\n\n"
            )
        veto_field = ('  "veto_hit": "命中的否决条件原文，没有则留空",\n'
                      if self.custom_filter_keywords else "")
        user_msg += (
            "请分析匹配度，按以下 JSON 格式返回（不要包含其他内容）：\n"
            '{\n  "score": 0-100,\n  "is_match": true/false,\n'
            + veto_field +
            '  "reason": "匹配分析简要说明",\n'
            '  "strengths": ["优势1", "优势2"],\n'
            '  "weaknesses": ["劣势1", "劣势2"],\n'
            '  "suggested_greeting": "基于岗位要求生成的个性化打招呼消息"\n}'
        )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

    def _make_cache_key(self, job_url: str, resume_hash: str) -> str:
        # 口径必须进键：只按"岗位URL+简历"缓存的话，改了判分提示词/阈值/否决词
        # 还是命中旧结论，等于改了不生效（2026-10-04 一轮 62 次判分 54 次是缓存）
        rule = "|".join([
            str(self.match_threshold),
            str(self.veto_only_match),
            str(self.custom_scoring_prompt or ""),
            ",".join(self.custom_filter_keywords or []),
        ])
        raw = f"{job_url}:{resume_hash}:{rule}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict:
        if not AI_CACHE_FILE.exists():
            return {}
        try:
            with open(AI_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                now = time.time()
                expired = [k for k, v in data.items() if v.get("_expires_at", 0) < now]
                for k in expired:
                    del data[k]
                return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self, cache: dict):
        with self._cache_lock:
            write_json_atomic(AI_CACHE_FILE, cache)

    def clear_cache(self):
        if AI_CACHE_FILE.exists():
            AI_CACHE_FILE.unlink()

    def get_stats(self) -> dict:
        return {
            "analyzed": self.analyzed_count,
            "matched": self.match_count,
            "cache_hits": self.cache_hit_count,
            "match_rate": round(self.match_count / max(self.analyzed_count, 1) * 100, 1),
            "threshold": self.match_threshold,
            "providers_total": len(self.providers),
            "providers_valid": sum(1 for p in self.providers if p.is_valid()),
        }


# ─────────────────────────────────────────────
# GreetEngine — 自动打招呼/投递引擎
# ─────────────────────────────────────────────

class GreetEngine:
    """自动打招呼/投递引擎。

    使用 BrowserManager 管理浏览器，UnifiedConfig 提供配置，
    支持 AI 匹配分析、Cookie 管理、反爬策略和去重管理。

    使用方式：
        manager = BrowserManager(config)
        engine = GreetEngine(manager, unified_config, log_callback=print)
        engine.start(tasks=[{"query": "数据分析", "city": "上海"}])
    """

    # 多账号共用 data/chatted_jobs.json，写盘要跨实例串行 + 合并
    _chatted_lock = threading.Lock()
    # 去重快照的重读间隔（秒）：另一账号打过的岗位本账号要能在下一轮看到
    CHATTED_REFRESH_SECONDS = 60

    def __init__(
        self,
        browser_manager: BrowserManager,
        config: UnifiedConfig,
        log_callback: Optional[Callable] = None,
        progress_callback: Optional[Callable] = None,
        greet_event_cb: Optional[Callable] = None,
        wind_control_cb: Optional[Callable] = None,
        captcha_gate_cb: Optional[Callable[[], bool]] = None,
        account_index: int = 0,
    ):
        self.browser_manager = browser_manager
        self.config = config
        # 话术/简历图片按账号取：写死 accounts[0] 会让账号2 用主账号的
        # 打招呼语和简历，两个号发出去的内容一模一样
        self.account_index = account_index
        self.log_cb = log_callback
        self.progress_cb = progress_callback
        self._greet_event_cb = greet_event_cb
        # 风控触发回调 — 触发时通知 main_loop 暂停打招呼并推送前端事件
        # 回调签名: wind_control_cb(message: str, wtype: str) -> None
        # wtype: "captcha"（验证码） | "limit"（限制提示）
        self._wind_control_cb = wind_control_cb
        # 人机验证闸门 — 签名: () -> bool，True 表示人工已经过完验证。
        # 与 wind_control_cb 的分工：那个只通知前端"出风控了"，这个是**等结果**，
        # 不打招呼侧以前只 return 原因、从不等，于是验证页就在那儿干挂着。
        self._captcha_gate_cb = captcha_gate_cb

        # 运行状态
        self.running = False
        self._is_logged_in = False
        self._login_event = threading.Event()
        # 已沟通岗位集合的进程内缓存（首次使用时读盘）
        self._chatted_cache = None
        self._chatted_loaded_at = 0.0

        # 统计
        self.applied_count = 0
        self.skipped_count = 0
        self.total_jobs = 0
        # 连续"点了沟通没出抽屉"的次数与冷却截止：BOSS 当日沟通额度用完后
        # 这个失败会一直重复，不停手就是整夜每 7 分钟白跑一趟
        self._no_drawer_streak = 0
        self._greet_cooldown_until = 0.0

        # 城市字典（从 API 捕获）
        self._city_dict = {}

        # 当前任务参数
        self._query = ""
        self._city = "上海"
        self._job_type = ""
        self._scroll_pages = 5
        self._greeting_source = "默认"
        self._image_files = []
        self._min_interval = 3
        self._max_interval = 8
        self._cookie_file = "zhipin_cookies.json"
        self._login_required_cb = None

        # 岗位列表
        self.jobs = []

        # 投递队列锁（防止并发操作浏览器）
        self._apply_lock = threading.Lock()

        # AI 分析器
        self._ai_analyzer = None

        # 打招呼记录存储（使用全局单例，确保与 API 层、清空操作共享同一实例）
        self._greet_store = _get_greet_store()

        # 追踪最后一次 AI 分析的完整信息（供 GreetRecord 记录使用）
        self._last_ai_result = None
        self._last_ai_system_prompt = None
        self._last_ai_user_prompt = None
        self._last_ai_model = ""
        self._last_ai_raw_response = None
        # AI 本次有没有真给出判断（见 _record_greet 的 ai_error）+ 本次判分耗时
        self._last_ai_duration_ms = 0

        # 从配置加载参数
        self._load_config_params()
        self._load_city_dict()

    def _load_config_params(self):
        """从 UnifiedConfig 读取所有可调参数。"""
        browser_cfg = self.config.browser
        self._headless = browser_cfg.headless
        self._viewport_width = browser_cfg.viewport_width
        self._viewport_height = browser_cfg.viewport_height
        self._page_load_timeout = browser_cfg.page_load_timeout
        self._custom_user_agent = browser_cfg.custom_user_agent
        self._proxy = browser_cfg.proxy
        self._browser_type = browser_cfg.browser_type

        login_cfg = self.config.login
        self._login_wait_timeout = login_cfg.wait_timeout
        self._cookie_file = login_cfg.cookie_file

        # 频率限制 / 重试次数由 reload_runtime_settings() 统一赋值（见文件末尾）
        retry_cfg = self.config.greet.retry
        self._retry_base_delay = retry_cfg.base_delay
        self._retry_backoff_factor = retry_cfg.backoff_factor

        self._apply_ai_config()

        # 岗位去重集合（基于URL+公司名+岗位名，仅当前运行期间有效）
        self._applied_job_keys = set()

        self.reload_runtime_settings()

    def _apply_ai_config(self):
        """把 config.ai 摊平成引擎字段；配置真的变了就丢掉容灾链，让它按新配置重建。

        热重载只换 config 对象不会重建容灾链 —— 阈值、否决关键词、单次预算改了
        要重启才生效，这正是"改了必须生效"的反例。签名相同则什么都不动，
        免得每轮热重载把冷却表清零、又开始重踩已知坏接口。
        """
        ai = self.config.ai
        sig = (ai.enabled, ai.match_threshold, ai.analyze_max_tokens,
               tuple(ai.custom_filter_keywords or []), ai.custom_scoring_prompt,
               ai.skip_unhealthy, ai.fail_action, bool(ai.veto_only_match),
               tuple((p.name, p.model, p.api_base, bool(p.api_key)) for p in ai.providers))
        if getattr(self, "_ai_config_sig", None) == sig:
            return
        rebuilt = self._ai_analyzer is not None
        self._ai_config_sig = sig

        self._ai_enabled = ai.enabled
        self._ai_threshold = ai.match_threshold
        self._ai_custom_filter_keywords = ai.custom_filter_keywords
        self._ai_custom_scoring_prompt = ai.custom_scoring_prompt
        self._ai_skip_unhealthy = ai.skip_unhealthy
        self._ai_fail_action = ai.fail_action
        self._ai_veto_only = bool(ai.veto_only_match)
        self._analyze_max_tokens = ai.analyze_max_tokens

        # AI providers 列表（从 UnifiedConfig 转换为 AIAnalyzerChain 所需格式）
        self._ai_providers = []
        for p in ai.providers:
            self._ai_providers.append({
                "name": p.name,
                "api_key": p.api_key,
                "api_base": p.api_base,
                "model": p.model,
                "timeout": p.timeout,
            })
        # 兼容旧格式
        if not self._ai_providers and ai.api_key:
            self._ai_providers.append({
                "name": "默认",
                "api_key": ai.api_key,
                "api_base": ai.api_base,
                "model": ai.model,
                "timeout": 30,
            })

        if rebuilt:
            self._log("INFO", "AI 配置已变更，下一个岗位按新配置重建容灾链")
            self._ai_analyzer = None

    def reload_runtime_settings(self):
        """从 self.config 重读「改了就该立刻生效」的字段。

        构造时读一次是不够的：主循环每轮热重载会换掉 config 对象，
        话术/简历图片/间隔若仍停留在首次快照，前端改了就要重启才生效。
        """
        self._resume_cfg = {
            "school": self.config.resume.school,
            "major": self.config.resume.major,
            "degree": self.config.resume.degree,
            "skills": self.config.resume.skills,
            "experience": self.config.resume.experience,
            "target_position": self.config.resume.target_position,
            "self_intro": self.config.resume.self_intro,
        }

        self._apply_ai_config()

        rl = self.config.greet.rate_limit
        self._rate_limit_enabled = rl.enabled
        self._max_per_hour = rl.max_per_hour
        self._max_per_day = rl.max_per_day
        self._retry_max_attempts = self.config.greet.retry.max_attempts

        # 从 greet.accounts 读取本账号的默认任务参数
        acc = self._account()
        if acc:
            self._min_interval = acc.message_interval_min
            self._max_interval = acc.message_interval_max
            self._cookie_file = acc.cookie_file
            self._image_files = acc.image_files
            if acc.jobs:
                job = acc.jobs[0]
                self._query = job.query
                self._city = job.city
                self._job_type = getattr(job, "job_type", "") or ""
                self._scroll_pages = job.scroll_pages
                self._greeting_source = "岗位配置"
                self._image_files = job.image_files or self._image_files

    def _account(self):
        """本引擎所属账号的配置；索引越界时回落到第一个账号。"""
        accounts = self.config.greet.accounts
        if not accounts:
            return None
        return accounts[self.account_index] if self.account_index < len(accounts) else accounts[0]

    def _account_label(self) -> str:
        """记录里显示的账号名 — 用配置里的名字，不再写 cookie 文件名。"""
        name = getattr(self._account(), "name", "") or ""
        return name or f"账号{self.account_index}"

    def _log(self, level: str, msg: str):
        """统一日志输出 — 回调 + 文件日志。"""
        if self.log_cb:
            self.log_cb(f"[{level}] {msg}")
        try:
            _file_logger.info(f"[{level}] {msg}")
        except Exception:
            pass

    def begin_event_ts(self, ts: str = "") -> str:
        """开始一次岗位动作：落库与实时推送共用这一份时间戳。

        前端按「岗位 + 时间戳 + 状态」把推送行和轮询回来的库行认成同一条。两边各取
        一次 now() 就会差一秒，同一次投递在表里留两行，看起来就是"记录和日志对不上"。
        """
        self._event_ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return self._event_ts

    def event_ts(self) -> str:
        return getattr(self, "_event_ts", None) or self.begin_event_ts()

    def _emit_greet_event(self, job: dict, status: str, ai_result: dict = None,
                          skip_reason: str = ""):
        """推送投递事件到前端表格。

        Args:
            job: 岗位信息字典
            status: "success" | "skip" | "Bai_skip" | "already" | "error"
            ai_result: AI 分析结果（可选）
            skip_reason: 跳过原因（可选，用于前端显示）
        """
        if not self._greet_event_cb:
            return
        try:
            ai = ai_result or job.get("_ai_result") or {}
            # 确保 ai_reason 完整透传到前端：job 上没有判分结果时，
            # 从 "AI判定不匹配: 具体原因" 形式的 skip_reason 里取
            ai_reason = ai.get("reason", "")
            if not ai_reason and skip_reason and "AI判定不匹配" in skip_reason:
                # skip_reason 格式: "AI判定不匹配: 具体原因..."，提取冒号后的部分
                ai_reason = skip_reason
            emit_data = {
                "timestamp": self.event_ts(),
                "job_name": job.get("job_name", ""),
                "company": job.get("company", "") or job.get("company_location", ""),
                "salary": job.get("salary", ""),
                "status": status,
                "ai_score": ai.get("score", 0),
                "ai_reason": ai_reason,
                "ai_match": ai.get("is_match", False),
                "greeting": job.get("_actual_greeting_sent", "")[:60],
                "url": job.get("url", ""),
                "skip_reason": skip_reason or job.get("_last_skip_reason", ""),
                "is_skipped": status in ("skip", "ai_skip", "already", "error"),
                # 追问结果也要实时推：否则前端当场看不到，非得刷新读历史才有
                "ai_probe": job.get("_ai_probe"),
                "auto_greet_note": job.get("_auto_greet_note"),
                # 前端按账号切记录，实时推送的行也要带账号，否则切到账号2
                # 时新推送的行情会串进主账号的表格
                "account_index": self.account_index,
                "account_name": self._account_label(),
            }
            self._greet_event_cb(emit_data)
        except Exception:
            pass

    def _record_greet(
        self,
        job: dict,
        is_greeted: bool = False,
        is_skipped: bool = False,
        skip_reason: str = "",
        actual_greeting_sent: str = "",
        status: str = "",
    ):
        """创建并保存一条打招呼/AI分析记录。

        AI 字段取自 job["_ai_result"] / job["_ai_meta"] —— 这条岗位自己那次判分。

        Args:
            job: 岗位信息字典
            is_greeted: 是否成功打招呼
            is_skipped: 是否跳过
            skip_reason: 跳过原因
            actual_greeting_sent: 实际发送的打招呼语
            status: 状态 (pending/applied/skipped/failed)，留空则自动推导
        """
        try:
            ai_result = job.get("_ai_result") or {}
            ai_meta = job.get("_ai_meta") or {}
            # 推导 status（若调用方未指定）
            if not status:
                if is_greeted:
                    status = "applied"
                elif is_skipped:
                    reason = skip_reason or ""
                    if any(kw in reason for kw in ("失败", "异常", "错误")):
                        status = "failed"
                    else:
                        status = "skipped"
                else:
                    status = "pending"
            # 招呼语只写"真发出去的那一句"。
            # 以前这里还兜 AI 建议文案与本号配置文案：410 条 skipped 记录都带着一句
            # 从没发过的"招呼语"，和 BOSS 端逐条对的时候全对不上
            greeting_message = actual_greeting_sent
            record = GreetRecord(
                job_name=job.get("job_name", ""),
                job_url=job.get("url", ""),
                ai_error=bool(ai_result.get("ai_error")),
                ai_duration_ms=ai_meta.get("duration_ms", 0),
                # 判分复盘的追问结果挂在 job 上：谁追问谁知道，不占用"最后一次调用"那批
                # 状态（发简历、回复都会覆写它们）
                ai_probe=job.get("_ai_probe"),
                auto_greet_note=job.get("_auto_greet_note"),
                company=job.get("company", job.get("company_location", "")),
                salary=job.get("salary", ""),
                job_description=job.get("jd_description", job.get("description", "")),
                job_requirements=job.get("jd_requirements", job.get("requirements", "")),
                ai_score=ai_result.get("score", 0),
                ai_is_match=ai_result.get("is_match", False),
                ai_reason=ai_result.get("reason", ""),
                ai_strengths=ai_result.get("strengths", []),
                ai_weaknesses=ai_result.get("weaknesses", []),
                ai_suggested_greeting=ai_result.get("suggested_greeting", ""),
                system_prompt=ai_meta.get("system_prompt"),
                user_prompt=ai_meta.get("user_prompt"),
                ai_model=ai_meta.get("model", ""),
                ai_raw_response=ai_meta.get("raw_response"),
                actual_greeting_sent=actual_greeting_sent,
                is_greeted=is_greeted,
                is_skipped=is_skipped,
                skip_reason=skip_reason,
                account_name=self._account_label(),
                account_index=self.account_index,
                status=status,
                greeting_message=greeting_message,
                timestamp=self.event_ts(),
            )
            self._greet_store.add(record)
        except Exception as e:
            self._log("WARN", f"记录打招呼信息失败: {e}")

    def _already_recorded_today(self, job: dict, reason: str) -> bool:
        """这个岗位今天在本号下是否已经记过同一类别的结论。

        一轮扫下来同一个岗位的结论不会变，不去重就是每轮几百条重复记录：
        招呼语空缺那条更是每轮每个岗位都记一遍，把 BOSS 端真实发出去的
        那几条淹掉，用户看到的就是"日志一堆跳过、记录一片重复"。
        失败类（可重试）不在这里拦。
        """
        kind = classify_greet_skip(reason)
        if not kind:
            return False
        try:
            return self._greet_store.has_today(job.get("url", ""), kind,
                                               self.account_index)
        except Exception as e:
            self._log("DEBUG", f"打招呼记录去重检查失败，照常记录: {e}")
            return False

    def _report_progress(self):
        if self.progress_cb:
            self.progress_cb({
                "applied": self.applied_count,
                "skipped": self.skipped_count,
                "total": self.total_jobs,
            })

    # ── 对外接口 ──


    def stop(self):
        """停止打招呼引擎。"""
        self.running = False

    def confirm_login(self) -> None:
        """确认登录完成（外部调用，通知引擎用户已手动登录）。"""
        self._login_event.set()
        # 确认登录后自动导航离开登录页
        instance = self.browser_manager.get_instance()
        if instance:
            try:
                instance.get("https://www.zhipin.com")
            except Exception:
                pass

    def check_login(self) -> bool:
        """检查登录状态。

        Returns:
            已登录返回 True，未登录返回 False。
        """
        instance = self.browser_manager.get_instance()
        if instance is None:
            return False
        try:
            for selector in (SELECTOR_NAV, ".header-login-btn", ".user-nav"):
                nav_ele = instance.ele(selector, timeout=3)
                if nav_ele:
                    text = nav_ele.text
                    if "登录/注册" not in text and text.strip():
                        self._is_logged_in = True
                        return True
            # 尝试检查 URL 是否包含登录页路径
            try:
                current_url = instance.url
                if current_url and "passport" not in current_url and "login" not in current_url:
                    return True
            except Exception:
                pass
            # 导航到首页检查
            instance.get("https://www.zhipin.com")
            self._random_delay(2, 5)
            for selector in (SELECTOR_NAV, ".header-login-btn", ".user-nav"):
                nav_ele = instance.ele(selector, timeout=3)
                if nav_ele:
                    text = nav_ele.text
                    if "登录/注册" not in text and text.strip():
                        self._is_logged_in = True
                        return True
            return False
        except Exception:
            return False

    def search_jobs(self, query: str, city: str, scroll_pages: int = 5,
                    job_type: str = "") -> list:
        """搜索岗位并返回岗位列表。

        Args:
            query: 搜索关键词，如 "数据分析"
            city: 城市名称，如 "上海"，也可以填 "全国"
            scroll_pages: 滚动翻页次数
            job_type: 求职类型 "全职"/"实习"/"兼职"，空=不限

        Returns:
            岗位信息字典列表，每个包含 job_name/salary/experience/education/
            company_location/url/query 字段
        """
        self._query = query
        self._city = city
        self._job_type = job_type or ""
        self._scroll_pages = scroll_pages
        self._parse_job_list()
        return self.jobs

    def send_greeting(self, job_info: dict) -> bool:
        """对单个岗位发送打招呼消息。

        Args:
            job_info: 岗位信息字典，需包含 url 字段

        Returns:
            发送成功返回 True，失败返回 False
        """
        job_name = job_info.get('job_name', '')
        job_url = job_info.get('url', '')
        self._log("DEBUG", f"send_greeting 被调用: job={job_name}, url={job_url[:60]}, running={self.running}")
        
        if not self.running:
            self._log("WARN", f"投递跳过: running=False, job={job_name}")
            return False
        if not job_url:
            self._log("WARN", f"投递跳过: url为空, job={job_name}")
            return False
        try:
            success, fail_reason = self._apply_job(job_info)
            if success:
                self._no_drawer_streak = 0
                self.applied_count += 1
                self._log("SUCCESS", f"✅ 已投递: {job_name}")
                # _apply_job_inner 在点发送那一刻已经即时记过，这里再记一次
                # 同一岗位就会出现两条记录
                if not job_info.get("_recorded"):
                    self._emit_greet_event(job_info, "success")
                    self._record_greet(
                        job_info, is_greeted=True,
                        actual_greeting_sent=job_info.get("_actual_greeting_sent", ""),
                    )
            else:
                self.skipped_count += 1
                self._log("WARN", f"⏭️ 跳过: {job_name}（原因: {fail_reason}）")
                self._emit_greet_event(job_info, "skip", skip_reason=fail_reason or "投递失败-原因未知")
                self._record_greet(job_info, is_skipped=True, skip_reason=fail_reason or "投递失败-原因未知")
            self._report_progress()
            return success
        except Exception as e:
            self._log("WARN", f"发送打招呼异常: {e}")
            self.skipped_count += 1
            self._emit_greet_event(job_info, "error", skip_reason=f"发送异常: {e}")
            self._report_progress()
            self._record_greet(job_info, is_skipped=True, skip_reason=f"发送异常: {e}")
            return False

    # ── 内部运行逻辑 ──

    def _load_city_dict(self):
        """从文件加载之前捕获的城市数据。"""
        try:
            if CITY_DICT_FILE.exists():
                with open(CITY_DICT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._city_dict.update(data)
                        self._log("INFO", f"已加载 {len(self._city_dict)} 个城市数据(文件)")
        except Exception:
            pass


    def _captcha_handoff(self) -> bool:
        """把人机验证交给人工，等闸门给结论。

        True  = 人工在时限内过完了，调用方可以继续这个岗位；
        False = 到点没人处理（或根本没接闸门、闸门自己炸了），调用方按原因跳过
                当前任务。连续几次都没人处理由 `_captcha_gate` 自己升格成停轮，
                这里不重复计数——两处各数一套就会出现"到底第几次"的歧义。
        """
        if self._captcha_gate_cb is None:
            return False
        try:
            return bool(self._captcha_gate_cb())
        except Exception as e:
            self._log("WARN", f"验证码闸门异常，按未恢复处理: {str(e)[:60]}")
            return False

    def _explain_missing_chat_button(self, instance, requested_url: str, job: dict):
        """点完沟通却没有按钮：先归因，是验证页就交人工，人工过完再重试一次按钮。

        返回 (原因, 按钮)。原因是空串表示可以继续；按钮为 None 表示调用方跳过这个岗位。
        只重试一次：人工过完验证按钮还在就接着投这个岗位，不在就老实跳过 ——
        为一个岗位反复等能把整轮吊死，而剩下的岗位本来也不该陪绑。
        """
        snap = self._chat_snapshot(instance)
        landed = str(snap.get("url") or "") or (getattr(instance, "url", "") or "")
        reason, already = chat_button_failure_reason(requested_url, landed, snap)
        self._log("WARN", f"没有沟通按钮｜{reason}")
        self._log("WARN", f"  现场 请求={requested_url[:60]} 落地={landed[:60]} "
                          f"按钮={snap.get('buttons') or '无'} "
                          f"提示={snap.get('notice') or '无'}")
        if reason == CAPTCHA_REASON and self._captcha_handoff():
            btn = self._find_chat_button(timeout=8)
            if btn is not None:
                self._log("INFO", "人工已完成验证，沟通按钮已出现，继续这个岗位")
                return "", btn
        if already:
            # BOSS 把已沟通的岗位直接跳成会话页：标了已沟通，
            # 下一轮搜索才不会又撞同一个岗位
            self._mark_chatted(job)
        return reason, None

    def _wait_for_login(self) -> bool:
        """等待用户手动登录 —— 停止信号必须听得见。

        必须先 clear：这个 event 只在 confirm_login 里 set 过、从不复位，
        人工登录过一次之后这里就会永远立刻返回，真掉登录时变成 300 秒空转。
        也不能整段等 timeout：那样点"停止"要等满 login_wait_timeout 才生效，
        所以切成 1 秒一片，每片都回头看 running。
        """
        self._login_event.clear()
        deadline = time.time() + max(0, self._login_wait_timeout)
        while time.time() < deadline:
            if not self.running:
                return False
            if self._login_event.wait(timeout=min(1.0, max(0.1, deadline - time.time()))):
                self._random_delay(2, 5)
                return self.check_login()
        return False

    def _build_search_url(self, query: str, city: str, job_type: str = "") -> str:
        """构建搜索 URL。"""
        from urllib.parse import quote
        city_code = self._get_city_id(city) if city else ""
        self._log("INFO", f"城市: {city}, 编码: {city_code}")
        type_code = ""
        job_type = (job_type or "").strip()
        if job_type:
            type_code = JOB_TYPE_CODES.get(job_type, "")
            if not type_code:
                self._log("WARN", f"求职类型「{job_type}」没有对应的 BOSS 编码，"
                                  f"这次搜索不会带这个筛选（可选：{'、'.join(JOB_TYPE_CODES)}）")
        encoded_query = quote(query, safe="")
        url = "https://www.zhipin.com/web/geek/jobs?"
        if city_code:
            url += f"query={encoded_query}&city={city_code}&industry=&position="
        else:
            url += f"query={encoded_query}&industry=&position="
        if type_code:
            url += f"&jobType={type_code}"
        return url

    def _parse_job_list(self):
        """解析岗位列表。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "浏览器未启动")
            self.jobs = []
            return

        self._log("INFO", "正在解析岗位列表...")

        search_url = self._build_search_url(self._query, self._city, self._job_type)
        self._log("INFO", f"访问搜索页面: {search_url}")
        instance.get(search_url)
        self._random_delay(3, 6)

        self._log("INFO", f"开始滚动 {self._scroll_pages} 次...")
        for i in range(self._scroll_pages):
            if not self.running:
                break
            try:
                instance.scroll.to_bottom()
                self._random_delay(1, 3)
                self._log("INFO", f"已滚动 {i+1}/{self._scroll_pages} 次")
            except Exception:
                self._log("WARN", "页面被刷新，等待页面加载完成后重试...")
                self._random_delay(2, 4)
                try:
                    instance.scroll.to_bottom()
                    self._random_delay(1, 3)
                except Exception:
                    pass

        job_url_elements = instance.eles(SELECTOR_JOB_NAME)
        full_job_urls = []
        for elem in job_url_elements:
            href = elem.attr("href")
            if href:
                if href.startswith("/"):
                    href = "https://www.zhipin.com" + href
                full_job_urls.append(href)

        self._log("INFO", f"共找到 {len(full_job_urls)} 个岗位链接")

        # ── 反爬解码：BOSS直聘用 Unicode 私用区字符（E030-E039）代替数字 ──
        # \ue030→0, \ue031→1, ..., \ue039→9；其他私用区字符（E000-F8FF）跳过
        def _decode_anti_scrape(text):
            """将BOSS直聘的反爬Unicode字符映射回数字。"""
            if not text:
                return text
            result = []
            for ch in text:
                code = ord(ch)
                if 0xe030 <= code <= 0xe039:
                    result.append(str(code - 0xe030))
                elif 0xe000 <= code <= 0xf8ff:
                    # 其他私用区字符直接跳过
                    continue
                else:
                    result.append(ch)
            return ''.join(result)

        processed_jobs = []

        # ── 逆向结果：用精确 CSS 选择器分别提取每个字段 ──
        # 真实结构：div.job-card-wrap > li.job-card-box >
        #   div.job-info > div.job-title > a.job-name + span.job-salary
        #            > ul.tag-list > li (经验/学历)
        #   div.job-card-footer > a.boss-info > span.boss-name (公司名)
        #                      > span.company-location (地区)
        # 关键：岗位名(.job-name)是干净的，反爬字符只在薪资(.job-salary)里。
        # 旧代码用 rec-job-list.texts() 整体解析，把岗位名和薪资合并到同一段，
        # 导致正则无法分离，数字被混入岗位名，薪资只剩"K"。
        job_cards = (
            instance.eles(".job-card-wrap", timeout=5)
            or instance.eles("li.job-card-box", timeout=3)
            or instance.eles(".job-card-wrapper", timeout=3)
            or instance.eles(".job-card-left", timeout=3)
        )
        self._log("INFO", f"找到 {len(job_cards)} 个岗位卡片(.job-card-wrap)")

        if job_cards:
            for idx, card in enumerate(job_cards):
                try:
                    # 岗位名：a.job-name（干净，无反爬字符）
                    name_el = card.ele(".job-name", timeout=1)
                    job_name = _decode_anti_scrape(name_el.text if name_el else "")

                    # 薪资：span.job-salary（反爬Unicode，必须解码）
                    sal_el = card.ele(".job-salary", timeout=1) or card.ele(".salary", timeout=1)
                    salary = _decode_anti_scrape(sal_el.text if sal_el else "")

                    # 标签：.tag-list li（经验、学历等）
                    tag_els = card.eles(".tag-list li", timeout=1)
                    tags = [_decode_anti_scrape(t.text).strip() for t in tag_els if t.text]
                    experience = tags[0] if len(tags) > 0 else ""
                    education = tags[1] if len(tags) > 1 else ""

                    # 公司名：.boss-name（逆向发现不是 .company-name）
                    comp_el = card.ele(".boss-name", timeout=1) or card.ele(".company-name", timeout=1)
                    company = _decode_anti_scrape(comp_el.text if comp_el else "")

                    # 地区：.company-location（逆向发现不是 .job-area）
                    area_el = card.ele(".company-location", timeout=1) or card.ele(".job-area", timeout=1)
                    location = _decode_anti_scrape(area_el.text if area_el else "").strip()

                    # URL：优先从 .job-name 的 href 获取
                    url = ""
                    if name_el:
                        href = name_el.attr("href")
                        if href:
                            url = "https://www.zhipin.com" + href if href.startswith("/") else href
                    if not url:
                        url = full_job_urls[idx] if idx < len(full_job_urls) else ""

                    # 兜底：精确选择器都没拿到时，回退到 texts() 按\n分割
                    if not job_name and not salary and not company:
                        card_texts = card.texts()
                        if card_texts:
                            parts = [_decode_anti_scrape(p) for p in card_texts[0].split("\n")]
                            if parts:
                                first_part = parts[0]
                                salary_pattern = r'(\d+\D{1,2}\d+[Kk]·?\d*薪?|\d+\D{1,2}\d+元/[月天小时]|\d+\D{1,2}\d+[Kk]|\d+K·?\d*薪?)'
                                salary_match = re.search(salary_pattern, first_part)
                                if salary_match:
                                    job_name = first_part[:salary_match.start()].strip()
                                    salary = salary_match.group()
                                else:
                                    job_name = first_part.strip()
                                if len(parts) > 1 and not experience:
                                    experience = parts[1]
                                if len(parts) > 2 and not education:
                                    education = parts[2]
                                if len(parts) > 3 and not company:
                                    company_location_raw = parts[3]
                                    if "·" in company_location_raw:
                                        dot_parts = company_location_raw.split("·")
                                        first_segment = dot_parts[0].strip()
                                        if " " in first_segment:
                                            sp = first_segment.rsplit(" ", 1)
                                            company = sp[0].strip()
                                            location = sp[1].strip() + "·" + "·".join(dot_parts[1:])
                                        else:
                                            company = first_segment
                                            location = "·".join(dot_parts[1:])
                                    elif " " in company_location_raw:
                                        sp = company_location_raw.rsplit(" ", 1)
                                        company = sp[0].strip()
                                        location = sp[1].strip()
                                    else:
                                        company = company_location_raw.strip()

                    # 清理岗位名末尾的破折号
                    job_name = job_name.rstrip("-–—").strip()

                    if idx < 3:
                        self._log("DEBUG", f"解析岗位[{idx}]: name={job_name}, salary={salary}, company={company}, location={location}")

                    processed_jobs.append({
                        "job_name": job_name,
                        "salary": salary,
                        "experience": experience,
                        "education": education,
                        "company": company,
                        "company_location": ((company + " " + location).strip()) if (company or location) else "",
                        "location": location,
                        "url": url,
                        "query": self._query,
                    })
                except Exception as e:
                    self._log("WARN", f"解析卡片 {idx} 失败: {e}")
                    # 失败时用链接兜底
                    if idx < len(full_job_urls):
                        processed_jobs.append({
                            "job_name": "", "salary": "", "url": full_job_urls[idx],
                            "query": self._query,
                        })
        else:
            self._log("INFO", "未找到岗位卡片，回退到 rec-job-list 文本解析")
            rec_list_ele = instance.ele(SELECTOR_REC_JOB_LIST, timeout=3)
            if rec_list_ele:
                job_name_list = rec_list_ele.texts()
                self._log("INFO", f"从 rec-job-list 解析出 {len(job_name_list)} 条文本")
                for idx, job_str in enumerate(job_name_list):
                    parts = job_str.split("\n")
                    if len(parts) < 4:
                        continue
                    parts = [_decode_anti_scrape(p) for p in parts]
                    first_part = parts[0]
                    if idx < 3:
                        self._log("DEBUG", f"解码后文本[{idx}]: parts={parts}")

                    salary_pattern = r'(\d+\D{1,2}\d+[Kk]·?\d*薪?|\d+\D{1,2}\d+元/[月天小时]|\d+\D{1,2}\d+[Kk]|\d+K·?\d*薪?)'
                    salary_match = re.search(salary_pattern, first_part)
                    if salary_match:
                        job_name = first_part[:salary_match.start()].strip()
                        salary = salary_match.group()
                    else:
                        job_name = first_part.strip()
                        salary = ""
                        if len(parts) > 1:
                            second_part = parts[1].strip()
                            salary_match2 = re.search(salary_pattern, second_part)
                            if salary_match2:
                                salary = salary_match2.group()
                            elif second_part and ("K" in second_part or "元" in second_part):
                                salary = second_part

                    job_name = job_name.rstrip("-–—").strip()
                    company_location = parts[3] if len(parts) > 3 else ""
                    company = ""
                    location = ""
                    if "·" in company_location:
                        dot_parts = company_location.split("·")
                        first_segment = dot_parts[0].strip()
                        if " " in first_segment:
                            sp = first_segment.rsplit(" ", 1)
                            company = sp[0].strip()
                            location = sp[1].strip() + "·" + "·".join(dot_parts[1:])
                        else:
                            company = first_segment
                            location = "·".join(dot_parts[1:])
                    elif " " in company_location:
                        sp = company_location.rsplit(" ", 1)
                        company = sp[0].strip()
                        location = sp[1].strip()
                    else:
                        company = company_location.strip()

                    processed_jobs.append({
                        "job_name": job_name,
                        "salary": salary,
                        "experience": parts[1] if len(parts) > 1 else "",
                        "education": parts[2] if len(parts) > 2 else "",
                        "company": company,
                        "company_location": company_location,
                        "location": location,
                        "url": full_job_urls[idx] if idx < len(full_job_urls) else "",
                        "query": self._query,
                    })
            else:
                self._log("INFO", "未找到 rec-job-list，直接使用链接")
                for u in full_job_urls:
                    processed_jobs.append({
                        "job_name": "", "salary": "", "url": u, "query": self._query
                    })

        self._log("INFO", f"解析出 {len(processed_jobs)} 条岗位信息")
        self.jobs = processed_jobs

    def _get_city_id(self, city_name: str) -> str:
        """获取城市编码。优先使用 API 捕获数据，再使用硬编码映射。"""
        if self._city_dict and city_name in self._city_dict:
            code = self._city_dict[city_name]
            self._log("INFO", f"城市 {city_name} 编码 (API): {code}")
            return str(code)
        if city_name in CITY_CODES:
            self._log("INFO", f"城市 {city_name} 编码 (硬编码): {CITY_CODES[city_name]}")
            return CITY_CODES[city_name]
        self._log("WARN", f"未找到城市 {city_name} 的编码")
        return ""


    def _init_ai(self):
        """初始化 AI 分析器（懒加载）。"""
        if self._ai_analyzer is not None:
            return self._ai_analyzer
        if not self._ai_enabled:
            return None
        try:
            if self._ai_providers:
                self._ai_analyzer = AIAnalyzerChain(
                    providers=self._ai_providers,
                    match_threshold=self._ai_threshold,
                    log_callback=lambda msg: self._log("INFO", msg),
                    custom_filter_keywords=self._ai_custom_filter_keywords,
                    custom_scoring_prompt=self._ai_custom_scoring_prompt,
                    skip_unhealthy=self._ai_skip_unhealthy,
                    analyze_max_tokens=self._analyze_max_tokens,
                    fail_action=self._ai_fail_action,
                    veto_only_match=self._ai_veto_only,
                )
            else:
                self._log("WARN", "AI 已启用但未配置任何 provider")
                return None
            self._ai_analyzer.set_resume(self._resume_cfg)
            self._log("INFO", f"🤖 AI 智能解析已启用（阈值: {self._ai_threshold}）")
            return self._ai_analyzer
        except Exception as e:
            self._log("WARN", f"AI 分析器初始化失败（降级为普通投递）: {e}")
            return None

    def _bind_ai_result(self, job: dict, result: dict, analyzer=None,
                        duration: float = 0):
        """把这条岗位的判分结果与调用现场挂到 job 上，记录/推送/追问都从这里取。

        同时更新 self._last_ai_*：那份是"最后一次调用"的调试视图（e2e 脚本在读），
        但落库不再用它——否则下一条岗位会把上一条的分数和模型名带进记录。
        """
        meta = {
            "model": getattr(analyzer, "last_model_name", "") or "",
            "system_prompt": getattr(analyzer, "last_system_prompt", None),
            "user_prompt": getattr(analyzer, "last_user_prompt", None),
            "raw_response": getattr(analyzer, "last_raw_response", None),
            "duration_ms": int((duration or 0) * 1000),
        }
        job["_ai_result"] = result or {}
        job["_ai_meta"] = meta
        self._last_ai_result = job["_ai_result"]
        self._last_ai_model = meta["model"]
        self._last_ai_system_prompt = meta["system_prompt"]
        self._last_ai_user_prompt = meta["user_prompt"]
        self._last_ai_raw_response = meta["raw_response"]
        self._last_ai_duration_ms = meta["duration_ms"]

    def _analyze_job_with_ai(self, job: dict):
        """用 AI 分析岗位匹配度。返回 (匹配结果, 耗时秒数)，未启用时返回 (None, 0)。"""
        analyzer = self._init_ai()
        if not analyzer:
            # 分析器都建不起来同样属于「AI 不可用」，不能当成不匹配把岗位全丢掉
            result = {"score": 50, "is_match": True, "ai_error": True,
                      "reason": "AI 分析器初始化失败，按默认通过", "suggested_greeting": ""}
            self._bind_ai_result(job, self._apply_fail_action(result))
            return result, 0
        ai_job = {
            "job_name": job.get("job_name", ""),
            "salary": job.get("salary", ""),
            "description": job.get("description", job.get("jd_description", "")),
            "requirements": job.get("requirements", job.get("jd_requirements", "")),
            "company": job.get("company", job.get("company_location", "")),
            "url": job.get("url", ""),
        }
        try:
            _start = time.time()
            result = analyzer.analyze_job(ai_job)
            duration = time.time() - _start
            score = result.get("score", 50)
            is_match = result.get("is_match", True)
            # 判分结果绑到这条岗位上（见 _bind_ai_result）
            self._bind_ai_result(job, result, analyzer, duration)
            self._log("INFO", f"🤖 AI 匹配度: {score}/100 ({duration:.1f}s) —— {result.get('reason', '')[:80]}")
            if result.get("ai_error"):
                # AI 没给出判断（接口全挂/无法解析）≠ AI 判定不匹配，按配置的
                # fail_action 处置：default 放行，skip 不投
                if not result.get("is_match", True):
                    self._log("WARN", "⚠️ AI 未能给出判断，按配置跳过本岗位，不做盲投")
                    return None, duration
                self._log("WARN", "⚠️ AI 未能给出判断，本轮按默认通过继续打招呼")
                return result, duration
            return (result, duration) if (is_match and score >= self._ai_threshold) else (None, duration)
        except Exception as e:
            self._log("WARN", f"AI 分析异常，按配置处置: {e}")
            result = {"score": 50, "is_match": True, "ai_error": True,
                      "reason": f"AI 分析异常: {e}", "suggested_greeting": ""}
            self._bind_ai_result(job, self._apply_fail_action(result))
            return result, 0

    def _apply_fail_action(self, result: dict) -> dict:
        """AI 没给出判断时按配置决定放行还是跳过（默认放行，与历史行为一致）。"""
        if self._ai_fail_action == "skip":
            result["is_match"] = False
            result["score"] = 0
        return result


    def _handle_disconnect(self) -> bool:
        """页面断开时的恢复：只把本账号的岗位页导航回来，不动整个浏览器。

        为什么不 close()+launch()：浏览器是打招呼线程和回复线程共享的，回复侧正握着
        _chat_tab，从打招呼侧关掉它等于把别人的标签页一起带走；重建浏览器归
        UnifiedBotLoop._try_reconnect_browser 管，那里才有 _reconnect_lock 串行化。
        为什么不再导航到 about:blank：旧实现导航到空白页之后就 return True 说"恢复成功"，
        窗口于是停在一片白 —— 用户报的"第二个浏览器打开后无内容"就是这个现场。
        起不来就如实返回 False，让上层走带锁的重连，不要拿着死对象继续跑。
        """
        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "页面断开时浏览器实例已不存在")
            return False

        targets = []
        try:
            targets.append(self._build_search_url(self._query, self._city, self._job_type))
        except Exception:
            pass
        targets.append("https://www.zhipin.com")

        for url in targets:
            try:
                instance.get(url)
                self._random_delay(1, 2)
                landed = instance.url or ""
                if landed:
                    self._log("INFO", f"页面断开已恢复到: {landed[:70]}")
                    return True
                self._log("WARN", f"导航后读不到 URL，换下一个落点: {url[:36]}")
            except Exception as e:
                self._log("WARN", f"断开恢复导航失败({url[:36]}): {str(e)[:60]}")

        self._log("ERROR", "页面断开后两个落点都没起来，交给重连流程处理")
        return False

    def _apply_job(self, job: dict, _disconnect_retry: int = 0):
        """投递一个岗位。

        Returns:
            (success, fail_reason) 元组：
            - 成功: (True, "")
            - 失败: (False, 具体失败原因字符串)
        """
        with self._apply_lock:
            return self._apply_job_inner(job, _disconnect_retry)

    def greet_cooldown_left(self, now=None) -> float:
        """打招呼还剩几秒冷却（不在冷却中返回 0.0）。

        轮次要在岗位循环之前就问这一句：闸门放在点「沟通」那一步，
        一轮里每个岗位都要跑完搜索→详情→判分才撞上它，白烧记录也白烧时间。
        """
        return max(0.0, self._greet_cooldown_until - (now or time.time()))

    def _apply_job_inner(self, job: dict, _disconnect_retry: int = 0):
        """实际投递逻辑（内部方法）。

        Returns:
            (success, fail_reason) 元组：
            - 成功: (True, "")
            - 失败: (False, 具体失败原因字符串)
        """
        if not self.running:
            return False, "运行已停止"
        url = job.get("url", "")
        if not url:
            return False, "岗位URL为空"

        # 冷却期内不再跑"搜索→详情→点沟通"这一整套：连着几次都停在同一个地方，
        # 说明卡的是账号层面的额度，不是这个岗位
        left = self.greet_cooldown_left()
        if left > 0:
            return False, (f"打招呼已冷却（连续 {self._no_drawer_streak} 次点了「立即沟通」"
                           f"没出聊天抽屉，疑似本号当日沟通额度用完），"
                           f"{int(left // 60) + 1} 分钟后重试")

        # 招呼语空缺必须在这里拦住，不能等到输入框那步：BOSS 点「沟通」本身就等于
        # 发起招呼（第二种机制还会立刻自动发平台预设文案），先点再发现没配就晚了。
        # 这里不打日志：原因串会原样返回给 send_greeting，那边"⏭️ 跳过: 岗位（原因:…）"
        # 已经带上同一句话，两边各打一行会让日志行数变成记录条数的两倍
        greeting, greeting_source = self._greeting_for(job)
        if not (greeting or "").strip():
            return False, GREETING_MISSING_REASON

        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "浏览器未启动")
            return False, "浏览器未启动"

        # 如果页面已断开，先尝试恢复
        if _disconnect_retry == 0:
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "页面已断开，尝试恢复...")
                if self._handle_disconnect():
                    instance = self.browser_manager.get_instance()
                    _disconnect_retry = 1
                else:
                    return False, "页面断开且恢复失败"

        try:
            message_sent = False  # 跟踪消息是否已发送
            # ── 1. 导航到岗位详情页 ──
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "导航前页面已断开，尝试恢复...")
                if not self._handle_disconnect():
                    return False, "导航前页面断开且恢复失败"
                instance = self.browser_manager.get_instance()

            for _retry in range(self._retry_max_attempts):
                try:
                    self._log("INFO", f"导航到: {url}")
                    # json.dumps 生成的是转义好的 JS 字符串字面量：岗位 URL 里出现
                    # 单引号时手工拼 '...' 会截断语句，等于把页面 DOM 内容当代码执行
                    instance.run_js(f"window.location.href = {json.dumps(url)}")
                    self._random_delay(3, 6)
                    self._log("INFO", f"导航后URL: {instance.url}")
                    break
                except Exception as _e:
                    self._log("WARN", f"页面加载重试: {_e}")
                    self._random_delay(2, 4)
            else:
                self._log("WARN", "页面加载失败，跳过此岗位")
                return False, "页面加载失败"

            # 检查是否被重定向到登录页
            try:
                current_url = instance.url
                if "passport" in current_url or "login" in current_url:
                    self._log("WARN", "访问岗位详情时被重定向到登录页")
                    if self._login_required_cb:
                        self._login_required_cb()
                    self._log("INFO", "请重新登录，登录后点击「确认登录」")
                    if not self._wait_for_login():
                        self._log("ERROR", "登录超时")
                        return False, "登录超时"
                    self._save_cookies()
                    self._log("SUCCESS", "登录成功")
                    try:
                        instance.get(url)
                        self._random_delay(3, 6)
                    except Exception:
                        return False, "登录后重新访问岗位失败"
            except Exception:
                self._log("WARN", "页面断开")
                return False, "页面断开"

            # ── 2. 查找沟通按钮 ──
            chat_btn = self._find_chat_button(timeout=8)
            if chat_btn is None:
                reason, chat_btn = self._explain_missing_chat_button(instance, url, job)
                if chat_btn is None:
                    return False, reason

            btn_text = chat_btn.text
            if "继续沟通" in btn_text:
                self._log("INFO", "该岗位之前已投递过（继续沟通），跳过")
                return False, "该岗位之前已投递过（继续沟通）"

            # ── 3. 获取 JD 信息 ──
            job_description = ""
            job_requirements = ""
            try:
                job_desc_elem = instance.ele(".job-sec-text", timeout=3)
                if job_desc_elem:
                    job_description = job_desc_elem.text
                    self._log("INFO", "岗位描述: " + job_description[:100] + "...")
            except Exception as e:
                self._log("DEBUG", f"读取岗位描述失败: {e}")
            try:
                req_elem = instance.ele(".requirements", timeout=2)
                if req_elem:
                    job_requirements = req_elem.text
            except Exception as e:
                self._log("DEBUG", f"读取任职要求失败: {e}")
            if not job_description and not job_requirements:
                # JD 是 AI 判分的主要依据，取不到时只凭标题/薪资很容易判成"不匹配"，
                # 事后翻日志必须能看出是这一类原因，而不是 AI 乱打分
                self._log("WARN", f"⚠️ 详情页未取到 JD（{job.get('job_name', '')}），"
                                  f"AI 仅按标题/薪资判分，跳过原因可能失真")
            if not job_requirements:
                job_requirements = job_description
            job["jd_description"] = job_description
            job["jd_requirements"] = job_requirements
            self._log("INFO", f"JD 描述长度: {len(job_description)} 字符")

            # ── 4. 点击立即沟通 ──
            self._log("INFO", "开始点击沟通按钮...")
            chat_btn = instance.ele(".btn btn-startchat", timeout=5)
            if not chat_btn:
                self._log("WARN", "未找到沟通按钮!")
                return False, "未找到沟通按钮（点击阶段）"
            self._log("INFO", f"找到沟通按钮，文本: {chat_btn.text}")
            # 记录点击前的标签页ID集合，用于识别新打开的标签页
            browser = instance._get_browser() if hasattr(instance, '_get_browser') else None
            pre_tab_ids = set(browser.tab_ids) if browser else set()
            chat_btn.click()
            self._log("INFO", "已点击沟通按钮，等待输入框...")
            # 先问一次结局，别急着死等：抽屉弹出来了 / 平台自己发了 / 还看不出来
            drawer_ready = self._wait_drawer_or_dialog(instance)
            if drawer_ready == "dialog":
                handled = self._auto_greet_path(instance, job, greeting)
                if handled is not None:
                    return handled
            if not drawer_ready:
                # 等待聊天窗口加载（参考原项目auto_boss: timeout=10秒）
                # 关键修复：BOSS直聘点击"立即沟通"后聊天窗口为页面内弹出层（popup），
                # 弹出层加载比新标签页慢，需要更长等待时间。从5-8秒增加到8-12秒。
                self._random_delay(8, 12)

            # 关键修复：不依赖弹窗容器检测，直接尝试查找输入框。
            # BOSS直聘的弹窗可能用各种 class 名，硬编码检测列表容易漏判。
            # 弹窗检测只作为辅助日志，不影响后续输入框查找流程。
            # 如果能找到输入框，说明弹窗已弹出；找不到再检查风控/验证弹窗。
            chat_popup_selectors = ([] if drawer_ready else [
                ".chat-container", ".chat-popup", ".chat-modal", ".drawer",
                ".modal-content", ".message-input", "#chat-input", ".chat-input",
                ".input-area", ".chat-footer",
            ])
            popup_detected = False
            for popup_sel in chat_popup_selectors:
                try:
                    popup = instance.ele(popup_sel, timeout=2)
                    if popup:
                        self._log("DEBUG", f"检测到聊天弹窗容器: {popup_sel}")
                        popup_detected = True
                        break
                except Exception:
                    pass
            if not popup_detected and not drawer_ready:
                # 辅助日志：未检测到弹窗容器，但不中断流程，继续尝试查找输入框
                self._log("INFO", "未检测到聊天弹窗容器（不影响流程，将继续查找输入框）")
                # 检查是否有风控/验证弹窗（仅记录日志，不中断）
                for risk_sel in [".error-tip", ".verify-modal", ".security-tip", ".risk-modal", ".captcha"]:
                    try:
                        risk_elem = instance.ele(risk_sel, timeout=1)
                        if risk_elem:
                            self._log("WARN", f"检测到风控/验证弹窗: {risk_sel}，文本: {risk_elem.text[:100] if risk_elem.text else ''}")
                    except Exception:
                        pass

            # 尝试获取新打开的聊天标签页（BOSS 点"沟通"后通常新开标签页）
            # 关键修复：新标签页严格通过 browser_manager.get_greet_chat_tab() 管理，
            # 与回复引擎的 _chat_tab 严格区分，避免抢占。
            chat_tab = None
            if browser:
                try:
                    # 优先通过 tab_ids 差检新打开的标签页（比 latest_tab 更可靠）
                    # latest_tab 返回最新激活的标签页，但可能恰好是回复引擎的 _chat_tab
                    post_tab_ids = set(browser.tab_ids)
                    new_tab_ids = post_tab_ids - pre_tab_ids
                    # 先检查新打开的标签页
                    for tid in new_tab_ids:
                        try:
                            t = browser.get_tab(tid)
                            t_url = t.url or ""
                            self._log("DEBUG", f"新标签页: {t_url}")
                            if "chat" in t_url or "message" in t_url:
                                chat_tab = t
                                self._log("INFO", f"识别到新打开的聊天标签页: {t_url}")
                                break
                        except Exception:
                            pass
                    # 关键修复：删除 latest_tab 回退逻辑。
                    # latest_tab 返回最近激活的标签页，如果回复引擎刚操作过 _chat_tab，
                    # latest_tab 就会返回回复引擎的聊天标签页，导致打招呼引擎在回复标签页上发消息。
                    # 如果 tab_ids 差集没找到新标签页，chat_tab 保持 None，
                    # 后续会尝试在当前搜索标签页(instance)上查找输入框（BOSS可能in-page导航）。
                    if chat_tab is None:
                        self._log("DEBUG", "tab_ids 差集未找到新聊天标签页，将检查当前搜索标签页是否in-page导航")
                except Exception as e:
                    self._log("DEBUG", f"获取新打开标签页失败: {e}")

            # 将识别到的聊天标签页注册到 browser_manager._greet_chat_tab
            # 这样后续 close_greet_chat_tab() 可以精确关闭它，不影响回复引擎的 _chat_tab
            if chat_tab is not None and self.browser_manager is not None:
                try:
                    from boss_bot.browser_launcher import BrowserInstance, _IS_MACOS
                    # 包装为 BrowserInstance 并注册到 browser_manager
                    self.browser_manager._greet_chat_tab = BrowserInstance(
                        chrome_page=chat_tab if not _IS_MACOS else None,
                        chromium=browser if _IS_MACOS else None,
                        tab=chat_tab if _IS_MACOS else None,
                    )
                    self._log("DEBUG", "已将聊天标签页注册到 browser_manager._greet_chat_tab")
                except Exception as e:
                    self._log("DEBUG", f"注册聊天标签页失败: {e}")

            # ── 5. 输入消息 ──
            # 来源在点沟通之前就已经定下来了（空缺根本走不到这里）
            self._greeting_source = greeting_source
            # 保存实际发送的打招呼语供 GreetRecord 记录使用
            job["_actual_greeting_sent"] = greeting
            self._log("INFO", f"打招呼语来源: {self._greeting_source}, 内容: {greeting[:50]}...")
            # 等待聊天窗口加载
            self._random_delay(3, 5)

            # 检查是否有弹窗阻止（简历弹窗等），自动关闭
            try:
                for popup_sel in [".panel-resume", ".sentence-popover", ".dialog-content", ".modal-content", ".resume-guide"]:
                    popup = instance.ele(popup_sel, timeout=1)
                    if popup:
                        self._log("WARN", f"检测到弹窗: {popup_sel}，尝试关闭...")
                        try:
                            close = instance.ele(".icon-close", timeout=1)
                            if close:
                                close.click()
                                self._random_delay(1, 2)
                        except Exception:
                            pass
            except Exception:
                pass

            # ── 查找聊天输入框 ──
            # 关键修复：BOSS直聘点击"立即沟通"后，聊天窗口是**页面内弹出层（popup）**，不是新标签页。
            # 查找顺序调整为：①当前页面 → ②当前页面iframe → ③新标签页 → ④遍历所有标签页
            # 严格参考原项目 auto_boss: self.dp.ele(".input-area", timeout=10)
            input_area = None
            greet_chat_instance = None  # 打招呼专用的临时聊天 BrowserInstance

            # 统一的输入框选择器列表（按优先级排序）
            # BOSS直聘聊天页面输入框实际是 #chat-input（contenteditable div，class=chat-input）
            # 扩展选择器覆盖各种可能的聊天输入框形态
            chat_input_selectors = [
                "#chat-input",
                ".chat-input",
                ".input-area",
                'textarea[placeholder*="回复"]',
                'textarea[placeholder*="输入"]',
                ".message-input textarea",
                ".chat-footer textarea",
                "tag:textarea",
                "[contenteditable=true]",
                'div[contenteditable="true"]',
            ]

            # 关键修复：增加重试机制（次数按 greet.retry.max_attempts 配置，递增等待）
            # 日志显示"未找到输入框"时URL还在job_detail页面，
            # 说明弹窗可能延迟弹出，需要重试查找
            _input_attempts = input_lookup_attempts(drawer_ready, self._retry_max_attempts)
            for _input_retry in range(_input_attempts):
                if input_area:
                    break
                if not self.running:
                    return False, "运行已停止"
                # 验证页一直等不到输入框，重试循环会空转几分钟；认出来就直接交给人工
                if self._on_captcha_page(instance):
                    return False, ("BOSS 弹出人机验证，需要人工在浏览器窗口完成"
                                   "（超过 60 秒未处理会自动跳过）")
                if _input_retry > 0:
                    _retry_wait = 3 + _input_retry * 2  # 第2次等5秒，第3次等7秒
                    self._log("INFO", f"输入框查找重试 {_input_retry+1}/{_input_attempts}，等待 {_retry_wait} 秒...")
                    self._interruptible_sleep(_retry_wait)

                # ① 优先在当前页面查找输入框（BOSS点击沟通后通常在当前页面弹出聊天窗口）
                # 关键修复2：参考原项目auto_boss，点击沟通后聊天窗口在当前页面弹出（不新开标签页），
                # URL不变，所以不检查URL是否含"chat"，直接在当前页面查找输入框
                try:
                    for sel in chat_input_selectors:
                        try:
                            input_area = instance.ele(sel, timeout=3)
                            if input_area:
                                self._log("INFO", f"在当前页面找到输入框: {sel}")
                                greet_chat_instance = instance
                                break
                        except Exception:
                            pass
                except Exception:
                    pass

                # ② 当前页面没找到，遍历当前页面的所有iframe查找
                # 关键修复：BOSS直聘聊天输入框可能在iframe中，日志显示"发现 2 个iframe"
                if not input_area:
                    try:
                        iframes = instance.eles("tag:iframe", timeout=2)
                        if iframes:
                            self._log("INFO", f"当前页面发现 {len(iframes)} 个iframe，尝试在iframe中查找输入框")
                            for iframe in iframes:
                                try:
                                    for sel in chat_input_selectors:
                                        try:
                                            input_area = iframe.ele(sel, timeout=3)
                                            if input_area:
                                                self._log("INFO", f"在当前页面iframe中找到输入框: {sel}")
                                                greet_chat_instance = instance
                                                break
                                        except Exception:
                                            pass
                                    if input_area:
                                        break
                                except Exception:
                                    pass
                    except Exception as e:
                        self._log("DEBUG", f"当前页面iframe查找失败: {e}")

                # ③ 当前页面和iframe都没找到，再在新打开的聊天标签页中查找（如果有新标签页）
                # 关键修复：不修改 instance._page/_tab（那会破坏搜索标签页），
                # 而是创建一个独立的 BrowserInstance 包装 chat_tab 用于操作
                if not input_area and chat_tab:
                    try:
                        from boss_bot.browser_launcher import BrowserInstance, _IS_MACOS
                        greet_chat_instance = BrowserInstance(
                            chrome_page=chat_tab if not _IS_MACOS else None,
                            chromium=browser if _IS_MACOS else None,
                            tab=chat_tab if _IS_MACOS else None,
                        )
                        for sel in chat_input_selectors:
                            try:
                                input_area = greet_chat_instance.ele(sel, timeout=5)
                                if input_area:
                                    self._log("INFO", f"在聊天标签页找到输入框: {sel}")
                                    break
                            except Exception:
                                pass
                        if not input_area:
                            # 重置 greet_chat_instance，避免误用未找到输入框的实例
                            greet_chat_instance = None
                    except Exception as e:
                        self._log("DEBUG", f"在聊天标签页查找输入框失败: {e}")
                        greet_chat_instance = None

                # ④ 最后兜底：遍历所有标签页查找（不过滤URL）
                # 关键：排除回复引擎专用的 _chat_tab，避免抢占
                if not input_area:
                    try:
                        browser = instance._get_browser() if hasattr(instance, '_get_browser') else None
                        if browser:
                            all_tabs = browser.tab_ids
                            # 获取回复引擎 _chat_tab 的 tab_id，用于排除
                            # 关键修复：使用 browser_manager.get_reply_tab_id() 方法替代内联获取，
                            # 统一排除逻辑，避免多处重复代码导致不一致
                            reply_chat_tab_id = None
                            if self.browser_manager is not None:
                                reply_chat_tab_id = self.browser_manager.get_reply_tab_id()
                            if reply_chat_tab_id:
                                self._log("DEBUG", f"回复引擎专用标签页 tab_id: {reply_chat_tab_id}，遍历时将排除")
                            if len(all_tabs) > 1:
                                self._log("INFO", f"当前页面未找到输入框，遍历 {len(all_tabs)} 个标签页（排除回复引擎标签页）")
                                for tab_id in all_tabs:
                                    # 跳过回复引擎的 _chat_tab，避免打招呼引擎抢占
                                    if reply_chat_tab_id and tab_id == reply_chat_tab_id:
                                        self._log("DEBUG", "  跳过回复引擎专用标签页")
                                        continue
                                    try:
                                        tab = browser.get_tab(tab_id)
                                        tab_url = tab.url
                                        self._log("DEBUG", f"  检查标签页: {tab_url}")
                                        # 不过滤URL，在每个标签页中尝试查找输入框
                                        for sel in chat_input_selectors:
                                            try:
                                                input_area = tab.ele(sel, timeout=3)
                                                if input_area:
                                                    # 守护日志：确认找到输入框的标签页不是回复引擎的标签页
                                                    if reply_chat_tab_id:
                                                        current_tab_id = getattr(tab, 'tab_id', None) or getattr(tab, '_tab_id', None)
                                                        if current_tab_id == reply_chat_tab_id:
                                                            self._log("ERROR", "严重BUG：打招呼引擎试图使用回复引擎标签页！跳过此标签页。")
                                                            input_area = None
                                                            continue
                                                    self._log("INFO", f"在标签页 {tab_url} 中找到输入框: {sel}")
                                                    # 包装为 greet_chat_instance，不修改 instance
                                                    try:
                                                        from boss_bot.browser_launcher import BrowserInstance, _IS_MACOS
                                                        greet_chat_instance = BrowserInstance(
                                                            chrome_page=tab if not _IS_MACOS else None,
                                                            chromium=browser if _IS_MACOS else None,
                                                            tab=tab if _IS_MACOS else None,
                                                        )
                                                        # 同步注册到 browser_manager
                                                        if self.browser_manager is not None:
                                                            self.browser_manager._greet_chat_tab = greet_chat_instance
                                                    except Exception:
                                                        pass
                                                    break
                                            except Exception:
                                                pass
                                        if input_area:
                                            break
                                    except Exception:
                                        pass
                    except Exception as e:
                        self._log("DEBUG", f"标签页遍历失败: {e}")

            if not input_area:
                handled = self._auto_greet_path(instance, job, greeting)
                if handled is not None:
                    return handled
                snap = self._chat_snapshot(instance)
                reason = chat_failure_reason(snap)
                self._log("WARN", f"未找到输入框｜{reason}")
                if reason.startswith(NO_DRAWER_REASON):
                    self._no_drawer_streak += 1
                    if self._no_drawer_streak >= NO_DRAWER_STREAK_LIMIT:
                        self._greet_cooldown_until = time.time() + NO_DRAWER_COOLDOWN_SEC
                        self._log("WARN",
                                  f"连续 {self._no_drawer_streak} 次点了「立即沟通」都不出聊天抽屉，"
                                  f"疑似本号当日沟通额度已用完 —— 打招呼冷却 "
                                  f"{NO_DRAWER_COOLDOWN_SEC // 60} 分钟，别整夜白试；"
                                  f"想立刻确认就在浏览器窗口里手动点一次「立即沟通」")
                else:
                    self._no_drawer_streak = 0
                # 看得见的类名才值得念一遍；隐藏的登录模板只报个数量，
                # 否则日志里全是"ipt-phone"，看着就像真掉了登录态
                raw_inputs = list(snap.get("inputs") or [])
                shown = [str(i.get("cls") if isinstance(i, dict) else i)
                         for i in raw_inputs
                         if not isinstance(i, dict) or i.get("visible")]
                hidden = len(raw_inputs) - len(shown)
                self._log("WARN", f"  现场 url={str(snap.get('url'))[:80]} "
                                  f"抽屉元素={snap.get('chat_elements') or '无'} "
                                  f"可见输入框={shown[:8]} 隐藏模板输入框={hidden} 个")
                if reason == CAPTCHA_REASON:
                    # 认出来了就得等人工：60 秒时限、到点算一次、连续三次停轮都在闸门里。
                    # 过完验证也不在这里补点「沟通」——抽屉是平台弹的，重演一次点击
                    # 可能让同一个 HR 收到两条招呼，这个岗位留给下一轮。
                    self._captcha_handoff()
                if "登录" in reason:
                    self._log("WARN", "登录态已失效，之后每个岗位都会卡在同一个地方，"
                                      "请先在浏览器窗口里重新登录 BOSS")
                return False, reason
            self._log("INFO", "找到输入框，输入消息...")
            input_area.input(greeting)
            self._log("INFO", "消息已输入")

            # ── 6. 点击发送 ──
            # 关键修复：发送按钮在聊天标签页中查找，不在搜索标签页(instance)中
            # 优先使用 greet_chat_instance（打招呼专用临时标签页），回退到 instance
            send_search_instance = greet_chat_instance if greet_chat_instance else instance
            message_sent = False
            try:
                # 尝试多个发送按钮选择器
                send_btn = None
                for send_sel in [".btn-send", ".btn-v2.btn-sure-v2.btn-send", ".send-message", "tag:button@@type=submit", ".chat-send"]:
                    try:
                        send_btn = send_search_instance.ele(send_sel, timeout=3)
                        if send_btn:
                            self._log("INFO", f"找到发送按钮: {send_sel}")
                            break
                    except Exception:
                        pass
                if send_btn:
                    send_btn.click()
                    message_sent = True
                else:
                    self._log("WARN", "未找到发送按钮，尝试按回车发送")
                    input_area.input("\n")
                    message_sent = True
            except Exception as send_e:
                self._log("WARN", f"点击发送按钮失败: {send_e}")
                # 尝试按回车发送
                try:
                    input_area.input("\n")
                    message_sent = True
                except Exception:
                    pass
            self._random_delay(1, 2)

            # 消息已经发出去了，先落库+推前端：下面还有图片上传、关弹窗、
            # 关临时标签页，全跑完要 5~30 秒，那期间界面看不到投递结果
            self._mark_chatted(job)
            self._record_sent_now(job)

            # 发送后检测页面是否断开
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "发送消息后页面连接断开，但消息可能已发送成功")
                self._mark_chatted(job)
                # 即使页面断开，也要尝试关闭临时聊天标签页
                if self.browser_manager is not None:
                    try:
                        self.browser_manager.close_greet_chat_tab()
                    except Exception:
                        pass
                return True, ""

            # ── 7. 发送图片 ──
            self._send_images_after_message()

            # ── 清理状态 ──
            # 关键修复：在聊天标签页中关闭弹窗，不在搜索标签页中
            try:
                close_btn = send_search_instance.ele(".icon-close", timeout=2)
                if close_btn:
                    close_btn.click()
                    self._random_delay(1, 2)
            except Exception:
                pass

            # ── 关闭打招呼专用的临时聊天标签页，回到搜索标签页 ──
            # 关键修复：通过 browser_manager.close_greet_chat_tab() 精确关闭临时标签页，
            # 不影响回复引擎的 _chat_tab，也不关闭搜索标签页
            if greet_chat_instance is not None and self.browser_manager is not None:
                try:
                    self.browser_manager.close_greet_chat_tab()
                    self._random_delay(1, 2)
                    self._log("INFO", "已关闭打招呼临时聊天标签页，回到搜索标签页")
                except Exception as e:
                    self._log("DEBUG", f"关闭打招呼临时标签页异常: {e}")
            elif greet_chat_instance is not None:
                # 兜底：直接关闭
                try:
                    greet_chat_instance.close_current_tab()
                    self._random_delay(1, 2)
                except Exception:
                    pass

            return True, ""

        except Exception as e:
            self._log("WARN", "发送消息异常: " + str(e))
            import traceback
            self._log("WARN", traceback.format_exc())
            # 异常路径也要清理临时聊天标签页，避免标签页累积
            if self.browser_manager is not None:
                try:
                    self.browser_manager.close_greet_chat_tab()
                except Exception:
                    pass
            # 如果消息已发送但后续步骤出错，仍标记为成功
            if message_sent:
                self._log("WARN", "消息已发送但后续步骤出错，标记为成功")
                self._mark_chatted(job)
                return True, ""
            return False, f"发送消息异常: {e}"

    def _send_images_after_message(self):
        """在发送消息之后上传图片。"""
        instance = self.browser_manager.get_instance()
        if instance is None or not self._image_files:
            return

        # 先关闭当前聊天窗口，再重新打开
        try:
            close_btn = instance.ele(".icon-close", timeout=2)
            if close_btn:
                close_btn.click()
                self._random_delay(1, 2)
        except Exception:
            pass

        # 检查聊天窗口是否打开
        input_area = None
        try:
            input_area = instance.ele(".input-area", timeout=10)
        except Exception:
            pass

        if not input_area:
            self._log("INFO", "聊天窗口未打开，尝试重新打开...")
            chat_btn = self._find_chat_button(timeout=5)
            if chat_btn:
                try:
                    chat_btn.click()
                    self._random_delay(1, 2)
                except Exception as e:
                    self._log("WARN", f"重新打开聊天窗口失败: {e}")
                    return
            else:
                # 这句话以前和"投递时找不到按钮"共用一套词，看记录的人以为岗位
                # 没投出去；实际招呼语已经发了，只是图片没补上
                self._log("WARN", "招呼语已发出，但回不到会话窗口，图片未上传")
                return

        # 上传图片 - 去重后依次上传
        seen = set()
        for img_path in self._image_files:
            if img_path in seen:
                continue
            seen.add(img_path)
            if os.path.isfile(img_path):
                abs_path = os.path.abspath(img_path)
                uploaded = self._upload_image(abs_path)
                if uploaded:
                    self._log("INFO", "已上传图片: " + os.path.basename(img_path))
                else:
                    self._log("WARN", "上传图片失败: " + os.path.basename(img_path))
                self._random_delay(1, 2)

    def _find_chat_button(self, timeout=5):
        """查找沟通按钮。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return None
        time.sleep(1)

        # 严格参考源文件：.btn btn-startchat（DrissionPage AND 语法）
        try:
            btn = instance.ele(".btn btn-startchat", timeout=timeout)
            if btn:
                return btn
        except Exception:
            pass

        # 备选：文本匹配
        for chat_text in ("立即沟通", "继续沟通"):
            try:
                btn = instance.ele(f"text:{chat_text}", timeout=2)
                if btn:
                    return btn
            except Exception:
                pass

        return None

    def _upload_image(self, img_path):
        """上传单张图片。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return False
        if not os.path.isfile(img_path):
            self._log("WARN", f"上传图片文件不存在: {img_path}")
            return False

        abs_path = os.path.abspath(img_path)

        # 优先：set.upload_files()
        try:
            instance.set.upload_files(abs_path)
            instance.wait.upload_paths_inputted()
            self._random_delay(2, 3)
            return True
        except Exception:
            pass

        # 备选1：直接找 input[type=file]
        try:
            file_input = instance.ele("tag:input@@type=file", timeout=3)
            if file_input:
                file_input.input(abs_path)
                self._random_delay(2, 3)
                return True
        except Exception:
            pass

        # 备选2：click.to_upload()
        try:
            btn = instance.ele(".toolbar-btn-content icon btn-sendimg tooltip tooltip-top", timeout=5)
            if btn:
                btn.click.to_upload(abs_path)
                self._random_delay(2, 3)
                return True
        except Exception:
            pass

        return False

    def _record_sent_now(self, job: dict):
        """投递成功的瞬间就落库 + 推前端，不等收尾动作跑完。

        原来记录要等图片上传、关弹窗、关临时标签页全部做完（5~30 秒）才写，
        界面在这段时间里是"BOSS 上已经投了、记录里还没有"。
        """
        if job.get("_recorded"):
            return
        job["_recorded"] = True
        try:
            self._emit_greet_event(job, "success")
            self._record_greet(job, is_greeted=True,
                               actual_greeting_sent=job.get("_actual_greeting_sent", ""))
        except Exception as e:
            # 即时记录失败不影响投递本身，外层 send_greeting 还会补记一次
            job["_recorded"] = False
            self._log("WARN", f"即时记录投递结果失败，改由收尾路径补记: {e}")

    def _random_delay(self, min_sec: float, max_sec: float):
        """随机延迟（反爬策略）—— 走可打断的等待。

        原来是裸 time.sleep：投递路径每两个动作之间都调它（8~12 秒那种），
        点"停止"要等整串延迟跑完才生效，卡在验证页时最能拖时间。
        """
        self._interruptible_sleep(random.uniform(min_sec, max_sec))

    def _interruptible_sleep(self, seconds: float):
        """可被停止打断的等待。

        投递路径上原来用裸 time.sleep，一次就是 5~7 秒且不看 running，
        点"停止/暂停"要等整个重试循环跑完才生效——最长能拖几分钟。
        """
        deadline = time.time() + max(0.0, seconds)
        while self.running and time.time() < deadline:
            time.sleep(min(0.2, max(0.05, deadline - time.time())))

    def _chat_snapshot(self, instance) -> dict:
        """抓一次页面现场（输入框/抽屉/按钮/提示/验证码），取不到就把异常带进去。"""
        snap = {"url": "", "inputs": [], "chat_elements": [], "buttons": [],
                "notice": "", "toast": "", "captcha": False, "captcha_why": "", "error": ""}
        try:
            got = json.loads(instance.run_js(CHAT_SNAPSHOT_JS) or "{}")
            if isinstance(got, dict):
                snap.update(got)
        except Exception as e:
            snap["error"] = str(e)
        # 验证码判据全项目一份（page_handler），不在这里另起一套：
        # 以前快照自己用"页面上有没有 .verify-box 这种壳"来判，隐藏空壳也算命中，
        # 于是后端说在验证、页面上什么都没有。
        try:
            from boss_bot.page_handler import CAPTCHA_PROBE_JS, captcha_evidence
            snap["captcha"], snap["captcha_why"] = captcha_evidence(
                instance.run_js(CAPTCHA_PROBE_JS, as_expr=True))
        except Exception as e:
            self._log("DEBUG", f"验证码探针读取失败: {e}")
        return snap

    def _greeting_for(self, job: dict):
        """本条岗位要发的招呼语：岗位手写 > AI 按岗位定制 > 账号（自写或自动默认）。"""
        acc = self._account()
        resume = getattr(self.config, "resume", None)
        profile = getattr(self.config, "user_profile", None)
        account_text = effective_account_greeting(acc, resume, profile) if acc else ""
        ai_text = sanitize_ai_greeting(job.get("_ai_suggested_greeting")
                                       or (job.get("_ai_result") or {}).get("suggested_greeting"))
        return pick_greeting(job.get("greeting_message", ""),
                             account_text, DEFAULT_GREETING, ai_text)

    def _auto_greet_dialog(self, instance) -> dict:
        """页面上有没有 BOSS"已自动发送"的弹窗；有就带回它的现场文本和按钮。"""
        try:
            return parse_auto_greet_dialog(instance.run_js(AUTO_GREET_PROBE_JS))
        except Exception as e:
            self._log("DEBUG", f"自动发送弹窗探测失败: {e}")
            return {}

    def _drawer_probe(self, instance):
        try:
            return instance.run_js(DRAWER_READY_JS)
        except Exception as e:
            self._log("DEBUG", f"抽屉/弹窗状态探测失败: {e}")
            return ""

    def _wait_drawer_or_dialog(self, instance) -> str:
        """点完「立即沟通」先问清结局："drawer" / "dialog" / ""（看不出来）。

        返回 "" 不等于失败，意思是"这里探测不出来，走原来那条慢路径兜底"——
        选择器逐个试、iframe、遍历标签页都还留着，BOSS 哪天换了弹窗形态也不至于
        突然一个都认不出来。
        """
        attempts = max(1, int(DRAWER_READY_TIMEOUT_SEC // DRAWER_READY_POLL_SEC))
        for _ in range(attempts):
            if not self.running:
                return ""
            got = parse_drawer_probe(self._drawer_probe(instance))
            if got.get("drawer"):
                return "drawer"
            if got.get("dialog"):
                return "dialog"
            self._interruptible_sleep(DRAWER_READY_POLL_SEC)
        return ""

    def _auto_greet_path(self, instance, job, greeting):
        """BOSS 第二种打招呼机制：平台自己把招呼发出去了，就地结算。

        返回 None 表示"不是这个弹窗"，调用方继续走原流程；返回 (True, "")
        表示这一单已经投出去了。记失败就是用户报的「BOSS 上明明投了，记录里
        却是红的」那一类对不上。
        """
        dialog = self._auto_greet_dialog(instance)
        if not dialog:
            return None
        self._log("WARN", f"BOSS 自动发出招呼语（第二种机制）| 弹窗class="
                          f"{dialog.get('cls', '')} | 按钮={dialog.get('buttons', [])}")
        self._log("WARN", f"  弹窗文本: {str(dialog.get('text', ''))[:120]}")
        outcome = self._auto_greet_followup(instance, greeting, dialog)
        if outcome in ("matched", "sent"):
            job["_auto_greet_note"] = (
                "BOSS 自动发出的即本号招呼语，未重复发送" if outcome == "matched"
                else "BOSS 自动发的是平台预设文案，已补发本号招呼语")
            self._mark_chatted(job)
            self._record_sent_now(job)
            return True, ""
        # 弹窗在 = 平台已经把招呼发出去了，只是没能进会话核对文案。留痕，不记失败。
        self._mark_chatted(job)
        job["_auto_greet_note"] = "平台已自动发出招呼语，未能进会话核对文案（建议抽查）"
        self._record_sent_now(job)
        return True, ""

    def _read_last_outgoing(self, tab) -> dict:
        """读会话里我方最后一条已发出的气泡；读不到返回空 dict。"""
        try:
            raw = tab.run_js(LAST_MINE_BUBBLE_JS)
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self._log("DEBUG", f"读取我方最后一条气泡失败: {e}")
            return {}

    def _greet_tab_candidates(self, instance) -> list:
        """弹窗点完「继续沟通」后会话可能开在当前页抽屉里，也可能新开一个标签页。

        遍历规则跟输入框兜底那段一致：排除回复引擎专用标签页，不碰 instance 本身。
        """
        cands = [instance]
        seen = {id(instance)}
        browser = instance._get_browser() if hasattr(instance, "_get_browser") else None
        if browser is None:
            return cands
        reply_tab_id = None
        if self.browser_manager is not None:
            try:
                reply_tab_id = self.browser_manager.get_reply_tab_id()
            except Exception:
                reply_tab_id = None
        try:
            tab_ids = list(browser.tab_ids)
        except Exception:
            return cands
        for tab_id in tab_ids:
            if reply_tab_id and tab_id == reply_tab_id:
                continue
            try:
                tab = browser.get_tab(tab_id)
            except Exception:
                continue
            if tab is None or id(tab) in seen:
                continue
            seen.add(id(tab))
            cands.append(tab)
        return cands

    def _auto_greet_followup(self, instance, greeting: str, dialog: dict) -> str:
        """第二种打招呼机制：BOSS 自己发了一条，我们判断要不要补发本号那句。

        matched=BOSS 发的就是本号招呼语（不重复发）；sent=内容不是我们的，已补发；
        none=进不去会话，什么都没发。盲发会对着搜索页打字，所以读不到列表就收手。
        """
        btn_text = pick_continue_btn(dialog)
        if not btn_text:
            self._log("WARN", "自动发送弹窗里没有「继续沟通」，无法进入会话核对")
            return "none"

        try:
            btn = instance.ele(f"text={btn_text}", timeout=3)
            if not btn:
                self._log("WARN", f"弹窗里的「{btn_text}」按钮没找到")
                return "none"
            btn.click()
        except Exception as e:
            self._log("WARN", f"点击「{btn_text}」失败: {e}")
            return "none"

        tab, read = self._wait_chat_open(instance)
        if tab is None:
            self._log("WARN", f"点了「继续沟通」也读不到会话气泡"
                              f"（{CHAT_OPEN_POLLS} 次 × {CHAT_OPEN_POLL_SEC:.0f}s），不盲发")
            return "none"
        sent_text = read.get("text", "")
        if auto_greet_matches(sent_text, greeting):
            self._log("INFO", "BOSS 自动发出的就是本号招呼语，不重复发送")
            return "matched"
        if not norm_greeting(greeting):
            self._log("WARN", "本号招呼语为空，不补发")
            return "none"

        input_area = None
        for sel in AUTO_GREET_INPUT_SELECTORS:
            try:
                input_area = tab.ele(sel, timeout=3)
                if input_area:
                    break
            except Exception:
                continue
        if not input_area:
            self._log("WARN", f"进了会话但没找到输入框（我方气泡 {read.get('mine')} 条，"
                              f"最后一条: {str(sent_text)[:40]}）")
            return "none"

        input_area.input(greeting)
        self._random_delay(1, 2)
        send_btn = None
        for sel in AUTO_GREET_SEND_SELECTORS:
            try:
                send_btn = tab.ele(sel, timeout=3)
                if send_btn:
                    break
            except Exception:
                continue
        if send_btn:
            send_btn.click()
        else:
            input_area.input("\n")
        self._log("INFO", f"BOSS 自动发的是平台预设文案"
                          f"（{str(sent_text)[:30] or '无我方气泡'}），已补发本号招呼语: "
                          f"{greeting[:40]}")
        return "sent"

    def _wait_chat_open(self, instance):
        """点完「继续沟通」等到会话真的渲染出来；返回 (tab, 我方气泡) 或 (None, {})。

        会话是异步加载的，固定等 2~3 秒常常正好卡在气泡还没画出来的时候——
        那时判"进不去"，本号招呼语就永远补不上，HR 只看到平台那句默认文案。
        """
        for attempt in range(CHAT_OPEN_POLLS):
            for tab in self._greet_tab_candidates(instance):
                read = self._read_last_outgoing(tab)
                if read and read.get("chat_page"):
                    return tab, read
            if attempt + 1 < CHAT_OPEN_POLLS and self.running:
                self._interruptible_sleep(CHAT_OPEN_POLL_SEC)
        return None, {}

    def _on_captcha_page(self, instance) -> bool:
        """当前页面是不是 BOSS 的人机验证页。"""
        try:
            from boss_bot.page_handler import CAPTCHA_PROBE_JS, classify_health
            probe = instance.run_js(CAPTCHA_PROBE_JS, as_expr=True)
            return classify_health(instance.url or "", probe) == "captcha"
        except Exception:
            return False


    # ── Cookie 管理 ──

    def _load_cookies(self) -> bool:
        """加载 Cookie。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return False
        try:
            cookie_name = self._cookie_file if self._cookie_file else "zhipin_cookies.json"
            paths_to_try = []
            if not os.path.isabs(cookie_name):
                paths_to_try.append(str(DATA_DIR / cookie_name))
                # 前端「我已登录」保存在项目根目录，这里也要能找到
                paths_to_try.append(str(BASE_DIR / cookie_name))
            else:
                paths_to_try.append(cookie_name)
            paths_to_try.append(str(DATA_DIR / "zhipin_cookies.json"))
            paths_to_try.append(str(BASE_DIR / "zhipin_cookies.json"))

            loaded = False
            for p in paths_to_try:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        cookies = json.load(f)
                    instance.set.cookies(cookies)
                    self._log("INFO", f"已加载 Cookie: {p}")
                    loaded = True
                    break
            if not loaded:
                self._log("WARN", "未找到 Cookie 文件")
            return loaded
        except Exception as e:
            self._log("WARN", f"Cookie 加载失败: {e}")
            return False

    def _save_cookies(self):
        """保存 Cookie。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return
        try:
            cookie_name = self._cookie_file or "zhipin_cookies.json"
            dst = str(resolve_path(cookie_name))
            cookies = instance.cookies()
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            self._log("INFO", f"已保存 Cookie: {dst}")
            # 这里以前还会把本账号的 Cookie 再 copy 一份到公共的 zhipin_cookies.json，
            # 等于把 B 号的登录态盖到 A 号头上，多账号下必须各写各的
        except Exception as e:
            self._log("WARN", f"Cookie 保存失败: {e}")


    # ── 去重管理 ──

    def _load_chatted(self) -> set:
        """读取已沟通岗位集合，进程内缓存，超过 CHATTED_REFRESH_SECONDS 重读一次。

        缓存不能永久有效：另一个账号刚打过的岗位，本账号如果在跑一整晚，
        永远读到自己那份旧快照就会重复招呼同一个 HR。
        """
        now = time.time()
        if self._chatted_cache is None or (now - self._chatted_loaded_at) > self.CHATTED_REFRESH_SECONDS:
            urls = set()
            try:
                if CHATTED_DB_FILE.exists():
                    with open(CHATTED_DB_FILE, "r", encoding="utf-8") as f:
                        urls = set(json.load(f))
            except Exception as e:
                self._log("WARN", f"读取去重库失败，按未沟通过处理: {e}")
                urls = set()
            # 本地已标记但还没落盘成功的，不能因为重读又丢了
            urls |= (self._chatted_cache or set())
            self._chatted_cache = urls
            self._chatted_loaded_at = now
        return self._chatted_cache

    def _is_already_chatted(self, job: dict) -> bool:
        """检查是否已沟通过。"""
        url = job.get("url", "")
        if not url:
            return False
        return url in self._load_chatted()

    def _mark_chatted(self, job: dict):
        """标记岗位为已沟通。

        去重库两个账号共用一份文件（同一个岗位让两个号都打一遍，HR 会收到
        两条一模一样的招呼）。但每个引擎各持一份内存副本，整文件重写会把
        对方刚加的 URL 抹掉 → 下次重复招呼。所以写入前先读盘并集合并。
        """
        url = job.get("url", "")
        if not url:
            return
        if url in self._load_chatted():
            return
        with GreetEngine._chatted_lock:
            try:
                merged = set()
                if CHATTED_DB_FILE.exists():
                    with open(CHATTED_DB_FILE, "r", encoding="utf-8") as f:
                        merged = set(json.load(f))
                merged |= (self._chatted_cache or set())
                merged.add(url)
                write_json_atomic(CHATTED_DB_FILE, sorted(merged))
                self._chatted_cache = merged
            except Exception as e:
                # 写失败必须报出来：静默丢一条就等于下次重复打招呼
                self._log("WARN", f"写入去重库失败，可能重复打招呼: {e}")

    # ── 聊天日志 ──

