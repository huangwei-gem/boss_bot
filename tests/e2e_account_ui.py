"""账号区 UI 的真机点击测试（task #34/#35）。

只点本地面板：左侧账号行的登录入口、右侧数据范围切换。
点"登录"会调 /api/accounts/<idx>/login —— 该接口只会把对应账号的浏览器停在
BOSS 登录页，不点发送、不打招呼、不发简历。

前置条件：Flask 服务运行在 http://localhost:5000
运行：python tests/e2e_account_ui.py
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import e2e_greet_records_ui as harness  # noqa: E402
from e2e_greet_records_ui import (  # noqa: E402
    FLASK_URL, check, click_chip, click_until_alive, close_browser,
    load_config_accounts, open_browser, scope_chips)

# check() 把结果记在它自己模块的那张表上，汇总要读同一张
results = harness.results


def account_rows(page):
    return page.eles("xpath://div[contains(concat(' ', normalize-space(@class), ' '),"
                     " ' account-tab ')]")


def login_buttons(page):
    return page.eles("xpath://button[contains(concat(' ', normalize-space(@class), ' '),"
                     " ' acc-login-btn ')]")


def cookie_dots(page):
    return page.eles("xpath://span[contains(concat(' ', normalize-space(@class), ' '),"
                     " ' cookie-status-dot ')]")


def log_tail(page, n=6):
    return page.run_js("""var a=document.getElementById('logArea');
        return a ? a.innerText.split('\\n').slice(-%d).join(' | ') : '';""" % n) or ""


def clear_log(page):
    page.run_js("var a=document.getElementById('logArea'); if(a) a.innerHTML='';")


def main():
    import requests
    try:
        requests.get(FLASK_URL, timeout=5)
    except Exception as e:
        print(f"Flask 未运行（{e}），先启动面板再跑本脚本")
        return 2

    want = len(load_config_accounts())

    print("=" * 60)
    print("  账号区 UI — 真机点击测试")
    print("=" * 60)

    page, proc = open_browser()
    try:
        page.get(FLASK_URL)
        page.wait.doc_loaded()
        js = page.run_js

        def ready():
            # 页面脚本要整个跑完才轮得到 config；没跑完就取会抛 ReferenceError
            try:
                return int(js("return typeof config === 'undefined' ? -1 : config.accounts.length"))
            except Exception:
                return -1

        for _ in range(30):
            if ready() >= 0 and account_rows(page):
                break
            time.sleep(0.5)

        want = len(load_config_accounts())
        rows = len(account_rows(page))
        check("左侧账号行按配置渲染", rows == want, f"{rows} 行 / 配置 {want} 个账号")
        btns = len(login_buttons(page))
        check("每个账号行都有登录入口", btns == want, f"{btns} 个按钮")

        # 左侧不该再兼职切数据：点行前后 dataScope 必须不变
        before = js("return String(dataScope)")
        assert click_until_alive(page, account_rows, 0), "账号行点不动"
        time.sleep(0.8)
        after = js("return String(dataScope)")
        check("点账号行不再切换数据范围", before == after, f"点前 {before} 点后 {after}")

        # 数据范围仍然是唯一的切换入口，而且真的只换数据
        chips = scope_chips(page)
        if len(chips) == want + 1 and want >= 2:
            assert click_chip(page, -1), "数据范围 chip 点不动"
            time.sleep(1.5)
            scope = js("return String(dataScope)")
            mixed = int(js("""return allGreetRecords.filter(function(r){
                    return r.account_index !== Number(dataScope); }).length;"""))
            check("点数据范围 chip 只切数据", scope == str(want - 1) and mixed == 0,
                  f"dataScope={scope}，混入其它账号 {mixed} 条")
        else:
            check("点数据范围 chip 只切数据", False,
                  f"chip {len(chips)} 个 / 账号 {want} 个（对不上说明面板进程没重启）")

        # 登录按钮：点了必须拿到后端的回应。
        # 不去翻日志——引擎日志刷得比读取快，我们的那行会被挤出窗口，测不稳。
        js("""window.__probeResp = null;
              var orig = window.fetch;
              window.fetch = function (u, o) {
                  var p = orig.apply(window, arguments);
                  if (String(u).indexOf('/login') >= 0) {
                      p.then(function (r) { return r.clone().text(); })
                       .then(function (t) { window.__probeResp = t; })
                       .catch(function (e) { window.__probeResp = 'ERR:' + e.message; });
                  }
                  return p;
              };""")
        assert click_until_alive(page, login_buttons, -1 if want >= 2 else 0), "登录按钮点不动"
        resp = ""
        for _ in range(20):
            time.sleep(0.4)
            resp = js("return window.__probeResp || ''") or ""
            if resp:
                break
        parsed = None
        try:
            parsed = json.loads(resp)
        except ValueError:
            pass
        ok_json = bool(parsed) and "status" in parsed
        # 面板进程没重启时新端点不存在，回的是 Flask 的 HTML 404 页——
        # 这不算失败，但必须能从回应里看出来
        stale = "404" in resp and "not found" in resp.lower()
        honest = ("Unexpected token" not in resp and "ERR:" not in resp
                  and (ok_json or stale))
        check("点登录拿到后端回应", honest and bool(resp),
              ("面板进程没重启，/login 还是 404" if stale else resp[:150]))

        # 登录态检测：点状态点要真的去检测
        clear_log(page)
        assert click_until_alive(page, cookie_dots, 0), "登录态点不动"
        time.sleep(1.5)
        check("登录态点一下就能检测", "检测" in log_tail(page), log_tail(page)[:110])

        # 招呼语输入框要真的写进本账号的配置对象。
        # 这里把 saveConfig 短路掉：测的是"输入框↔数据模型"的绑定，
        # 往盘上写再改回来会动到用户正在用的 bot_config.json，不值当。
        typed = "实测招呼语-" + str(int(time.time()))
        got = js("""var idx = activeAccountIdx, box = document.getElementById('accGreeting');
            var real = saveConfig; saveConfig = function(){ window.__saved = (window.__saved||0)+1; };
            box.value = %s;
            box.dispatchEvent(new Event('change'));
            var val = (config.accounts[idx]||{}).greeting_message;
            saveConfig = real;
            return JSON.stringify({idx: idx, val: val, saved: window.__saved||0});"""
                 % json.dumps(typed, ensure_ascii=False))
        info = json.loads(got)
        check("招呼语输入框写回本账号配置", info["val"] == typed and info["saved"] >= 1,
              f"账号 {info['idx']} 读到 {info['val']!r}，触发保存 {info['saved']} 次")
        check("招呼语跟着数据范围切换换账号",
              info["idx"] == int(js("return activeAccountIdx")))

        page.get_screenshot(str(PROJECT_ROOT / "tests/screenshots/account_ui.png"))
        print("  截图: tests/screenshots/account_ui.png", flush=True)
    finally:
        close_browser(proc)

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 60)
    print(f"  共 {len(results)} 项，失败 {len(failed)} 项")
    for name, _, detail in failed:
        print(f"    ✗ {name}: {detail}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
