# -*- coding: utf-8 -*-
"""仪表盘界面验证 — 指标卡 + 账号范围 + AI 接口体检区

真机打开 Flask 页面读 DOM，确认前端确实按新口径出数，而不是只改了后端。
用项目自带的破解版浏览器（boss 有反爬，一律走它），并且用独立 profile，
不碰你已登录的 BOSS 会话。

用法：python tools/verify_dashboard_ui.py
"""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage
from boss_bot.platform_compat import 破解版路径

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = 破解版路径(BASE)
PORT = 9399
SHOT = os.path.join(BASE, "tools", "verify_dashboard.png")
URL = "http://127.0.0.1:5000"

out = {"checks": [], "errors": []}


def check(name, ok, detail=""):
    out["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("  PASS  " if ok else "  FAIL  ") + name + ("  " + str(detail)[:120] if detail else ""))


def start_flask():
    """在当前进程里起 Flask，屏蔽自动打开浏览器那一步。"""
    sys.path.insert(0, os.path.join(BASE, "flask-version"))
    import app as A
    A._open_dashboard = lambda *a, **k: None
    A._auto_ai_health = lambda *a, **k: None
    kwargs = {"host": "127.0.0.1", "port": 5000, "debug": False, "use_reloader": False}
    if A._socketio_kwargs.get("async_mode") == "threading":
        kwargs["allow_unsafe_werkzeug"] = True
    t = threading.Thread(target=lambda: A.socketio.run(A.app, **kwargs), daemon=True)
    t.start()
    for _ in range(60):
        try:
            import urllib.request
            if urllib.request.urlopen(URL, timeout=2).status == 200:
                return A
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Flask 没起来")


def main():
    print("1) 启动 Flask ...")
    A = start_flask()
    print("   ok")

    # 先做一轮体检，让界面有东西可显示（复用真实探测逻辑）
    print("2) 触发 AI 接口体检（只测前 3 个，省时间）...")
    started, msg = A._start_ai_health([0, 1, 2])
    check("体检可启动", started, msg)
    for _ in range(60):
        if not A._ai_health_running:
            break
        time.sleep(1)

    print("3) 用破解版浏览器打开页面 ...")
    assert os.path.exists(CLOAK), f"破解版浏览器不存在: {CLOAK}"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(PORT)
    co.set_argument(f"--user-data-dir={os.path.join(BASE, 'browser_data', 'ui_verify')}")
    co.set_argument("--disable-blink-features=AutomationControlled")
    page = ChromiumPage(co)
    page.get(URL)
    time.sleep(4)

    cards = page.run_js('''(
        function(){
            var cs = document.querySelectorAll(".stat-card");
            var r = [];
            for (var i=0;i<cs.length;i++) {
                r.push({
                    label: (cs[i].querySelector(".stat-label")||{}).textContent || "",
                    value: (cs[i].querySelector(".stat-value")||{}).textContent || "",
                    sub: (cs[i].querySelector(".stat-sub")||{}).textContent || ""
                });
            }
            return JSON.stringify(r);
        }
    )()''', as_expr=True)
    cards = json.loads(cards or "[]")
    print("4) 指标卡：")
    for c in cards:
        print(f"   {c['label']} = {c['value']}  ({c['sub']})")
    labels = [c["label"] for c in cards]
    check("指标卡是 4 张", len(cards) == 4, labels)
    check("含累计投递/已投递/接收简历/面试数",
          labels == ["累计投递", "已投递", "接收简历", "面试数"], labels)
    applied = next((c for c in cards if c["label"] == "已投递"), {})
    check("已投递小字带每日上限", "每日上限" in (applied.get("sub") or ""),
          applied.get("sub"))
    check("上限取自配置(150)", "150" in (applied.get("sub") or ""), applied.get("sub"))
    interview = next((c for c in cards if c["label"] == "面试数"), {})
    check("面试数小字说明按会话去重", "按会话去重" in (interview.get("sub") or ""),
          interview.get("sub"))

    # 账号范围切换（两个账号时才出现）
    scope = page.run_js('document.getElementById("metricsScope").innerText', as_expr=True)
    check("出现账号范围切换", scope and "全部账号" in scope, (scope or "").replace("\n", " "))
    chips = page.run_js('''(
        function(){var b=document.querySelectorAll("#metricsScope .scope-chip");
        var t=[];for(var i=0;i<b.length;i++)t.push(b[i].textContent);return JSON.stringify(t);})()''',
        as_expr=True)
    chips = json.loads(chips or "[]")
    check("范围含主账号与账号2", len(chips) >= 3, chips)
    page.run_js('document.querySelectorAll("#metricsScope .scope-chip")[2].click()', as_expr=True)
    time.sleep(1.5)
    active = page.run_js('''(
        function(){var b=document.querySelectorAll("#metricsScope .scope-chip.active");
        return b.length?b[0].textContent:"";})()''', as_expr=True)
    check("点单账号后高亮切换", active and active != "全部账号", active)

    # AI 体检区
    page.run_js("toggleAiProviders()", as_expr=True)
    time.sleep(1.5)
    page.run_js("loadAiHealth()", as_expr=True)
    time.sleep(2)
    summary = page.run_js('document.getElementById("aiHealthSummary").innerText', as_expr=True)
    print("5) AI 体检汇总：", summary)
    check("体检汇总显示可用数", "可用" in (summary or ""), summary)
    dots = page.run_js('''(
        function(){var d=document.querySelectorAll("#aiProviders .ai-status-dot");
        var s={};for(var i=0;i<d.length;i++){var k=d[i].className.split(" ")[1]||"?";s[k]=(s[k]||0)+1;}
        return JSON.stringify(s);})()''', as_expr=True)
    print("   每个接口状态点：", dots)
    st = json.loads(dots or "{}")
    check("接口卡片带状态点", sum(st.values()) >= 20, st)
    check("已测接口标出可用", (st.get("available") or 0) >= 1, st)
    rows = page.run_js('''document.querySelectorAll("#aiProviders .ai-provider-reason").length''',
                       as_expr=True)
    check("不可用接口有原因文字", int(rows or 0) >= 1, rows)

    page.get_screenshot(path=SHOT, full_page=True)
    check("截图已保存", os.path.exists(SHOT), SHOT)

    ok = all(c["ok"] for c in out["checks"])
    print("\n结论：", "全部通过" if ok else "有未通过项")
    with open(os.path.join(BASE, "tools", "verify_dashboard_ui.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
