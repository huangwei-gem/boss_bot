# -*- coding: utf-8 -*-
"""无头 / 后台模式能不能过 BOSS 风控 —— 拿真号 Cookie 在隔离环境里实测。

为什么单独一个脚本：无头是"要么整条链都能跑，要么全废"的开关，
猜没有用，必须看 BOSS 到底给不给岗位列表、聊天侧栏渲不渲染。

另外三个只在真账号上才看得出结果的功能也在这里量（它们正好是"关标签页"
那次改动可能碰坏的地方，而 tools/probe_*.py 那批老探针全是硬连线上
9222/9223 的，不能拿来当无头证据）：
  - JD 抽屉 / 岗位详情面板（投递链路读 JD 的地方）
  - 交换联系方式卡片 .message-card-wrap + .card-btn
  - 面试邀请：卡片上的「立即查看」+ 展开后 _locate_interview_reject_btn()
    能不能找到「拒绝」

隔离方式（红线）：
- 只用临时端口 + 临时 profile，绝不碰线上 9222/9223 那两个号的 profile；
- Cookie 文件只读，跑前跑后各算一次 sha256，必须一模一样；
- 只定位元素，不点「立即沟通」「继续沟通」「发送」「同意」「拒绝」，
  一次招呼都不会发出去；唯一允许的点击是岗位卡片（等于导航）和面试卡片上的
  「立即查看」（只是把那块面板展开，不提交任何东西）。

用法：
    python tools/check_headless_boss.py --mode headless
    python tools/check_headless_boss.py --mode headful
    python tools/check_headless_boss.py --mode headless --account 1
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.greet_engine import (DRAWER_READY_JS, JD_THIN_CHARS,  # noqa: E402
                                   parse_drawer_probe)
from boss_bot.page_handler import BossChatHandler  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
SEARCH_URL = "https://www.zhipin.com/web/geek/job-recommend"
CHAT_URL = "https://www.zhipin.com/web/geek/chat"

# 线上那两个浏览器和线上面板：本脚本一个都不许连。
# 端口撞车时 DrissionPage 会直接接上已在跑的浏览器，那样"隔离"就是假的，
# 探针会在用户真正登录的标签页上点来点去。
LIVE_PORTS = (5000, 9222, 9223)

# 跑前跑后各算一次 sha256 的只读文件。
# data/*.json 也在名单里，但线上引擎正在写它们，改动不能算在本脚本头上，
# 所以只对 Cookie / 配置这几个真正可能被探针写坏的文件硬失败。
CRITICAL_FILES = ("zhipin_cookies.json", "zhipin_cookies_1.json",
                  "bot_config.json", "config_overrides.json")

# 聊天页那一轮的上限：点开会话会把 BOSS 侧的未读清掉，
# 证到能力就收，绝不为凑数把用户几十个会话翻成已读。
MAX_CHAT_OPENS = 9
# 两类卡片各留名额：面试邀请本来就少，混在"最近更新"里会被回复轮刚碰过的
# 会话挤光，跑完 8 个会话一个邀请都没碰到就等于没测。
MAX_INTERVIEW_CANDIDATES = 4
MAX_CONTACT_CANDIDATES = 5
CHAT_STAGE_BUDGET_SEC = 300.0

# 浏览器自己报的家底：无头最容易在这些点上露馅。
# 必须写成带 return 的语句体：DrissionPage 的 run_js 传箭头函数只会拿到函数
# 对象本身（实测返回 null），探针等于没跑。
FINGERPRINT_JS = r"""
return {
  ua: navigator.userAgent,
  webdriver: navigator.webdriver,
  headless_ua: navigator.userAgent.indexOf('Headless') >= 0,
  plugins: navigator.plugins.length,
  has_chrome: !!window.chrome,
  languages: (navigator.languages || []).join(','),
  platform: navigator.platform,
  viewport: window.innerWidth + 'x' + window.innerHeight,
  dpr: window.devicePixelRatio,
  webgl: (() => {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) return 'no-webgl';
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : 'no-ext';
  })(),
}
"""

# 交换联系方式卡片说的是什么。真机见过的说法有"交换微信/交换联系方式/
# 要一个电话号码"，判据要和 page_handler.accept_contact_exchange 用同一套，
# 否则存档筛出来的候选和页面上认的卡片会对不上。
_CONTACT_MARKS = ("交换", "联系方式", "电话号码", "微信号码")

# BOSS 未登录/风控时都往这两个地址跳，混在一起会误判成"无头被拦"
LOGIN_MARKS = ("passport", "security.html", "login", "user/?ka")

# 点第一张岗位卡把详情/抽屉打开：这一下等价于导航，所以只许点卡片里的 <a>。
# 文案带「沟通/打招呼/发送/同意/拒绝」的一律不点 —— 万一某个版本把沟通按钮
# 直接挂在卡片里，这一下就是替用户打招呼。
CLICK_FIRST_CARD_JS = r"""
var forbid = /沟通|打招呼|投递|发送|同意|拒绝|接受/;
var cards = document.querySelectorAll('.job-card-wrap');
if (!cards.length) return 'no-card';
// 从第二张开始找：抽屉一进来就先渲着第一张的正文，点第一张看不出"点下去有没有反应"
for (var i = 1; i < cards.length && i < 5; i++) {
  var a = cards[i].querySelector('a[href*="job_detail"]');
  if (!a || forbid.test((a.textContent || '').trim())) continue;
  a.click();
  return 'clicked#' + i;
}
return 'no-safe-link';
"""

# 抽屉/详情页取证：只量"JD 正文真渲染出来没有"，一个按钮都不点。
# 判据挂在可见性上：BOSS 的详情页常驻一份 display:none 的模板，
# 只数类名会把没渲染说成渲染了（同一个坑在 greet_engine 的快照里踩过）。
JD_DRAWER_JS = r"""
function vis(el) {
  if (!el || !el.getClientRects || !el.getClientRects().length) return false;
  var s = window.getComputedStyle(el);
  return s.display !== 'none' && s.visibility !== 'hidden';
}
function visChars(sel) {
  var nodes = document.querySelectorAll(sel), n = 0;
  for (var i = 0; i < nodes.length; i++) {
    if (vis(nodes[i])) n += (nodes[i].textContent || '').trim().length;
  }
  return n;
}
var hosts = ['.job-detail-box', '.job-detail-body', '.job-drawer', '.drawer',
             '[class*="drawer"]', '.job-detail', '[class*="job-detail"]',
             '.job-primary', '.info-primary', '.job-banner'];
var hits = [];
for (var k = 0; k < hosts.length; k++) {
  var e = document.querySelectorAll(hosts[k]), v = 0;
  for (var m = 0; m < e.length; m++) if (vis(e[m])) v++;
  if (v) hits.push(hosts[k] + 'x' + v);
}
var greet = 0;
var gs = document.querySelectorAll('.btn-startchat, .btn-starttalk, .job-btns, .op-btn-chat');
for (var q = 0; q < gs.length; q++) if (vis(gs[q])) greet++;
// 岗位名用来判断"点了卡片抽屉有没有换内容"：正文再长，一直显示同一份也不算反应
var paneName = '';
var names = document.querySelectorAll('.job-detail-box .job-name, .job-primary .name, h1.name');
for (var z = 0; z < names.length; z++) {
  if (vis(names[z])) { paneName = (names[z].textContent || '').trim().slice(0, 30); break; }
}
return JSON.stringify({url: location.href, jd_chars: visChars('.job-sec-text'),
                       drawer_chars: visChars('.job-detail-body'),
                       req_chars: visChars('.requirements'), greet_btns: greet,
                       pane_name: paneName, hosts: hits});
"""

# 交换联系方式卡片：判据抄 page_handler.accept_contact_exchange，但只看不动手。
# 那一个"同意"点下去就是真替用户把联系方式交出去了。
CONTACT_CARD_JS = r"""
function vis(el) { return !!(el && el.getClientRects && el.getClientRects().length); }
var all = document.querySelectorAll('.message-card-wrap');
var agree = 0, btns = [];
for (var i = 0; i < all.length; i++) {
  var t = all[i].querySelector('.message-card-top-title');
  var tx = t ? (t.textContent || '') : '';
  if (tx.indexOf('是否同意') < 0) continue;
  // 简历卡也带"是否同意"，混进来会把"卡片定位没问题"这个结论带偏
  var isContact = tx.indexOf('交换') >= 0 || tx.indexOf('联系方式') >= 0
      || tx.indexOf('电话号码') >= 0 || tx.indexOf('微信号码') >= 0;
  if (!isContact || tx.indexOf('简历') >= 0) continue;
  agree++;
  var bs = all[i].querySelectorAll('.message-card-buttons .card-btn');
  for (var j = 0; j < bs.length; j++) {
    if (vis(bs[j])) btns.push((bs[j].textContent || '').trim().slice(0, 4));
  }
}
return JSON.stringify({cards: all.length, agree_cards: agree, btns: btns});
"""

# 面试邀请卡片：只数卡片和「立即查看」，再看那块面板的容器在不在。
# .interview-modal.interview-cancel（"已拒绝/已超时"）是已经办完的，
# 没有「拒绝」按钮不是 bug，得和"无头渲不出来"分开写。
# 注意：那块 dialog 展开后在 SPA 里不会随换会话卸载，所以容器只能当
# "无头渲得出这块面板"的证据，不能拿来断定"当前这个会话有面试邀请"。
INTERVIEW_CARD_JS = r"""
function vis(el) { return !!(el && el.getClientRects && el.getClientRects().length); }
var cards = document.querySelectorAll('.message-card-wrap');
var invite = 0, view = 0;
for (var i = 0; i < cards.length; i++) {
  var tx = cards[i].textContent || '';
  if (tx.indexOf('面试') < 0 || tx.indexOf('邀请') < 0) continue;
  invite++;
  var bs = cards[i].querySelectorAll('.card-btn');
  for (var j = 0; j < bs.length; j++) {
    if ((bs[j].textContent || '').trim() === '立即查看' && vis(bs[j])) view++;
  }
}
var hosts = ['.interview-page-footer', '.interview-wrap', '.boss-popup__wrapper',
             '.interview-modal.interview-status0', '.interview-modal.interview-cancel'];
var hits = [];
for (var k = 0; k < hosts.length; k++) {
  var e = document.querySelectorAll(hosts[k]), v = 0;
  for (var q = 0; q < e.length; q++) if (vis(e[q])) v++;
  if (v) hits.push(hosts[k] + 'x' + v);
}
return JSON.stringify({cards: cards.length, invite_cards: invite, view_btn: view,
                       modal_hosts: hits});
"""


def _sha256(path: Path) -> str:
    if not path.is_file():
        return "<缺失>"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _protected_snapshot() -> dict:
    """只读文件的指纹：sha + 大小 + mtime。

    带 mtime 是为了事后分得清"谁写的"：线上引擎一直在写 data/*.json，
    探针跑一趟那几个文件本来就会变，把它们算成探针写坏了就是自己骗自己。
    """
    paths = [BASE_DIR / name for name in CRITICAL_FILES]
    paths += sorted((BASE_DIR / "data").glob("*.json"))
    out = {}
    for p in paths:
        if not p.is_file():
            continue
        st = p.stat()
        out[p.relative_to(BASE_DIR).as_posix()] = {
            "sha": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
            "size": st.st_size, "mtime": int(st.st_mtime)}
    return out


def _cookie_file(account_index: int) -> tuple:
    """本账号真正那份 Cookie，返回 (路径, 出处说明)。

    这里必须用 UnifiedConfig.load()：直接 UnifiedConfig() 拿到的是代码默认值，
    里面永远只有一个"主账号"，于是 --account 1 会悄悄用回 0 号那份 Cookie，
    第二个号的结论全是假的。
    越界的账号号也一样不能悄悄回落：报"账号4 跑过了"其实是 0 号，
    比直接报错更坏。
    """
    cfg = UnifiedConfig.load()
    accounts = cfg.greet.accounts or []
    if not 0 <= account_index < len(accounts):
        raise ValueError(f"配置里只有 {len(accounts)} 个账号（索引 0~{len(accounts) - 1}），"
                         f"--account {account_index} 越界，不再假装那是另一个号")
    name = accounts[account_index].cookie_file or cfg.login.cookie_file \
        or "zhipin_cookies.json"
    owner = accounts[account_index].name or f"账号{account_index}"
    return Path(str(resolve_path(name))), f"{owner}·cookie_file"


def _card_candidates(account_index: int) -> list:
    """从本地存档里挑出"这家会话出现过交换联系方式卡/面试邀请卡"的会话。

    为什么拿存档当靶子：无头下"卡片渲不出来"和"这个号最近根本没收到卡片"
    是两件不同的事，只有奔着真出现过卡片的会话去点，才证伪得了前者。
    只 read_text，不碰 MessageStore 的写入路径。

    账号归属按 MessageStore 的口径来：账号0 的存档是历史那批不带 a0_ 前缀的
    文件名，只 glob a0_* 会给第一个号筛出 0 个候选，看着像"这号没卡片"。
    """
    out = []
    for path in sorted((BASE_DIR / "messages").glob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        tag = re.match(r"^a(\d+)_", path.name)
        owner = data.get("account_index")
        if owner is None:
            owner = tag.group(1) if tag else "0"
        if str(owner) != str(account_index):
            continue
        kinds = set()
        for msg in data.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            body = " ".join(str(msg.get(k) or "") for k in ("text", "card_text", "content"))
            if "是否同意" in body and any(w in body for w in _CONTACT_MARKS):
                kinds.add("contact")
            if "面试" in body and "邀请" in body:
                kinds.add("interview")
        if not kinds:
            continue
        out.append({"name": data.get("chat_name") or "",
                    "company": data.get("company") or "",
                    "kinds": sorted(kinds),
                    "updated_at": str(data.get("updated_at") or "")})
    # 越近的会话越可能还挂在 BOSS 那 30 天的联系人里，点得开的概率大得多
    out.sort(key=lambda r: r["updated_at"], reverse=True)
    picked = {}
    for row in ([r for r in out if "interview" in r["kinds"]][:MAX_INTERVIEW_CANDIDATES]
                + [r for r in out if "contact" in r["kinds"]][:MAX_CONTACT_CANDIDATES]):
        picked[(row["name"], row["company"])] = row
    return list(picked.values())[:MAX_CHAT_OPENS]


def _modal_cancelled(hosts) -> bool:
    """面试面板渲出来了、但那是 interview-cancel（已拒绝/已超时）那一张。

    待接受的是 .interview-modal.interview-status0，上面才有 拒绝/接受 两个按钮；
    已经办完的那张没有「拒绝」是正常状态，不能报成"无头下定位器找不到按钮"。
    """
    return any("interview-cancel" in h for h in hosts or [])


def _run_js_json(instance, script: str) -> dict:
    """跑一段返回 JSON 字符串的探针 JS，拿不到就回空 dict。

    异常只记类型名，不拼 str(e)：DrissionPage 抛 JavaScriptError 时会把求值
    上下文整段吐出来，里面可能带着会话令牌，那东西不能进 stdout 也不能进文件。
    """
    try:
        raw = instance.run_js(script)
    except Exception as e:
        return {"_error": type(e).__name__}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"_error": "bad-json"}
    return data if isinstance(data, dict) else {"_error": "not-object"}


def _count(instance, selector: str) -> int:
    try:
        return len(instance.eles(selector, timeout=4) or [])
    except Exception:
        return 0


def _settle(instance, marks=LOGIN_MARKS, timeout=40):
    """BOSS 首屏常先弹一次 security.html 再自己跳回来，等它落定再看结局。

    返回 (最终 URL, 中途见过的风控地址)。不等就直接读 URL 会把
    "SPA 正在过安检" 误判成 "无头被风控拦死"。
    """
    security_seen = ""
    end = time.time() + timeout
    while time.time() < end:
        url = instance.url or ""
        if any(m in url for m in marks):
            security_seen = security_seen or url
            time.sleep(2)
            continue
        return url, security_seen
    return instance.url or "", security_seen


def _probe_jd_drawer(instance) -> dict:
    """点一张岗位卡，看 JD 抽屉/详情面板在无头下渲不渲染（只导航，绝不沟通）。

    投递链路是真的靠这块面板拿 JD 的：greet_engine 读到 .job-sec-text 才有
    JD 闸门和 AI 判分的依据，抽屉渲不出来，后面整条链都是瞎投。
    """
    out = {"status": "inconclusive", "detail": "", "before": {}, "after": {}}
    if not (instance.eles(".job-card-wrap", timeout=4) or []):
        out["detail"] = "岗位列表里没有卡片，抽屉无从验证"
        return out
    out["before"] = _run_js_json(instance, JD_DRAWER_JS)

    browser = instance._get_browser()
    try:
        before_ids = set(browser.tab_ids) if browser else set()
    except Exception:
        before_ids = set()
    try:
        clicked = instance.run_js(CLICK_FIRST_CARD_JS) or ""
    except Exception as e:
        clicked = f"error:{type(e).__name__}"
    out["click"] = clicked
    if not str(clicked).startswith("clicked"):
        out["detail"] = f"卡片没点成（{clicked}），没有安全的职位 <a> 就不硬来"
        return out

    time.sleep(4)
    # 有的版本把详情开在新标签页里：读那一份，读完立刻关掉。
    # 刚把 49 个标签页的泄漏修好，探针不能再把源头添回去。
    tab = None
    try:
        new_ids = (set(browser.tab_ids) - before_ids) if browser else set()
        if new_ids:
            tab = browser.get_tab(new_ids.pop())
            time.sleep(2)
    except Exception:
        tab = None
    try:
        out["after"] = _run_js_json(tab or instance, JD_DRAWER_JS)
    finally:
        if tab is not None:
            try:
                tab.close()
            except Exception:
                pass

    def chars_of(d):
        # 页内抽屉是 .job-detail-body，独立详情页是 .job-sec-text，取看得见的那一份
        return max(int(d.get("jd_chars") or 0), int(d.get("drawer_chars") or 0))

    chars = chars_of(out["after"])
    out["jd_chars"] = chars
    # 抽屉"真能打开"的证据是内容跟着点击换了；URL 或岗位名变了都算
    out["responded"] = (out["before"].get("url") != out["after"].get("url")
                        or out["before"].get("pane_name") != out["after"].get("pane_name")
                        or chars_of(out["before"]) != chars)
    hosts = out["after"].get("hosts") or []
    if chars >= JD_THIN_CHARS:
        out["status"] = "proved"
        out["detail"] = (f"页内抽屉渲出 {chars} 字 JD 正文（门槛 {JD_THIN_CHARS} 字），"
                         f"点卡片后内容换了没有: {out['responded']}，容器 {hosts[:3]}")
    elif chars > 0:
        out["status"] = "inconclusive"
        out["detail"] = (f"抽屉只读到 {chars} 字，低于投递链路的 JD 门槛 "
                         f"{JD_THIN_CHARS} 字，容器 {hosts[:3]}")
    else:
        # 页内抽屉没正文不代表链路断：投递真正导航去的是独立详情页，那一步另算
        out["status"] = "inconclusive"
        out["detail"] = (f"页内抽屉没渲出正文（落地 {str(out['after'].get('url'))[:70]}），"
                         f"看独立详情页那一步")
    return out


def _probe_chat_features(instance, account_index: int) -> dict:
    """在隔离浏览器里点开几个会话，验卡片类 DOM 在无头下渲不渲染。

    只点侧栏的会话行和面试卡片上的「立即查看」（那只是把面板展开，不提交任何东西）；
    「同意」「拒绝」「发送」一下都不碰 —— 点一次「拒绝」就把 HR 的面试撤回了，
    点一次「同意」等于替用户把联系方式交出去。
    """
    out = {
        "candidates": 0, "chats_visited": 0, "chats_not_found": 0,
        "interview_scanned": 0,
        "cards_total": 0, "bubbles": 0, "drawer_ready": {}, "errors": [],
        "contact": {"status": "inconclusive", "detail": "没有可点开的候选会话"},
        "interview": {"status": "inconclusive", "detail": "没有可点开的候选会话"},
    }
    cands = _card_candidates(account_index)
    out["candidates"] = len(cands)
    if not cands:
        out["contact"]["detail"] = out["interview"]["detail"] = (
            f"账号{account_index} 的存档里没有出现过这两类卡片的会话，无靶子可打")
        return out

    handler = BossChatHandler(browser_instance=instance)
    need = {"contact", "interview"}
    contact_hit = False
    interview_seen = False
    deadline = time.time() + CHAT_STAGE_BUDGET_SEC
    for c in cands:
        if not need or time.time() > deadline:
            break
        if not set(c["kinds"]) & need:
            continue
        try:
            if not handler.enter_chat({"name": c["name"], "company": c["company"],
                                       "index": -1}):
                out["chats_not_found"] += 1
                continue
            out["chats_visited"] += 1
            # 卡片常在更早的历史里，滚一下才有；上限 3 轮，滚不出来就是没有
            msgs = handler.read_all_messages(max_scroll_rounds=3, scroll_wait_ms=600) or []
        except Exception as e:
            out["errors"].append(f"进入会话失败: {type(e).__name__}")
            continue
        out["bubbles"] = max(out["bubbles"], len(msgs))

        if not out["drawer_ready"]:
            try:
                out["drawer_ready"] = parse_drawer_probe(instance.run_js(DRAWER_READY_JS))
            except Exception as e:
                out["errors"].append(f"抽屉探针没跑成: {type(e).__name__}")

        contact = _run_js_json(instance, CONTACT_CARD_JS)
        out["cards_total"] = max(out["cards_total"], int(contact.get("cards") or 0))
        if "contact" in need and contact.get("agree_cards"):
            contact_hit = True
            btns = contact.get("btns") or []
            if "同意" in btns or "拒绝" in btns:
                out["contact"] = {
                    "status": "proved",
                    "detail": f'定位到 {contact["agree_cards"]} 张交换联系方式卡片，'
                              f'卡片按钮 {btns}（未点击）'}
                need.discard("contact")
            else:
                out["contact"] = {
                    "status": "inconclusive",
                    "detail": f'卡片本体在（{contact["agree_cards"]} 张），.card-btn 已被换成 '
                              f'{btns or "无"} —— 这单早处理过了，不是无头的问题'}

        iv = _run_js_json(instance, INTERVIEW_CARD_JS)
        if "interview" in c["kinds"]:
            out["interview_scanned"] += 1
        if iv.get("invite_cards"):
            interview_seen = True
        if "interview" in need and iv.get("invite_cards"):
            hosts = iv.get("modal_hosts") or []
            if not iv.get("view_btn"):
                out["interview"] = {
                    "status": "inconclusive",
                    "detail": f'面试邀请卡片在（{iv["invite_cards"]} 张），但没有「立即查看」可点，'
                              f'面板容器 {hosts}'}
                if _modal_cancelled(hosts):
                    need.discard("interview")
                continue
            expanded = handler.open_interview_invite()
            # execute=False 这条分支只定位不点：内部就是生产代码的
            # _locate_interview_reject_btn()，带 8 秒轮询（面板异步渲染，
            # 固定睡 2 秒会整单漏掉）
            verdict = (handler.reject_interview_invite(execute=False, wait_sec=8)
                       if expanded else "not-expanded")
            after = _run_js_json(instance, INTERVIEW_CARD_JS)
            hosts = after.get("modal_hosts") or []
            if verdict == "found":
                out["interview"] = {"status": "proved",
                                    "detail": f"「立即查看」展开后 _locate_interview_reject_btn()"
                                              f" = found，面板容器 {hosts}（未点击「拒绝」）"}
                need.discard("interview")
            elif _modal_cancelled(hosts):
                # 面板渲出来了，但那张是 interview-cancel（已拒绝/已超时）：
                # 本号此刻没有待接受的邀请，定位器找不到「拒绝」是对的
                out["interview"] = {
                    "status": "inconclusive",
                    "detail": f"「立即查看」点开后渲出的是已拒绝/已超时那张，没有可点的「拒绝」"
                              f"（定位器返回 {verdict}，面板容器 {hosts}）"}
                need.discard("interview")
            else:
                # 待接受的面板真渲出来了却没有「拒绝」，那才是生产定位器漏了
                pending = any("interview-status0" in h for h in hosts)
                out["interview"] = {
                    "status": "failed" if pending else "inconclusive",
                    "detail": f"展开后定位「拒绝」= {verdict}，面板容器 {hosts}，"
                              f"面试卡片 {after.get('invite_cards')} 张"}
                if pending:
                    need.discard("interview")

    if out["contact"]["status"] != "proved" and not contact_hit:
        out["contact"]["detail"] = (
            f"点开 {out['chats_visited']} 个存档里出现过卡片的会话，页面上卡片消息总数 "
            f"{out['cards_total']}（.message-card-wrap），气泡 {out['bubbles']} 条，"
            f"没有待同意的交换联系方式卡片 —— 账号当前没这一单的活数据，"
            f"既证不了也说明不了坏")
    if not interview_seen:
        out["interview"]["detail"] = (
            f"扫过 {out['interview_scanned']} 个存档里出现过面试邀请的会话，页面上没有"
            f"面试邀请卡片（点开 {out['chats_visited']} 个会话，点不开的 "
            f"{out['chats_not_found']} 个）—— 本号当前没有待处理的面试邀请")
    return out


def probe(mode: str, account_index: int, port: int) -> dict:
    headless = mode == "headless"
    cookie_path, cookie_from = _cookie_file(account_index)
    sha_before = _sha256(cookie_path)
    protected_before = _protected_snapshot()

    profile = Path(tempfile.mkdtemp(prefix=f"boss_{mode}_"))
    report = {
        "mode": mode,
        "account_index": account_index,
        "cookie_file": cookie_path.name,
        "cookie_file_from": cookie_from,
        "cookie_sha_before": sha_before,
        "profile": str(profile),
        "checks": {},
        "errors": [],
    }
    instance = None
    try:
        instance = launch_browser(
            headless=headless, port=port, user_data_dir=str(profile),
            viewport_width=1280, viewport_height=800,
        )
        if not instance.load_cookies(str(cookie_path)):
            report["errors"].append("Cookie 加载失败")

        instance.get(SEARCH_URL)
        time.sleep(5)
        url, security_seen = _settle(instance)
        report["checks"]["url"] = url
        report["checks"]["security_redirect"] = security_seen[:160]
        report["checks"]["fingerprint"] = instance.run_js(FINGERPRINT_JS)
        report["checks"]["job_cards"] = (
            _count(instance, ".job-card-wrap")
            or _count(instance, "li.job-card-box")
            or _count(instance, ".job-card-wrapper")
        )
        report["checks"]["login_wall"] = any(
            k in (instance.url or "") for k in ("login", "passport", "user/?ka")
        )

        # 卡片链接先抓在手里：点过一次卡片之后列表可能就没了，再想读第二次就读不到。
        # 只认 href 里带 job_detail 的那个 <a>：卡片里还挂着公司链接（target=_blank），
        # 点错了既开不了详情又白冒一次风控。
        href = ""
        try:
            cards = (instance.eles(".job-card-wrap", timeout=4)
                     or instance.eles("li.job-card-box", timeout=3))
            for card in (cards or []):
                for a in (card.eles("tag:a", timeout=2) or []):
                    link = a.attr("href") or ""
                    if "job_detail" in link:
                        href = link
                        break
                if href:
                    break
        except Exception as e:
            report["errors"].append(f"读岗位卡片链接失败: {e}")
        if href.startswith("/"):
            href = "https://www.zhipin.com" + href

        # JD 抽屉：投递链路真正读 JD 的地方，点卡片那一下只是导航
        report["checks"]["jd_drawer"] = _probe_jd_drawer(instance)

        # 岗位详情页能不能开（打招呼链路的必经一步，只开不点）
        detail_ok = ""
        try:
            if href.startswith("http"):
                instance.get(href)
                time.sleep(4)
                detail_ok = instance.url
        except Exception as e:
            report["errors"].append(f"岗位详情页打不开: {e}")
        report["checks"]["job_detail_url"] = detail_ok
        if detail_ok:
            report["checks"]["has_greet_btn"] = bool(
                _count(instance, ".job-btns") or _count(instance, ".btn-starttalk")
                or _count(instance, "tag:a")
            )
            # 独立详情页才是 greet_engine 读 JD、点沟通的那一页：
            # 抽屉没在列表页渲出来时，这一份读数就是投递链路的实际结论
            panel = _run_js_json(instance, JD_DRAWER_JS)
            report["checks"]["job_detail_panel"] = panel
            drawer = report["checks"]["jd_drawer"]
            panel_chars = max(int(panel.get("jd_chars") or 0),
                              int(panel.get("drawer_chars") or 0))
            if panel_chars >= JD_THIN_CHARS and drawer.get("status") != "proved":
                drawer["status"] = "proved"
                drawer["detail"] += (f"；独立详情页渲出 {panel_chars} 字正文"
                                     f"（.job-sec-text），沟通按钮块 "
                                     f"{panel.get('greet_btns')} 个（只定位未点）")

        # 聊天页：回复链路的必经页面，虚拟列表在无头下能不能渲染
        try:
            instance.get(CHAT_URL)
            time.sleep(6)
            report["checks"]["chat_url"] = instance.url
            report["checks"]["chat_rows"] = _count(instance, ".friend-content")
            report["checks"]["chat_bubbles"] = _count(instance, ".chat-message-item")
        except Exception as e:
            report["errors"].append(f"聊天页打不开: {e}")

        # 卡片类 DOM：交换联系方式 / 面试邀请，这两个是老探针只连 9222/9223、
        # 从来没在无头下量过的
        try:
            report["checks"]["chat_features"] = _probe_chat_features(instance, account_index)
        except Exception as e:
            report["errors"].append(f"聊天卡片探针崩了: {type(e).__name__}")

        # 风控验证码迹象：BOSS 拦人时整页会变成 verify
        try:
            body = (instance.run_js("return document.body.innerText.slice(0,400)") or "")
        except Exception:
            body = ""
        report["checks"]["body_snippet"] = body[:200]
        report["checks"]["captcha_suspect"] = any(
            k in body for k in ("安全验证", "人机验证", "访问验证", "滑动")
        )
        return report
    finally:
        if instance is not None:
            try:
                instance.quit()
            except Exception:
                pass
        shutil.rmtree(str(profile), ignore_errors=True)
        report["cookie_sha_after"] = _sha256(cookie_path)
        protected_after = _protected_snapshot()
        report["protected_sha_before"] = protected_before
        report["protected_sha_after"] = protected_after
        # 只报"哪些文件的指纹变了"：data/*.json 是线上引擎在写，
        # 把它们算成探针写坏了就是自己骗自己，看 mtime 分辨是谁动的手
        report["protected_changed"] = [
            k for k, v in protected_after.items()
            if (protected_before.get(k) or {}).get("sha") != v.get("sha")]


STATUS_CN = {"proved": "已证实", "inconclusive": "无活数据（暂判不了）", "failed": "失败"}


def _summarize(report: dict) -> list:
    """把散装数字归成一张能力表：无头是"整条链都能跑"的开关，结论得逐条摆出来。"""
    c = report.get("checks") or {}
    feats = c.get("chat_features") or {}
    drawer = c.get("jd_drawer") or {}
    ready = feats.get("drawer_ready") or {}
    rows = [
        ("岗位列表渲染",
         "proved" if c.get("job_cards") else ("inconclusive" if c.get("login_wall") else "failed"),
         f'{c.get("job_cards")} 张卡片，风控跳转 {c.get("security_redirect") or "无"}'),
        ("JD 抽屉 / 岗位详情", drawer.get("status", "inconclusive"),
         drawer.get("detail") or "没跑"),
        ("聊天侧栏 / 会话抽屉",
         "proved" if (c.get("chat_rows") and ready.get("drawer")) else
         ("inconclusive" if c.get("chat_rows") else "failed"),
         f'{c.get("chat_rows")} 行会话，生产抽屉探针 drawer={ready.get("drawer")} '
         f'dialog={ready.get("dialog")}，气泡 {feats.get("bubbles")} 条'),
        ("交换联系方式卡片 .message-card-wrap/.card-btn",
         (feats.get("contact") or {}).get("status", "inconclusive"),
         (feats.get("contact") or {}).get("detail") or "没跑"),
        ("面试邀请「拒绝」定位",
         (feats.get("interview") or {}).get("status", "inconclusive"),
         (feats.get("interview") or {}).get("detail") or "没跑"),
        ("验证码/风控迹象",
         "inconclusive" if c.get("captcha_suspect") else "proved",
         "正文出现验证字样" if c.get("captcha_suspect") else "没检出验证拦截"),
    ]
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("headless", "headful"), default="headless")
    p.add_argument("--account", type=int, default=0)
    p.add_argument("--port", type=int, default=0)
    a = p.parse_args()
    port = a.port or (9400 + a.account + (0 if a.mode == "headless" else 10))

    # 撞上线上端口就不能跑：DrissionPage 发现端口已被占用会直接接上去，
    # 那样"隔离环境"是假的，探针会打在用户真登录的浏览器和面板上。
    if port in LIVE_PORTS:
        print(f"端口 {port} 是线上面板/浏览器在用，本脚本拒绝使用（红线：不碰 :5000 与 9222/9223）")
        return 2

    try:
        report = probe(a.mode, a.account, port)
    except ValueError as e:
        print(f"账号索引不对：{e}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))

    rows = _summarize(report)
    print(f"\n无头能力结论（{report['mode']} / 账号{report['account_index']} / "
          f"{report['cookie_file']}）：")
    for label, status, detail in rows:
        print(f"  [{STATUS_CN[status]}] {label} —— {detail}")

    same = report.get("cookie_sha_before") == report.get("cookie_sha_after")
    print(("Cookie 文件未被改动 ✓" if same else "警告：Cookie 文件被改动了 ✗"))
    critical_changed = [k for k in report.get("protected_changed") or []
                        if k in CRITICAL_FILES]
    if critical_changed:
        print(f"警告：这些关键文件被改动了 ✗ {critical_changed}")
    changed = report.get("protected_changed") or []
    if changed:
        print(f"（跑前跑后指纹变过的文件：{changed} —— data/*.json 由线上引擎在写，"
              f"不是本脚本，看 mtime 可核对）")

    failed = [label for label, status, _ in rows if status == "failed"]
    if failed:
        print(f"判定失败的能力：{failed}")
    if not same or critical_changed or report["errors"] or failed:
        return 1
    # 判不了不等于失败：账号此刻没有那张卡片，硬报红就是把没证据说成有 bug
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
