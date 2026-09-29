# -*- coding: utf-8 -*-
"""仪表盘端到端实测 — 真浏览器、真点击、真接口，逐项断言

和 pytest 的分工：pytest 管单元/集成逻辑，这个脚本管"打开来真的能用"。
全程不点任何会对外发消息的按钮（启动/打招呼/发送/发简历都不点），
只做读、切、筛、存配置这类可回滚的操作。

用法：python tools/e2e_dashboard.py [--headed]
退出码：0 全过，1 有失败项
"""

import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = os.path.join(BASE, "cloakbrowser", "chrome.exe")
PORT = 9402
DASH = "http://127.0.0.1:5000"
SHOTS = os.path.join(BASE, "tools", "e2e")
os.makedirs(SHOTS, exist_ok=True)

RESULTS = []


def check(group, name, ok, detail=""):
    ok = bool(ok)
    RESULTS.append({"group": group, "name": name, "ok": ok, "detail": str(detail)[:220]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {group} / {name}"
          + (f"  ({str(detail)[:90]})" if detail and not ok else ""))


def js(tab, code):
    """run_js 包装：把 JSON 字符串还原成 Python 对象"""
    raw = tab.run_js(code, as_expr=True)
    if isinstance(raw, str) and raw[:1] in "[{":
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def start_flask():
    sys.path.insert(0, os.path.join(BASE, "flask-version"))
    import app as A
    A._open_dashboard = lambda *a, **k: None
    A._auto_ai_health = lambda *a, **k: None
    kwargs = {"host": "127.0.0.1", "port": 5000, "debug": False, "use_reloader": False}
    if A._socketio_kwargs.get("async_mode") == "threading":
        kwargs["allow_unsafe_werkzeug"] = True
    threading.Thread(target=lambda: A.socketio.run(A.app, **kwargs), daemon=True).start()
    import urllib.request
    for _ in range(80):
        try:
            if urllib.request.urlopen(DASH, timeout=2).status == 200:
                return A
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Flask 未就绪")


def launch():
    assert os.path.exists(CLOAK), "破解版浏览器不存在，按用户要求必须用它跑"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(PORT)
    co.set_argument(f"--user-data-dir={os.path.join(BASE, 'browser_data', 'e2e')}")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1440,900")
    return ChromiumPage(co)


COLLECT_ERRORS = '''(function(){
  window.__jsErrors = window.__jsErrors || [];
  window.addEventListener("error", function(e){ window.__jsErrors.push(String(e.message)); });
  window.addEventListener("unhandledrejection", function(e){
    window.__jsErrors.push("promise: " + String(e.reason)); });
})()'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    print("== 起后端 ==")
    A = start_flask()
    print("== 起破解版浏览器 ==")
    page = launch()

    # ── 1. 加载 ──
    page.get(DASH)
    js(page, COLLECT_ERRORS)
    time.sleep(4)
    check("加载", "标题正确", "Boss直聘" in (page.title or ""), page.title)
    check("加载", "主标题在页面上", "自动投递" in (js(page, 'document.body.innerText') or ""))
    check("加载", "WebSocket 已连上",
          "已连接" in (js(page, 'document.getElementById("connStatus").textContent') or ""))

    # ── 2. 指标卡与后端一致 ──
    cards = js(page, '''(function(){var c=document.querySelectorAll(".stat-card"),r=[];
      for(var i=0;i<c.length;i++)r.push({label:(c[i].querySelector(".stat-label")||{}).textContent,
      value:(c[i].querySelector(".stat-value")||{}).textContent,
      sub:(c[i].querySelector(".stat-sub")||{}).textContent});return JSON.stringify(r);})()''')
    check("指标", "4 张卡", len(cards) == 4, [c["label"] for c in cards])
    # fetch 是异步的，用同步 XHR 拿后端值再比
    api = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/metrics",false);x.send();return JSON.parse(x.responseText);})()''')
    check("指标", "累计投递与后端一致",
          str(api["total"]["greet_sent"]) == cards[0]["value"].strip(),
          f"后端{api['total']['greet_sent']} 前端{cards[0]['value']}")
    check("指标", "每日上限取自配置", "150" in (cards[1]["sub"] or ""), cards[1]["sub"])
    chips = js(page, '''(function(){var b=document.querySelectorAll("#metricsScope .scope-chip"),r=[];
      for(var i=0;i<b.length;i++)r.push(b[i].textContent);return JSON.stringify(r);})()''')
    check("指标", "账号范围切换可用", len(chips) >= 3, chips)
    js(page, 'document.querySelectorAll("#metricsScope .scope-chip")[2].click()')
    time.sleep(1.5)
    v2 = js(page, 'document.getElementById("statTotal").textContent')
    check("指标", "切到账号2 后数值随之变化", str(v2) != str(cards[0]["value"]),
          f"全部={cards[0]['value']} 账号2={v2}")
    js(page, 'document.querySelectorAll("#metricsScope .scope-chip")[0].click()')
    time.sleep(1.2)

    # ── 3. 投递记录表 + 筛选 ──
    rows_all = js(page, 'document.querySelectorAll(".greet-table tbody tr").length')
    check("记录", "投递记录有数据", rows_all and int(rows_all) > 0, rows_all)
    picked = js(page, '''(function(){var sel=document.getElementById("greetFilterStatus");
      if(!sel) return "no-select"; sel.value="skip";
      sel.dispatchEvent(new Event("change")); return sel.value;})()''')
    check("记录", "状态筛选控件可定位", picked == "skip", picked)
    time.sleep(1.5)
    rows_filtered = js(page, 'document.querySelectorAll(".greet-table tbody tr").length')
    texts = js(page, '''(function(){var t=document.querySelectorAll(".greet-table tbody tr"),o=[];
      for(var i=0;i<Math.min(t.length,5);i++)o.push(t[i].innerText.replace(/\\s+/g," "));
      return JSON.stringify(o);})()''')
    check("记录", "状态筛选生效（行数变少且都是跳过）",
          rows_filtered is not None and int(rows_filtered) <= int(rows_all)
          and all("跳过" in (x or "") for x in texts),
          f"全部{rows_all} 筛选后{rows_filtered}")
    js(page, 'resetGreetFilter && resetGreetFilter()')
    time.sleep(1)

    # ── 4. 聊天面板：渲染顺序必须与后端数组一致，且按各自账号的文件读 ──
    from boss_bot.message_store import MessageStore
    check("聊天", "会话列表渲染",
          js(page, 'document.querySelectorAll(".boss-chat-item").length') > 0,
          js(page, 'document.querySelectorAll(".boss-chat-item").length'))

    def chat_candidates(acct):
        st = MessageStore(account_index=acct)
        own = [c for c in st.get_chat_list()
               if c.get("message_count") and int(c.get("account_index") or 0) == acct]
        return st, own

    acct0_store, acct0_chats = chat_candidates(0)
    _, acct1_chats = chat_candidates(1)

    def ui_rows(page):
        """前端画出来的每一行：正文（气泡或卡片）+ 左右方向 + 时间标签"""
        raw = js(page, '''(function(){var b=document.getElementById("bossChatMessages");
          if(!b)return "[]";var rows=b.querySelectorAll(".boss-msg-row"),o=[];
          for(var i=0;i<rows.length;i++){
            var el=rows[i].querySelector(".boss-msg-bubble")
                  ||rows[i].querySelector(".boss-msg-card");
            var t=rows[i].querySelector(".boss-msg-time");
            // 方向要看 class 里的 me 这个词，不能用 indexOf：
            // "system" 里也含 "me"，系统条会被误判成我方消息
            var mine=/(^|\\s)me(\\s|$)/.test(rows[i].className);
            o.push({mine:mine,
                    text:(el?(el.textContent||""):"").replace(/\\s+/g," ").trim(),
                    time:(t?(t.textContent||""):"").trim()});}
          return JSON.stringify(o);})()''')
        # js() 已经把 JSON 字符串还原成对象了，这里不能再 loads 一次
        return raw if isinstance(raw, list) else json.loads(raw or "[]")

    def expected_rows(store, chat):
        """后端该画成什么样：卡片/系统行也要有可见文本，引擎自记的 action 不进气泡序列"""
        msgs = store.get_messages(chat["chat_name"], chat.get("job_name", ""),
                                  chat.get("company", ""))
        out = []
        for m in msgs:
            if m.get("kind") == "action":
                continue
            text = ((m.get("text") or m.get("content") or m.get("card_text") or "")
                    .replace("\n", " ").strip())
            out.append({"mine": bool(m.get("is_mine")), "text": text,
                        "time": (m.get("time") or "").strip()})
        return out

    for acct, cand in ((0, acct0_chats), (1, acct1_chats)):
        if not cand:
            continue
        store = MessageStore(account_index=acct)
        target = max(cand, key=lambda c: c["message_count"])
        # 选择会话必须用身份（姓名+公司），昵称本身在两个号上都会撞车
        js(page, f'selectBossChat({json.dumps(target["chat_id"], ensure_ascii=False)})')
        time.sleep(2)
        ui = ui_rows(page)
        be = expected_rows(store, target)
        name = f'{target["chat_name"]}|{(target.get("company") or "")[:10]}'
        tag = f"聊天(账号{acct + 1})"
        check(tag, "前端行数 = 后端消息数", len(ui) == len(be),
              f"{name} 前端{len(ui)} 后端{len(be)}")
        bad = sum(1 for a, b in zip(ui, be) if a["text"] != b["text"])
        check(tag, "逐条文本与线上存储一致", bad == 0, f"{bad} 条文本不符")
        order_bad = sum(1 for a, b in zip(ui, be)
                        if a["text"] and b["text"] and a["text"] != b["text"])
        check(tag, "顺序一致（不重排）", order_bad == 0, f"{order_bad} 处错位")
        time_bad = sum(1 for a, b in zip(ui, be) if a["time"] != b["time"])
        check(tag, "时间标签照搬线上", time_bad == 0, f"{time_bad} 条时间不符")
        mine_ok = all(a["mine"] == b["mine"] for a, b in zip(ui, be))
        check(tag, "我方/对方方向正确", mine_ok)
        page.get_screenshot(path=os.path.join(SHOTS, f"chat_a{acct}.png"))

    # ── 4b. 同昵称的两段对话必须互不串台 ──
    # 实测侧栏 34 行里 4 组重名（陈女士/唐女士/刘女士/易女士），
    # 以前按昵称存文件，点开一个就把两个人的话并在一起显示
    names = {}
    for c in acct0_chats:
        names.setdefault(c["chat_name"], []).append(c)
    dups = {k: v for k, v in names.items() if len(v) > 1}
    if dups:
        name, two = next(iter(dups.items()))
        per_identity = []
        for chat in two:
            js(page, 'selectBossChat(%s)' % json.dumps(chat["chat_id"], ensure_ascii=False))
            time.sleep(1.6)
            rows = ui_rows(page)
            be = expected_rows(MessageStore(account_index=0), chat)
            per_identity.append((chat["chat_id"], rows, be,
                                 {m.get("mid") for m in
                                  MessageStore(account_index=0).get_messages(
                                      chat["chat_name"], chat.get("job_name", ""),
                                      chat.get("company", ""))
                                  if m.get("mid")}))
        each_matches_own_file = all(len(r) == len(b) and
                                    all(a["text"] == c["text"] for a, c in zip(r, b))
                                    for _, r, b, _ in per_identity)
        # 两路的正文可以撞车（PK 分析卡那句话 BOSS 给谁都一样），
        # 真正能证明"没并成一个文件"的是 data-mid：线上每条唯一
        mids = [s for _, _, _, s in per_identity]
        disjoint = all(mids) and not set.intersection(*[set(s) for s in mids])
        check("会话身份", f"同昵称 {name}×{len(two)} 各自显示自己那一路",
              each_matches_own_file, "点开后读到的不是该身份自己的文件")
        check("会话身份", f"同昵称 {name}×{len(two)} 的 data-mid 互不重叠",
              disjoint, f"mid 集合={mids}")
    else:
        check("会话身份", "存在同昵称多路会话可供校验", False,
              "当前账号没有重名会话，这条没真正验到")
    check("会话身份", "列表把身份显示出来（姓名+公司）",
          js(page, '''(function(){var it=document.querySelectorAll(".boss-chat-item-job");
            for(var i=0;i<it.length;i++){if((it[i].textContent||"").trim())return true;}
            return false;})()'''),
          "副标题（公司）没显示，同名会话在列表里分不出来")

    # ── 5. AI 体检（真实网络请求） ──
    js(page, 'toggleAiProviders()')
    time.sleep(1)
    dots = js(page, 'document.querySelectorAll("#aiProviders .ai-status-dot").length')
    provs = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/ai/health",false);x.send();
      var d=JSON.parse(x.responseText);return d.summary;})()''')
    check("AI", "每个接口都有状态点", int(dots) >= provs["total"], f"{dots} vs {provs['total']}")
    check("AI", "体检结果已落盘并显示", provs["available"] >= 1, provs)
    js(page, 'runAiHealth([2])')
    st = "probing"
    for _ in range(16):          # 真实网络请求，慢接口要等，固定 sleep 会误报
        time.sleep(2)
        st = js(page, '''(function(){var d=document.getElementById("aiDot-2");
          return d?d.className:"none";})()''') or "none"
        if "available" in st:
            break
    check("AI", "单个重测后状态更新", "available" in st or "unavailable" in st, st)

    # ── 5b. 判分预算 / 判分质量 / 提示词默认值 ──
    budget = js(page, 'document.getElementById("aiAnalyzeMaxTokens").value')
    check("AI", "判分预算输入框已按配置回填", str(budget or "").isdigit() and int(budget) >= 512,
          f"值={budget}")
    cfg_budget = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/config",false);x.send();
      return JSON.parse(x.responseText).config.ai.analyze_max_tokens;})()''')
    check("AI", "预算已存进后端配置", int(cfg_budget or 0) == int(budget),
          f"后端 {cfg_budget} vs 界面 {budget}")

    quality = js(page, 'document.getElementById("aiQualityLine").textContent') or ""
    check("AI", "判分质量行有统计", ("判分质量" in quality) and
          ("真判分" in quality or "暂无记录" in quality), quality[:60])
    qapi = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/ai/quality",false);x.send();return JSON.parse(x.responseText);})()''')
    check("AI", "判分质量接口给出兜底率", qapi.get("status") == "ok" and
          "fallback_rate" in qapi and qapi.get("total", 0) >= 1,
          {k: qapi.get(k) for k in ("total", "judged", "fallback", "fallback_rate")})

    # 「恢复默认」必须用引擎那份 20 条的默认规则，不是前端抄本
    js(page, 'typeof DEFAULT_SYSTEM_RULES')
    stale = js(page, '(function(){try{return String(eval("DEFAULT_SYSTEM_RULES")).length;}'+
                     'catch(e){return "none";}})()')
    check("AI", "前端不再自带默认规则抄本", str(stale) == "none", stale)
    js(page, 'showPromptModal()')
    time.sleep(1.5)
    js(page, 'resetPromptModal()')
    time.sleep(1.0)
    sys_rules = js(page, 'document.getElementById("promptSystem").value') or ""
    check("AI", "恢复默认拿到的是引擎默认规则",
          "必须根据完整对话上下文回复" in sys_rules and "语气专业" in sys_rules,
          f"{len(sys_rules)} 字")
    # 只读校验，不点保存：用户改过的提示词不能被测试覆盖
    js(page, 'closePromptModal()')

    # ── 6. 配置保存回路（改→存→读回→还原） ──
    orig = js(page, 'document.getElementById("aiThreshold").value')
    js(page, '''(function(){var e=document.getElementById("aiThreshold");
      e.value="66";e.dispatchEvent(new Event("change"));})()''')
    time.sleep(1.5)
    js(page, 'saveConfigShow()')
    time.sleep(2.5)
    back = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/config",false);x.send();return JSON.parse(x.responseText);})()''')
    th = (back.get("config") or back).get("ai", {}).get("match_threshold")
    check("配置", "改动能存进后端", str(th) == "66", f"读回 {th}")
    toast = js(page, 'document.querySelectorAll(".toast").length')
    check("配置", "保存有轻提示反馈", int(toast or 0) >= 1, toast)
    js(page, '''(function(){var e=document.getElementById("aiThreshold");
      e.value="%s";e.dispatchEvent(new Event("change"));})()''' % orig)
    time.sleep(1)
    js(page, 'saveConfig()')
    time.sleep(2)

    # ── 6b. 演练模式开关走真实 UI 路径 + 整体回写不得抹掉未建模字段 ──
    cfg_file = os.path.join(BASE, "bot_config.json")
    theme_before = ""
    try:
        with open(cfg_file, encoding="utf-8") as f:
            theme_before = json.load(f).get("theme", "")
    except Exception:
        pass
    js(page, 'toggleAdvBool("dry_run")')      # 点开关：改值 + 存盘 + 轻提示
    time.sleep(2.5)
    dr = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/config",false);x.send();
      var c=JSON.parse(x.responseText);return (c.config||c).dry_run;})()''')
    check("演练", "开关改动存得进后端", bool(dr), f"读回 {dr}")
    saved_theme = ""
    try:
        with open(cfg_file, encoding="utf-8") as f:
            saved_theme = json.load(f).get("theme", "")
    except Exception:
        pass
    check("配置", "前端整体回写不抹掉 theme", saved_theme == theme_before and bool(saved_theme),
          f"改前={theme_before} 改后={saved_theme}")
    with open(cfg_file, encoding="utf-8") as f:
        saved_dry = json.load(f).get("dry_run")
    check("演练", "演练模式已落盘为 true", saved_dry is True, saved_dry)

    # 另两个运行时开关：跳过不可用AI接口 / 自进化记录
    get_cfg = lambda expr: js(page, '(function(){var x=new XMLHttpRequest();'
                                    'x.open("GET","/api/config",false);x.send();'
                                    'var c=JSON.parse(x.responseText);c=c.config||c;'
                                    'return ' + expr + ';})()')
    for tid, key, expr in (("advSkipUnhealthy", "skip_unhealthy", "(c.ai||{}).skip_unhealthy"),
                           ("advSelfEvolve", "self_evolve", "c.self_evolve_enabled")):
        before = js(page, "document.getElementById('%s').classList.contains('on')" % tid)
        js(page, 'toggleAdvBool("%s")' % key)
        time.sleep(2.2)
        back = get_cfg(expr)
        check("开关", f"{tid} 改动存得进后端", str(bool(back)) == str(not bool(before)),
              f"点前={before} 读回={back}")
        js(page, 'toggleAdvBool("%s")' % key)
        time.sleep(2.2)
    js(page, 'toggleAdvBool("dry_run")')      # 还原
    time.sleep(2.5)
    with open(cfg_file, encoding="utf-8") as f:
        restored_dry = json.load(f).get("dry_run")
    check("演练", "再点一次能关回 false", restored_dry is False, restored_dry)

    # ── 7. 弹窗 / Esc / 主题 / 断线横幅 ──
    js(page, 'showTemplatesModal()')
    time.sleep(1.2)
    opened = js(page, 'document.querySelectorAll(".modal-overlay").length')
    check("交互", "编辑模板弹窗能打开", int(opened or 0) >= 1, opened)
    try:
        page.actions.key_press("esc")
    except Exception:
        js(page, 'document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape"}))')
    time.sleep(1)
    closed = js(page, 'document.querySelectorAll(".modal-overlay").length')
    check("交互", "Esc 能关弹窗", int(closed or 0) == 0, closed)
    theme0 = js(page, 'document.documentElement.getAttribute("data-theme")')
    js(page, 'toggleTheme()')
    time.sleep(1.2)
    theme1 = js(page, 'document.documentElement.getAttribute("data-theme")')
    check("交互", "主题切换生效", theme0 != theme1, f"{theme0} -> {theme1}")
    js(page, 'showConnLost("测试")')
    time.sleep(0.6)
    banner = js(page, '!!document.getElementById("connLost")')
    js(page, 'hideConnLost()')
    time.sleep(0.6)
    gone = js(page, '!!document.getElementById("connLost")')
    check("交互", "断线横幅出现/消失", banner and not gone, f"{banner}/{gone}")

    # ── 8. 响应式与可达性 ──
    js(page, 'toggleTheme()')
    cols = js(page, '''(function(){var g=document.querySelector(".stats-grid");
      return getComputedStyle(g).gridTemplateColumns.split(" ").length;})()''')
    check("布局", "宽屏 4 列", int(cols) == 4, cols)
    no_title = js(page, '''(function(){var b=document.querySelectorAll("button"),n=0;
      for(var i=0;i<b.length;i++){var t=(b[i].textContent||"").trim();
      if(!t && !b[i].getAttribute("title") && !b[i].getAttribute("aria-label"))n++;}
      return n;})()''')
    check("可达性", "无文字按钮都有 title/aria-label", int(no_title or 0) == 0, no_title)
    # ── 5b. 多账号范围：切账号要带着记录、聊天、导出、按钮一起切 ──
    def api_get(path):
        raw = js(page, '''(function(){var x=new XMLHttpRequest();
          x.open("GET", %s, false); x.send();
          return x.status + "|" + x.responseText;})()''' % json.dumps(path))
        status, _, body = str(raw).partition("|")
        return int(status), json.loads(body) if body else {}

    st_a1, d_a1 = api_get("/api/greet_records?account=1")
    st_a0, d_a0 = api_get("/api/greet_records?account=0")
    st_all, d_all = api_get("/api/greet_records")
    check("多账号", "打招呼记录接口按账号分",
          st_a1 == 200 and d_a1["total"] < d_all["total"] and d_a0["total"] < d_all["total"],
          f"全部{d_all['total']} 主{d_a0['total']} 二号{d_a1['total']}")
    check("多账号", "记录里的账号字段正确",
          all(r.get("account_index") == 1 for r in d_a1["records"]) and
          all(r.get("account_index") == 0 for r in d_a0["records"]),
          set(r.get("account_index") for r in d_a1["records"]))
    check("多账号", "不再有 cookie 文件名当账号名",
          not any(str(r.get("account_name", "")).endswith(".json") for r in d_all["records"]),
          [r.get("account_name") for r in d_all["records"][:3]])

    js(page, 'document.querySelectorAll("#metricsScope .scope-chip")[2].click()')
    time.sleep(2.5)
    rows_a1 = int(js(page, 'document.querySelectorAll(".greet-table tbody tr").length') or 0)
    check("多账号", "切到账号2 后打招呼记录只剩该账号",
          rows_a1 == int(d_a1["total"]),
          f"页面{rows_a1} 接口{d_a1['total']}")   # 行模型统一后不再截到 200 行
    hint = js(page, 'document.getElementById("actionScopeLabel").textContent') or ""
    check("多账号", "操作条标明当前范围", "账号2" in hint, hint)
    labels = js(page, '''(function(){var r=[],b=["btnPauseGreet","btnPauseReply"];
      for(var i=0;i<b.length;i++){var e=document.getElementById(b[i]);
      r.push(e?e.textContent:"");} return JSON.stringify(r);})()''')
    check("多账号", "暂停按钮写着只对当前账号生效",
          all("账号2" in (x or "") for x in labels), labels)

    chats_all, dc_all = api_get("/api/reply_records/grouped")
    chats_a1, dc_a1 = api_get("/api/reply_records/grouped?account=1")
    check("多账号", "回复分组接口按账号分",
          dc_a1["total_groups"] < dc_all["total_groups"] or dc_a1["total_groups"] > 0,
          f"全部{dc_all['total_groups']} 二号{dc_a1['total_groups']}")
    check("多账号", "回复分组带账号字段",
          all(g.get("account_index") == 1 for g in dc_a1["groups"]),
          [g.get("account_index") for g in dc_a1["groups"][:5]])
    time.sleep(1.5)
    ui_chats = int(js(page, 'document.querySelectorAll(".boss-chat-item").length') or 0)
    check("多账号", "聊天列表跟着账号切",
          ui_chats == int(dc_a1["total_groups"]), f"页面{ui_chats} 接口{dc_a1['total_groups']}")

    # 只记录不真发：拦住 fetch / window.open / confirm，看前端准备往哪儿打
    js(page, '''(function(){
      window.__fired=[]; window.__opened=[]; window.__confirm=[];
      window.__of=window.fetch; window.__ow=window.open; window.__oc=window.confirm;
      window.fetch=function(u,o){window.__fired.push((o&&o.method||"GET")+" "+u);
        return Promise.resolve(new Response(JSON.stringify({status:"ok"}),
          {status:200,headers:{"Content-Type":"application/json"}}));};
      window.open=function(u){window.__opened.push(u);return null;};
      window.confirm=function(m){window.__confirm.push(m);return false;};
    })()''')
    js(page, 'pauseGreet(); resumeGreet(); pauseReply(); resumeReply();'
             ' downloadGreetRecords(); downloadReplyRecords();'
             ' loginPendingIdx=1; confirmLogin();')
    time.sleep(1.2)
    fired = js(page, 'JSON.stringify(window.__fired)') or []
    opened = js(page, 'JSON.stringify(window.__opened)') or []
    check("多账号", "暂停/恢复走单账号路由",
          any("POST /api/accounts/1/pause_greet" == x for x in fired) and
          any("POST /api/accounts/1/resume_reply" == x for x in fired), fired)
    check("多账号", "导出链接带账号参数",
          any("/api/export/greet_records" in u and "account=1" in u for u in opened), opened)
    check("多账号", "登录确认只发给要登录的号",
          "POST /api/accounts/1/confirm_login" in fired, fired)

    # fetch 还拦着：此时 /api/config 回来的是 {status:"ok"}，没有 accounts。
    # 界面必须保持原配置——整份赋值会把账号列表、招呼语、AI 配置一起抹掉
    js(page, '(function(){window.__accBefore=(config.accounts||[]).length;'
             ' loadConfig(); return 1;})()')
    time.sleep(1.0)
    acc_after = js(page, '(config.accounts||[]).length')
    check("配置异常响应", "接口返回没有 accounts 时不覆盖界面配置",
          acc_after == js(page, 'window.__accBefore') and acc_after >= 2,
          f"前 {js(page, 'window.__accBefore')} → 后 {acc_after}")

    js(page, 'setDataScope("all")')
    time.sleep(1.5)
    js(page, 'window.__fired = [];')
    js(page, 'pauseGreet(); confirmLoginAs(null);')
    time.sleep(1.0)
    fired_all = js(page, 'JSON.stringify(window.__fired)') or []
    check("多账号", "全部账号范围才用全局路由",
          "POST /api/pause_greet" in fired_all
          and "POST /api/confirm_login" in fired_all, fired_all)

    js(page, 'setDataScope(1)')
    time.sleep(1.2)
    # 这一步要看的是"清空确认里写的是哪个号"，而拦住的 fetch 会让
    # 切范围时的重拉拿到异常响应。先把真 fetch 放回来（confirm 钩子留着）。
    js(page, 'window.fetch = window.__of;')
    js(page, 'setDataScope(1)')
    time.sleep(1.5)
    js(page, 'clearGreetTable(); clearReplyRecords();')
    time.sleep(0.8)
    confirms = js(page, 'JSON.stringify(window.__confirm)') or []
    check("多账号", "清空前说清会不会波及其他账号",
          len(confirms) >= 2 and all("账号2" in c for c in confirms)
          and any("不受影响" in c for c in confirms), confirms[:2])
    js(page, '''(function(){window.fetch=window.__of;window.open=window.__ow;
      window.confirm=window.__oc;})()''')
    # 上面拦过 fetch，表被空响应清掉了，恢复真 fetch 后重新拉一次
    js(page, 'applyScopeToView()')
    time.sleep(2.5)

    scoped = js(page, 'JSON.stringify([inScope({account_index:0}),'
                      ' inScope({account_index:1})])')
    check("多账号", "范围外的实时行被挡掉", scoped == [False, True], scoped)
    rows_1 = int(js(page, 'document.querySelectorAll(".greet-table tbody tr").length') or 0)
    check("多账号", "恢复真接口后账号2 记录数对得上",
          rows_1 == int(d_a1["total"]), f"页面{rows_1} 接口{d_a1['total']}")

    dots = js(page, '''(function(){var d=document.querySelectorAll(".account-tab .cookie-status-dot"),r=[];
      for(var i=0;i<d.length;i++)r.push(d[i].className.replace("cookie-status-dot","").trim()+"|"+d[i].title);
      return JSON.stringify(r);})()''')
    check("多账号", "Cookie 点已自动刷新（不靠手点）",
          len(dots) >= 2 and not any(x.startswith("invalid") for x in dots), dots)
    acc_dots = js(page, '''(function(){var d=document.querySelectorAll(".account-tab .acc-status-dot"),r=[];
      for(var i=0;i<d.length;i++)r.push(d[i].className.replace("acc-status-dot","").trim()+"|"+d[i].title);
      return JSON.stringify(r);})()''')
    check("多账号", "账号状态点能说明阶段",
          all(("未启动" in x or "运行中" in x or "等你登录" in x or "初始化" in x)
              for x in acc_dots), acc_dots)

    js(page, 'setDataScope("all")')
    time.sleep(2.5)
    check("多账号", "切回全部账号后实时行不再过滤",
          js(page, 'JSON.stringify(inScope({account_index:0}))') == "true")
    rows_all_after = int(js(page, 'document.querySelectorAll(".greet-table tbody tr").length') or 0)
    check("多账号", "切回全部账号能看到所有记录",
          rows_all_after == int(d_all["total"]),
          f"页面{rows_all_after} 接口{d_all['total']}")

    errs = js(page, 'JSON.stringify(window.__jsErrors||[])')
    check("稳定性", "操作过程中无 JS 报错", not errs, errs)

    page.get_screenshot(path=os.path.join(SHOTS, "final.png"))
    bad = [r for r in RESULTS if not r["ok"]]
    print(f"\n== 合计 {len(RESULTS)} 项，失败 {len(bad)} 项 ==")
    for r in bad:
        print(f"  FAIL {r['group']}/{r['name']}: {r['detail']}")
    with open(os.path.join(SHOTS, "report.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
