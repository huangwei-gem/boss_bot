"""账号区 UI 的真机点击测试（需求 5：左侧只管新增/登录，右侧只管切数据）。

pytest 那侧锁的是源码形状；这里要看到的是"手真的点下去了，界面真的换了"。
尤其"右侧数据范围点了没反应"这一条，只有真浏览器能证。

绝不碰用户那份 bot_config.json，也不干扰正在跑的 5000 面板：
临时目录放一份副本，起第二个 Flask（端口 5056，路径全指向副本），
浏览器用项目内置 cloakbrowser 的独立实例。全程只点本地面板，不碰 BOSS，
不点任何发送/打招呼/发简历按钮。

Cookie 红线：整轮跑前跑后逐个算 sha，一个字节都不能变。

运行：python tests/e2e_account_scope_ui.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import e2e_greet_records_ui as harness  # noqa: E402
from e2e_greet_records_ui import (  # noqa: E402
    check, click_chip, click_until_alive, close_browser, open_browser, scope_chips)

results = harness.results

TEST_PORT = int(os.environ.get("BOSS_SCOPE_UI_PORT", "5056"))
URL = f"http://127.0.0.1:{TEST_PORT}"
MARK = "UI实测招呼语-勿与全局模板混淆"

LAUNCHER = """
import sys, pathlib
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(flask)r)
import boss_bot.unified_config as UC
tmp = pathlib.Path(%(tmp)r)
UC.BOT_CONFIG_FILE = tmp / "bot_config.json"
UC.OVERRIDES_FILE = tmp / "config_overrides.json"
UC.USER_PROFILE_FILE = tmp / "user_profile.json"
import app as A
A._ensure_config()
kwargs = {"host": "127.0.0.1", "port": %(port)d, "debug": False, "use_reloader": False}
if (getattr(A, "_socketio_kwargs", {}) or {}).get("async_mode") == "threading":
    kwargs["allow_unsafe_werkzeug"] = True
A.socketio.run(A.app, **kwargs)
"""

COOKIE_FILES = ("zhipin_cookies.json", "zhipin_cookies_1.json")


def sha_of_cookies():
    out = {}
    for name in COOKIE_FILES:
        p = PROJECT_ROOT / name
        out[name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else None
    return out


def seed(tmp_dir):
    """副本里把两个号的招呼语都清空，才测得出"是不是系统代填的"。"""
    data = json.loads((PROJECT_ROOT / "bot_config.json").read_text(encoding="utf-8"))
    accs = data.get("accounts") or []
    if len(accs) < 2:
        raise AssertionError(f"这份配置只有 {len(accs)} 个账号，测不了左右分工")
    for a in accs:
        a["greeting_message"] = ""
    (tmp_dir / "bot_config.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def start_server(tmp_dir, port=None):
    """起一个只读临时配置副本的 Flask 实例；port 可变，好让别的实测脚本复用。"""
    global TEST_PORT, URL
    TEST_PORT = port or TEST_PORT
    URL = f"http://127.0.0.1:{TEST_PORT}"
    script = LAUNCHER % {"root": str(PROJECT_ROOT),
                         "flask": str(PROJECT_ROOT / "flask-version"),
                         "tmp": str(tmp_dir), "port": TEST_PORT}
    env = dict(os.environ, BOSS_BOT_CONFIG=str(tmp_dir / "bot_config.json"))
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    import requests
    for _ in range(40):
        if proc.poll() is not None:
            raise AssertionError("实测用的 Flask 实例自己退了，看启动命令的报错")
        try:
            if requests.get(URL + "/api/config", timeout=1).ok:
                return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise AssertionError("实测用的 Flask 实例 20 秒内没起来")


def api(path, payload=None):
    import requests
    if payload is None:
        return requests.get(URL + path, timeout=10).json()
    return requests.post(URL + path, json=payload, timeout=20).json()


def wait_for(page, js, tries=24):
    for _ in range(tries):
        try:
            if page.run_js(js):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def active_chip(page):
    return page.run_js("return (document.querySelector('.scope-chip.active')||{}).textContent||''")


def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="boss_account_scope_"))
    proc = None
    page = br = None
    before = sha_of_cookies()
    try:
        seeded = seed(tmp_dir)
        names = [a.get("name") or f"账号{i}" for i, a in enumerate(seeded["accounts"])]
        n_accounts = len(seeded["accounts"])
        proc = start_server(tmp_dir)

        print("=" * 60)
        print("  账号区 UI — 真机点击测试")
        print(f"  账号：{names}")
        print("=" * 60)

        page, br = open_browser()
        page.get(URL)
        page.wait.doc_loaded()
        check("开机零点击就有数据范围条（不用等 20 秒轮询）",
              wait_for(page, f"return document.querySelectorAll('.scope-chip').length === {n_accounts + 1}"),
              f"chip {len(scope_chips(page))} 个 / 期望 {n_accounts + 1} 个")
        check("默认高亮在「全部账号」", "全部" in active_chip(page), active_chip(page))

        # ── 0b. 数据范围条重绘不许换掉 chip 节点（换了就等于吃掉用户那一下点击）──
        page.run_js("var b=document.querySelector('#metricsScope .scope-chip');"
                    "if(b)b.__keep=1; renderMetricsScope(); renderMetricsScope(); return 1")
        check("数据范围条重绘后 chip 还是同一个节点",
              bool(page.run_js("return !!((document.querySelector('#metricsScope .scope-chip')||{})).__keep")))

        # ── 1. 点右侧 chip：立刻高亮，且记录跟着切 ──
        target = n_accounts          # 最后一个 chip = 最后一个账号
        chip_text = (page.run_js(
            "return (document.querySelectorAll('.scope-chip')[%d]||{}).textContent||''" % target)
            or "").strip()
        assert chip_text and click_chip(page, target), "点不到账号 chip"
        check("点一下 chip 高亮立即换人",
              wait_for(page, "return ((document.querySelector('.scope-chip.active')||{}).textContent||'')"
                             ".indexOf(%s) >= 0" % json.dumps(chip_text, ensure_ascii=False),
                       tries=12),
              f"点的是「{chip_text}」，现在高亮「{active_chip(page)}」")
        check("切换后作用对象提示跟着改",
              (chip_text or "").strip() in page.run_js(
                  "return String((document.getElementById('aiScopeNote')||{}).textContent||'')"),
              page.run_js("return String((document.getElementById('aiScopeNote')||{}).textContent||'')"))

        want = api(f"/api/greet_records?account={n_accounts - 1}")
        rows = int(page.run_js("return document.querySelectorAll('#greetTable tbody tr').length") or 0)
        shown = page.run_js(
            "return Array.from(document.querySelectorAll('#greetTable tbody tr'))"
            ".slice(0,3).map(function(t){var c=t.querySelectorAll('td');return (c[1]?c[1].innerText:'')+'|'"
            "+(c[2]?c[2].innerText:'')}).join(' || ')")
        api_head = " || ".join(f"{(r.get('job_name') or '')}|{(r.get('company') or '')}"
                               for r in (want.get("records") or [])[:3])
        check("打招呼记录表按新范围重拉（前几行与后端一致）",
              (not api_head) or (api_head in shown) or rows == 0,
              f"界面 {shown[:90]} / 接口 {api_head[:90]}")

        # ── 2. 右侧/底部不再有登录入口，左侧仍然能登录 ──
        check("「确认登录」按钮已从界面撤掉（函数也一起删了）",
              int(page.run_js("return document.getElementById('btnConfirmLogin')?1:0")) == 0
              and page.run_js("return typeof confirmLogin") == "undefined")
        check("左侧账号行带「登录」按钮",
              wait_for(page, "return document.querySelectorAll('.acc-login-btn').length >= 2"),
              f"{page.run_js('return document.querySelectorAll(\".acc-login-btn\").length')} 个")
        check("左侧账号行带 Cookie 状态点",
              int(page.run_js("return document.querySelectorAll('.cookie-status-dot').length") or 0) >= 2)

        # ── 3. 点状态点做登录态检测：没有运行中的浏览器时给"未联网核对"，不许蒙结论 ──
        dots = lambda p: p.eles("xpath://span[contains(concat(' ', normalize-space(@class), ' '),"
                                " ' cookie-status-dot ')]")
        assert click_until_alive(page, dots, idx=1), "点不到账号2 的状态点"
        got_title = wait_for(
            page, "return Array.from(document.querySelectorAll('.cookie-status-dot'))"
                  ".some(function(e){return /有效|失效|未知|未联网核对/.test(e.title||'')})", tries=20)
        titles = page.run_js("return Array.from(document.querySelectorAll('.cookie-status-dot'))"
                             ".map(function(e){return e.title||''}).join(' ## ')")
        check("检测给出可核对的结论（不启浏览器时说明未联网核对）", got_title, titles[:150])

        # ── 4. 招呼语按号：写进这个号，不污染别的号，也不代填模板 ──
        page.run_js(f"setDataScope('{n_accounts - 1}');return 1")
        time.sleep(0.6)
        filled = page.run_js("return document.getElementById('accGreeting').value")
        check("选中新号后招呼语框是空的（不代填全局模板）",
              not (filled or "").strip(), repr((filled or "")[:60]))
        page.run_js(f"document.getElementById('accGreeting').value='{MARK}';"
                    "onAccChange();return 1")
        time.sleep(1.5)
        disk = json.loads((tmp_dir / "bot_config.json").read_text(encoding="utf-8"))
        check("招呼语落到这个账号自己的配置里",
              disk["accounts"][n_accounts - 1].get("greeting_message") == MARK,
              str(disk["accounts"][n_accounts - 1].get("greeting_message"))[:60])
        others = [a.get("greeting_message") for i, a in enumerate(disk["accounts"])
                  if i != n_accounts - 1]
        check("其它账号的招呼语没被一起改掉", all(not o for o in others), str(others)[:80])
        check("配置里没有全局招呼语可供回落",
              not api("/api/config")["config"].get("greeting_message"),
              str(api("/api/config")["config"].get("greeting_message"))[:60])

        # ── 5. 岗位弹窗不再预填模板 ──
        page.run_js("showJobModal({job_name:'实测岗位',company:'实测公司',"
                    "city:'长沙',query:'数据分析',scroll_pages:5,greeting_message:''});return 1")
        time.sleep(0.5)
        modal_g = page.run_js("return (document.getElementById('modalGreeting')||{}).value||''")
        label = page.run_js("return Array.from(document.querySelectorAll('#jobModal label'))"
                            ".map(function(e){return e.textContent}).join('|')")
        check("新建岗位弹窗的招呼语框不预填话术", not modal_g.strip(), repr(modal_g[:60]))
        check("岗位招呼语写明留空走账号级", "账号级" in label, label[:120])
        page.run_js("closeJobModal && closeJobModal();return 1")

        # ── 6. 新增账号：列表 +1，红点，数据范围自动跟到新号 ──
        add = lambda p: p.eles("xpath://button[contains(.,'添加')]")
        assert click_until_alive(page, add), "点不到「添加」按钮"
        check("新增账号后列表多一行",
              wait_for(page, f"return document.querySelectorAll('.account-tab').length === {n_accounts + 1}",
                       tries=20),
              f"{page.run_js('return document.querySelectorAll(\".account-tab\").length')} 行")
        check("新号没有招呼语（等着用户自己写）",
              MARK not in str(json.loads((tmp_dir / "bot_config.json").read_text(encoding="utf-8"))
                              ["accounts"][-1].get("greeting_message") or ""))
        check("新增后作用对象切到新号",
              wait_for(page, "return String((document.getElementById('aiScopeNote')||{}).textContent||'')"
                             ".indexOf('账号') >= 0", tries=12),
              page.run_js("return String((document.getElementById('aiScopeNote')||{}).textContent||'')"))

        # ── 7. 点左侧账号行本身就切数据范围（用户原话："点击账号2他切换不了"）──
        tabs = lambda p: p.eles(
            "xpath://div[contains(concat(' ', normalize-space(@class), ' '), ' account-tab ')]")
        assert click_until_alive(page, tabs, idx=0), "点不到左侧第一个账号行"

        def click_tab(idx):
            """点到 dataScope 真换人为止，返回用了几次。
            一次点不上就是重绘把行换掉了——用户视角的「点了没反应」。"""
            for attempt in range(1, 5):
                click_until_alive(page, tabs, idx=idx)
                if wait_for(page, f"return String(dataScope) === '{idx}'", tries=4):
                    return attempt
            return 0

        used = click_tab(0)
        check("点账号行一次就切数据范围（右侧记录跟着换）", used == 1,
              f"dataScope={page.run_js('return String(dataScope)')}，点上用了 {used or '没点上'} 次")
        check("点中的账号行有高亮",
              bool(page.run_js("return !!document.querySelector('.account-tab.active')")))

        # ── 7b. metrics 轮询每几秒重绘账号条：重绘不能把行换成新节点 ──
        page.run_js("document.querySelector('.account-tab').__keep = 1; return 1")
        page.run_js("renderAccounts(); renderAccounts(); return 1")
        check("轮询重绘后账号行还是同一个节点",
              bool(page.run_js("return !!document.querySelector('.account-tab').__keep")),
              "整块 innerHTML 重建会让正在被点的那一行中途消失")
        check("重绘之后马上点仍然一次生效", click_tab(1) == 1,
              f"dataScope={page.run_js('return String(dataScope)')}")

        # ── 8. 招呼语没配这件事必须顶上前说清楚，不能只埋在日志里 ──
        alert_js = ("var b=document.getElementById('greetAlert');"
                    "return (b?getComputedStyle(b).display:'none') + '@@' + (b?b.innerText:'').trim()")

        def alert_state():
            raw = str(page.run_js(alert_js) or "@@")
            disp, _, txt = raw.partition("@@")
            return disp, txt

        page.run_js(f"setDataScope({n_accounts - 1});return 1")   # 这个号在步骤 4 已填过招呼语
        time.sleep(1.5)
        disp, txt = alert_state()
        check("填过招呼语的号：不出现整号级提示", disp == "none", f"display={disp} {txt[:60]}")
        page.run_js("setDataScope('0');return 1")                  # 主账号没填
        time.sleep(1.5)
        disp, txt = alert_state()
        check("没填招呼语的号：打招呼区顶上前点名提示",
              disp != "none" and ("没填" in txt or "未填" in txt), f"display={disp} {txt[:90]}")
        go = lambda p: p.eles("xpath://div[@id='greetAlert']//button[contains(.,'去填写')]")
        check("提示里有「去填写」入口", len(go(page)) == 1, f"{len(go(page))} 个按钮")
        page.run_js("var b=document.querySelector('#greetAlert button');"
                    "if(b)b.__keep=1; renderGreetAlert(); renderGreetAlert(); return 1")
        check("提示重绘后「去填写」还是同一个按钮",
              bool(page.run_js("return !!((document.querySelector('#greetAlert button')||{})).__keep")))
        # 数一下处理函数真被调到几次：焦点没落下来时能分清是"没点到"还是"点了没聚焦"
        page.run_js("var o=gotoGreetEditor; window.__goHits=0;"
                    "gotoGreetEditor=function(i){window.__goHits++; return o(i);}; return 1")
        evidence = page.run_js(
            "return 'greetTab=' + !!document.querySelector('#greetTabContent.active')"
            " + ' alertDisp=' + getComputedStyle(document.getElementById('greetAlert')).display")
        if go(page):
            # 滚进视野再点：提示条在记录表上方，窗口矮时按钮在屏幕外，
            # 裸 click 会点到空处（以前这里拿旧句柄直接点，成败看窗口高度）
            click_until_alive(page, go)
            # 当前范围是"只看账号 1"，所以提示点到的就是它；切过去后要焦点落在招呼语框
            check("点「去填写」把编辑目标切到这个号并聚焦招呼语框",
                  wait_for(page, "return 0 === Number(activeAccountIdx) && "
                                 "document.activeElement === document.getElementById('accGreeting')",
                           tries=10),
                  page.run_js("return 'activeAccountIdx=' + activeAccountIdx + ' focus=' + "
                              "(document.activeElement||{}).id + ' hits=' + window.__goHits + ' '")
                  + evidence)

    finally:
        if page is not None:
            try:
                page.run_js("return 1")
            except Exception:
                pass
        close_browser(br)
        if proc is not None:
            proc.kill()
        after = sha_of_cookies()
        print("=" * 60)
        print(f"  Cookie 文件跑前 sha：{before}")
        print(f"  Cookie 文件跑后 sha：{after}")
        check("Cookie 一个字节都没动", before == after,
              "" if before == after else f"{before} -> {after}")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        passed = sum(1 for _, ok, _ in results if ok)
        print("=" * 60)
        print(f"  共 {len(results)} 项，失败 {len(results) - passed} 项")
        for name, ok, detail in results:
            if not ok:
                print(f"  ✗ {name} — {detail}")
        return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
