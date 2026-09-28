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

    # ── 4. 聊天面板：渲染顺序必须与后端数组一致 ──
    from boss_bot.message_store import MessageStore
    store = MessageStore(account_index=0)
    listing = [c for c in store.get_chat_list() if c.get("message_count")]
    check("聊天", "会话列表渲染",
          js(page, 'document.querySelectorAll(".boss-chat-item").length') > 0,
          js(page, 'document.querySelectorAll(".boss-chat-item").length'))
    if listing:
        target = max(listing, key=lambda c: c["message_count"])
        name = target["chat_name"]
        js(page, f'selectBossChat({json.dumps(name)})')
        time.sleep(2)
        ui = js(page, '''(function(){var b=document.getElementById("bossChatMessages");
          if(!b)return "[]";var rows=b.querySelectorAll(".boss-msg-row"),o=[];
          for(var i=0;i<rows.length;i++){var el=rows[i].querySelector(".boss-msg-bubble")
            ||rows[i].querySelector(".boss-msg-card");
          o.push({mine:rows[i].className.indexOf("me")>=0,
                  text:(el?(el.textContent||""):"").replace(/\\s+/g," ").trim()});}
          return JSON.stringify(o);})()''')
        be = [m for m in store.get_messages(name)
              if (m.get("text") or m.get("content") or "").strip()]
        check("聊天", "前端气泡数 = 后端消息数", len(ui) == len(be),
              f"前端{len(ui)} 后端{len(be)}")
        bad = 0
        for a, b in zip(ui, be):
            ta = (a["text"] or "")[:14]
            tb = ((b.get("text") or b.get("content") or "").replace("\n", " ").strip())[:14]
            if ta and tb and ta not in tb and tb not in ta:
                bad += 1
        check("聊天", "逐条顺序与文本一致", bad == 0, f"{bad} 条错位")
        mine_ok = all(a["mine"] == bool(b.get("is_mine")) for a, b in zip(ui, be))
        check("聊天", "我方/对方方向正确", mine_ok)
        page.get_screenshot(path=os.path.join(SHOTS, "chat.png"))

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
    time.sleep(9)
    st = js(page, '''(function(){var d=document.getElementById("aiDot-2");
      return d?d.className:"none";})()''')
    check("AI", "单个重测后状态更新", "available" in st or "unavailable" in st, st)

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
