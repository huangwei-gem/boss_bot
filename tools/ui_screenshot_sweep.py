# -*- coding: utf-8 -*-
"""整屏巡检 — 把面板每个视图/弹窗/断点/主题都截一张，并顺手量出"哪里被切了"。

为什么单独做这个：改完一处只看那一处的截图，别处被挤坏、文字被截断、
弹窗在窄屏溢出这类问题看不见（2026-10-04 用户："改完之后全部检查一下页面，
不断的截图页面看看情况"）。所以这里既出图也出清单：
每张图配一段 DOM 体检（横向溢出、被 overflow 切掉的文字、看不见的控件），
图给人看，清单给回归用。

只读：切 tab、展开折叠、开弹窗、换主题、换数据范围，
不点启动/停止/发送/打招呼/发简历/清空，也不保存配置。

用法：
    python -X utf8 tools/ui_screenshot_sweep.py                # 自己起临时面板
    python -X utf8 tools/ui_screenshot_sweep.py --url http://127.0.0.1:5000
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage  # noqa: E402

CLOAK = str(BASE / "cloakbrowser" / "chrome.exe")
PORT = 9402
PANEL_PORT = 5059
OUT = BASE / "tools" / "e2e" / "shots"

# 每张图都跑一遍：横向溢出 / 文字被 overflow 切掉 / 有内容却量不到尺寸
PROBE_JS = r'''(function(){
  var vw = document.documentElement.clientWidth, out = [];
  function hiddenByAncestor(el){
    var p = el.parentElement;
    while (p && p !== document.body) {
      var cs = getComputedStyle(p);
      if (cs.display === 'none' || cs.visibility === 'hidden') return true;
      if (cs.overflow !== 'visible' && p.scrollHeight > p.clientHeight + 2) return true;
      p = p.parentElement;
    }
    return false;
  }
  function path(el){
    var s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else if (el.className && typeof el.className === 'string')
      s += '.' + el.className.trim().split(/\s+/).slice(0,2).join('.');
    return s;
  }
  var all = document.querySelectorAll('body *');
  for (var i = 0; i < all.length && out.length < 40; i++) {
    var el = all[i], cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    var txt = (el.textContent || '').replace(/\s+/g, '').slice(0, 18);
    if (!txt) continue;
    if (r.right > vw + 2 && cs.position !== 'fixed')
      out.push({kind: '横向溢出', el: path(el), detail: Math.round(r.right) + '>' + vw, text: txt});
    else if (cs.overflow !== 'visible' && el.scrollWidth > el.clientWidth + 3 &&
             el.clientWidth > 8 && !hiddenByAncestor(el))
      out.push({kind: '文字被切', el: path(el),
                detail: el.scrollWidth + '>' + el.clientWidth, text: txt});
  }
  return JSON.stringify(out);
})()'''

JS_ERRORS = '''(function(){
  window.__jsErrors = window.__jsErrors || [];
  window.addEventListener("error", function(e){ window.__jsErrors.push(String(e.message)); });
  window.addEventListener("unhandledrejection", function(e){
    window.__jsErrors.push("promise: " + String(e.reason)); });
})()'''

# (图名, 拍之前要跑的 JS, 截图目标 None=整页)
SCENARIOS = [
    ("01_default_1920", "", None),
    ("02_reply_tab", "switchRecordTab('reply')", None),
    ("03_boss_chat", "var it=document.querySelector('.boss-chat-item');if(it)it.click();",
     "#bossChatWrap"),
    ("04_log_expanded", "var p=document.getElementById('logPanel');"
                        "if(p.classList.contains('collapsed'))toggleLogPanel();"
                        "p.scrollIntoView({block:'end'});", None),
    ("05_side_advanced", "var t=document.getElementById('advToggle');"
                         "if(!t.classList.contains('open'))t.click();"
                         "document.getElementById('advSection').scrollIntoView({block:'center'});",
     None),
    ("06_modal_excel", "showExcelModal()", ".modal-overlay"),
    ("07_modal_resume", "showResumeModal()", ".modal-overlay"),
    ("08_modal_prompt", "showPromptModal()", ".modal-overlay"),
    ("09_modal_rules", "showRulesModal()", ".modal-overlay"),
    ("10_modal_templates", "showTemplatesModal()", ".modal-overlay"),
    ("11_modal_evolution", "showEvolutionModal()", ".modal-overlay"),
    ("12_modal_archive", "showArchiveModal()", ".modal-overlay"),
    ("13_ai_providers", "toggleAiProviders();"
                        "document.getElementById('aiProvSection').scrollIntoView({block:'center'});",
     None),
    ("14_config_preview", "var t=document.getElementById('configPreviewToggle');"
                          "if(t&&!t.classList.contains('open'))t.click();", None),
    ("15_light_theme", 'if(document.documentElement.getAttribute("data-theme")!=="light")'
                       'toggleTheme();', None),
    ("16_dark_theme", 'if(document.documentElement.getAttribute("data-theme")!=="dark")'
                      'toggleTheme();', None),
    ("17_scope_account2", "var b=document.querySelector('[data-scope=\"1\"]');if(b)b.click();",
     None),
    ("18_scope_all", "var b=document.querySelector('[data-scope=\"all\"]');if(b)b.click();",
     None),
]

WIDTHS = [(1920, 1080), (1600, 900), (1280, 800), (1100, 800), (900, 700)]


def js(page, code):
    raw = page.run_js(code, as_expr=True)
    if isinstance(raw, str) and raw[:1] in "[{":
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


# 弹窗是 fetch 回来才拼 DOM 的，等它真出现再拍；拍完两种遮罩都要收，
# 不然下一张图整屏都是这个弹窗（上一轮 13~18 号图就是这么废掉的）
OVERLAY_VISIBLE = '''(function(){var els=document.querySelectorAll(".modal-overlay,.login-modal-overlay");
  for(var i=0;i<els.length;i++){if(getComputedStyle(els[i]).display!=="none")return true;}
  return false;})()'''
CLOSE_ALL = ("document.querySelectorAll('.modal-overlay').forEach(function(e){e.remove();});"
             "document.querySelectorAll('.login-modal-overlay').forEach(function(e){e.style.display='none';});")


def shoot(page, name, target):
    if target:
        el = page.ele(target, timeout=4)
        if el:
            # 元素截图只拍它落在视口里的那一块，不先滚进来就永远只有开头
            page.run_js("var e=document.querySelector('%s');"
                        "if(e)e.scrollIntoView({block:'start'});" % target)
            time.sleep(.5)
            el.get_screenshot(str(OUT / f"{name}.png"))
            return True
    page.get_screenshot(path=str(OUT), name=f"{name}.png")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    args = ap.parse_args()

    tmp = proc = None
    url = args.url
    if url:
        # 带 --url 就是让脚本去点那个面板：5000 是线上投递用的面板，
        # 巡检会切范围、开弹窗、点开关，绝不能拿它当靶子
        assert ":5000" not in url or os.environ.get("BOSS_SWEEP_ALLOW_LIVE") == "1", \
            f"巡检脚本不得对着线上面板 {url} 点按钮（会打断投递）。去掉 --url 让它自起临时面板。"
    else:
        import e2e_account_scope_ui as E
        tmp = Path(tempfile.mkdtemp(prefix="boss_sweep_"))
        E.seed(tmp)
        proc = E.start_server(tmp, PANEL_PORT)
        url = f"http://127.0.0.1:{PANEL_PORT}"

    OUT.mkdir(parents=True, exist_ok=True)
    assert os.path.exists(CLOAK), "必须用项目自带的破解版浏览器"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(PORT)
    co.set_argument(f"--user-data-dir={BASE / 'browser_data' / 'e2e'}")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1920,1080")
    page = ChromiumPage(co)
    report = []
    try:
        page.get(url)
        for _ in range(30):
            if js(page, "!!document.getElementById('advToggle')"):
                break
            time.sleep(1)
        js(page, JS_ERRORS)
        time.sleep(3)

        # 自检：探针要能抓到一段明知会被切掉的文字，否则"0 处"只是它坏了
        page.run_js("var d=document.createElement('div');d.id='probeSelfTest';"
                    "d.style.cssText='width:70px;overflow:hidden;white-space:nowrap;"
                    "position:fixed;left:0;top:0;z-index:9999';"
                    "d.textContent='这是一段肯定装不下会被切掉的中文文字';"
                    "document.body.appendChild(d);")
        self_hit = [f for f in (js(page, PROBE_JS) or []) if f["el"].endswith("#probeSelfTest")]
        page.run_js("var d=document.getElementById('probeSelfTest');if(d)d.remove();")
        print("探针自检:", "OK（抓到被切的文字）" if self_hit else "失效——下面的 0 处不可信")

        for name, setup, target in SCENARIOS:
            if setup:
                page.run_js(setup)
            if target and ("modal" in target or "Modal" in setup):
                for _ in range(8):
                    if js(page, OVERLAY_VISIBLE):
                        break
                    time.sleep(.5)
            time.sleep(1.0)
            page.set.window.size(1920, 1080)
            time.sleep(.6)
            shoot(page, name, target)
            flags = js(page, PROBE_JS) or []
            report.append({"shot": name, "flags": flags})
            print(f"[{name}] 体检 {len(flags)} 处")
            for f in flags[:6]:
                print(f"    {f['kind']} {f['el']} {f['detail']} | {f['text']}")
            page.run_js(CLOSE_ALL)
            time.sleep(.4)

        page.run_js(CLOSE_ALL)
        page.run_js("switchRecordTab('greet');")
        for w, h in WIDTHS:
            page.set.window.size(w, h)
            time.sleep(1.0)
            name = f"w{w}"
            shoot(page, name, None)
            flags = js(page, PROBE_JS) or []
            report.append({"shot": name, "flags": flags})
            print(f"[{name}] 体检 {len(flags)} 处")
            for f in flags[:8]:
                print(f"    {f['kind']} {f['el']} {f['detail']} | {f['text']}")

        errs = js(page, 'JSON.stringify(window.__jsErrors||[])') or "[]"
        print("JS 报错:", errs[:400])
        (OUT / "sweep_report.json").write_text(
            json.dumps({"shots": report, "js_errors": errs}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        total = sum(len(r["flags"]) for r in report)
        print(f"\n== {len(report)} 张图，体检标记合计 {total} 处，图在 {OUT} ==")
    finally:
        try:
            page.quit()
        except Exception:
            pass
        if proc:
            proc.kill()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
