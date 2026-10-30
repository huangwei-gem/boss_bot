"""打招呼记录实时性的真机点击测试（task #33/#35）。

自己起一个临时面板（配置与记录都指向临时副本），不碰 BOSS、不点任何发送类按钮。
用项目内置 cloakbrowser 起独立实例（独立端口 + 临时用户目录），
不干扰正在跑的两个投递浏览器。

不依赖线上那份记录：2026-10-23 用户清掉 172 条旧记录后线上就是 0 条，
"历史记录渲染出来了"曾经因此变成假红。这里种两条真格式的记录再验渲染。

运行：python tests/e2e_greet_records_ui.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from tests.conftest import mk测试临时目录, 挑空闲端口  # noqa: E402
from boss_bot.platform_compat import 终止进程


from boss_bot.browser_launcher import _find_best_browser_path, _wait_for_port  # noqa: E402
from boss_bot.unified_config import 面板端口  # noqa: E402

DEBUG_PORT = int(os.environ.get("BOSS_PROBE_PORT", "9333"))
TEST_PORT = 挑空闲端口(int(os.environ.get("BOSS_GREET_UI_PORT", "5061")), "打招呼记录实测")
if TEST_PORT == 面板端口():
    raise RuntimeError("实测禁止打线上面板：临时实例不能占用 %d" % TEST_PORT)
FLASK_URL = "http://127.0.0.1:%s" % TEST_PORT
UI_TAG = "UI实测"
_probe_port = DEBUG_PORT

# 与 greet_engine._emit_greet_event + app.greet_event_callback 的推送结构一致
PUSH_OK = {
    "job_name": f"{UI_TAG}-园林植物养护", "company": "实测公司A", "salary": "6-8K",
    "status": "success", "is_skipped": False, "ai_score": 85, "ai_reason": "",
    "skip_reason": "", "greeting": "您好，我对这个岗位很感兴趣",
    "url": "https://www.zhipin.com/job_detail/ui_probe_ok.html",
    "timestamp": "2026-09-29 10:00:00", "time": "10:00:00",
    "account_index": 0, "account_name": "主账号",
}
PUSH_AI = dict(PUSH_OK, job_name=f"{UI_TAG}-AI不匹配岗", status="skip", is_skipped=True,
               skip_reason="AI判定不匹配: 只接受长期实习生",
               ai_reason="岗位只要长期实习生，与期望不符",
               url="https://www.zhipin.com/job_detail/ui_probe_ai.html")
# 2026-10-29 现场：他勾了「数据标注/AI训练师」，投递腿拿否决词把它弹回来，
# 记录里那一行被画成「AI不匹配」——那句「AI」是岗位名带进来的，模型根本没判过。
# 所以状态归类别只认后端真写下的判据原文，不许拿裸「AI」字样猜。
PUSH_VETO = dict(PUSH_OK, job_name="数据标注/AI训练师", status="skip", is_skipped=True,
                 skip_reason="JD 命中否决词「坐班」｜数据标注/AI训练师",
                 ai_reason="", url="https://www.zhipin.com/job_detail/ui_probe_veto.html")

LAUNCHER = """
import sys, pathlib
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(flask)r)
import boss_bot.unified_config as UC
tmp = pathlib.Path(%(tmp)r)
UC.BOT_CONFIG_FILE = tmp / "bot_config.json"
UC.OVERRIDES_FILE = tmp / "config_overrides.json"
UC.USER_PROFILE_FILE = tmp / "user_profile.json"
# 记录文件也指向副本：种的两条只落在临时目录，用户那份记录一个字节都不动
import boss_bot.reply_record as RR
RR.DATA_DIR = tmp / "data"
RR.REPLY_RECORDS_FILE = RR.DATA_DIR / "reply_records.json"
RR.GREET_RECORDS_FILE = RR.DATA_DIR / "greet_records.json"
RR._reply_store = None
RR._greet_store = None
import boss_bot.job_pool as JP
JP.resolve_path = lambda p: tmp / "data"
import app as A
A.DATA_DIR = tmp / "data"
A._ensure_config()
kwargs = {"host": "127.0.0.1", "port": %(port)d, "debug": False, "use_reloader": False}
if (getattr(A, "_socketio_kwargs", {}) or {}).get("async_mode") == "threading":
    kwargs["allow_unsafe_werkzeug"] = True
A.socketio.run(A.app, **kwargs)
"""

种子记录 = [
    {"timestamp": "2026-09-29 10:00:00", "job_name": "种子-园林植物养护",
     "job_url": "https://www.zhipin.com/job_detail/seed_ok.html", "company": "种子公司A",
     "salary": "6-8K", "ai_score": 85, "ai_is_match": True,
     "is_greeted": True, "status": "applied", "greeting_message": "您好，我对这个岗位很感兴趣",
     "account_index": 0, "account_name": "主账号"},
    {"timestamp": "2026-09-29 10:05:00", "job_name": "种子-AI不匹配岗",
     "job_url": "https://www.zhipin.com/job_detail/seed_ai.html", "company": "种子公司B",
     "salary": "5-7K", "ai_score": 20, "ai_is_match": False,
     "ai_reason": "岗位只要长期实习生，与期望不符",
     "is_skipped": True, "status": "skipped",
     "skip_reason": "AI判定不匹配: 只接受长期实习生",
     "account_index": 0, "account_name": "主账号"},
]

results = []


def load_config_accounts():
    """盘上真实配置的账号数——界面 chip 数量要跟着它走，不能信进程里的旧快照"""
    from boss_bot.unified_config import BOT_CONFIG_FILE
    return (json.loads(Path(BOT_CONFIG_FILE).read_text(encoding="utf-8"))
            .get("accounts") or [])


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)


def close_browser(proc):
    """Chrome 会派生子进程，只杀启动那一个会留下占着调试端口的孤儿浏览器。"""
    if proc is None:
        return
    try:
        终止进程(proc.pid)
    except Exception:
        proc.kill()


def open_browser():
    from boss_bot.browser_launcher import _is_port_open
    if _is_port_open("127.0.0.1", DEBUG_PORT):
        raise RuntimeError(
            f"端口 {DEBUG_PORT} 已被占用：可能是上次实测留下的孤儿浏览器，"
            f"也可能有别的程序在跑。绝不连到来路不明的浏览器上，"
            f"清掉占用进程或改 BOSS_PROBE_PORT 再试。")
    exe, browser_type = _find_best_browser_path("chrome")
    inside_project = Path(exe).is_file() and PROJECT_ROOT in Path(exe).resolve().parents
    if not inside_project:
        raise RuntimeError(f"实测必须用项目内置 cloakbrowser，拿到的是 {browser_type}: {exe}")
    print(f"  使用浏览器: {exe}", flush=True)
    profile = str(mk测试临时目录("boss_ui_probe"))
    args = [exe, f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            # 窗口太窄会切到移动端布局：侧栏直接隐藏，元素取不到矩形，点不了
            "--window-size=1680,1050"]
    # BOSS_PROBE_HEADLESS=1：整轮真机实测在无头下重跑一遍。既不弹窗口抢焦点，
    # 也顺带证明这些 UI 在没有窗口的前提下照样点得到。
    if os.environ.get("BOSS_PROBE_HEADLESS", "") == "1":
        args.append("--headless=new")
        print("  无头模式：BOSS_PROBE_HEADLESS=1", flush=True)
    proc = subprocess.Popen(
        args + ["about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not _wait_for_port("127.0.0.1", DEBUG_PORT, timeout=25):
        proc.kill()
        raise RuntimeError("cloakbrowser 调试端口没起来")
    from DrissionPage import ChromiumOptions, ChromiumPage
    try:
        co = ChromiumOptions()
        # 按端口连，让 DrissionPage 自己去找调试地址：
        # 抢在 /json/version 就绪之前拿 ws 会拿到空地址，attach 直接失败
        co.set_local_port(DEBUG_PORT)
        page = ChromiumPage(co)
    except Exception:
        close_browser(proc)   # 连不上就把刚拉起来的浏览器收掉，别留孤儿占端口
        raise
    return page, proc


def scope_chips(page):
    """只要数据范围那一组 chip。

    DrissionPage 的 css 选择器在这个页面上取不全元素，用 XPath 拿可点的真元素；
    而且必须按 data-scope 过滤——判分复盘那组账号切换按钮同用 .scope-chip 类名，
    取全集时 `-1` 会点到复盘那组的账号0，dataScope 当然不动（实测把这条测成"红"过）。
    """
    return page.eles("xpath://button[contains(concat(' ', normalize-space(@class), ' '),"
                     " ' scope-chip ')][@data-scope]")


def click_until_alive(page, locate, idx=0, tries=6):
    """面板上的元素会被周期性重渲染（Cookie 状态、指标轮询），句柄说没就没：
    每次重新取、滚进视野、立刻点。窗口太窄时元素拿不到矩形，同样重试。"""
    from DrissionPage.errors import ElementLostError, NoRectError
    for _ in range(tries):
        els = locate(page)
        if not els or len(els) <= abs(idx):
            time.sleep(0.4)
            continue
        try:
            target = els[idx]
            target.scroll.to_see()
            time.sleep(0.1)
            target.click()
            return True
        except (NoRectError, ElementLostError):
            time.sleep(0.4)
    return False


def click_chip(page, idx):
    """指标每 20 秒重绘一次切换条，句柄随时可能失效：取到就立刻点，失效就重来。

    先滚进视野，否则屏幕外的 chip 拿不到矩形，点击会抛 NoRectError。
    """
    from DrissionPage.errors import ElementLostError, NoRectError
    for _ in range(6):
        chips = scope_chips(page)
        if not chips or len(chips) <= abs(idx):
            time.sleep(0.4)
            continue
        target = chips[idx]
        try:
            target.scroll.to_see()
            time.sleep(0.1)
            target.click()
            return True
        except (NoRectError, ElementLostError):
            time.sleep(0.4)
    return False


def 种一份临时配置(tmp_dir, 记录=None):
    """抄三份真配置到临时目录，再种一份打招呼记录（默认用本套件的两条）。"""
    记录 = 种子记录 if 记录 is None else 记录
    for 名 in ("bot_config.json", "user_profile.json", "config_overrides.json"):
        源 = PROJECT_ROOT / 名
        if 源.is_file():
            (tmp_dir / 名).write_text(源.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_dir / "data").mkdir(parents=True, exist_ok=True)
    (tmp_dir / "data" / "greet_records.json").write_text(
        json.dumps({"records": 记录, "total": len(记录),
                    "last_saved": 记录[-1].get("timestamp") if 记录 else ""},
                   ensure_ascii=False),
        encoding="utf-8")


def 起临时面板(tmp_dir, port=None):
    port = int(TEST_PORT if port is None else port)
    url = "http://127.0.0.1:%s" % port
    script = LAUNCHER % {"root": str(PROJECT_ROOT),
                         "flask": str(PROJECT_ROOT / "flask-version"),
                         "tmp": str(tmp_dir), "port": port}
    env = dict(os.environ, BOSS_BOT_CONFIG=str(tmp_dir / "bot_config.json"),
               PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    import requests
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError("临时面板自己退了（检查 LAUNCHER 与端口 %d）" % port)
        try:
            if requests.get(url + "/api/config", timeout=1).ok:
                return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("临时面板 30 秒内没起来（端口 %d）" % port)


def main():
    tmp_dir = mk测试临时目录("boss_greet_ui")
    种一份临时配置(tmp_dir)
    panel = 起临时面板(tmp_dir)

    print("=" * 60)
    print("  打招呼记录实时性 — 真机点击测试（临时面板 %d）" % TEST_PORT)
    print("=" * 60)

    page, proc = open_browser()
    try:
        page.get(FLASK_URL)
        page.wait.doc_loaded()
        js = page.run_js

        # 端口落在 Chrome 受限口时，浏览器给的是自己的错误页而不是面板，
        # 后面每条 JS 都会炸在 ReferenceError 上，看不出为什么。这里先点名。
        if js("return typeof allGreetRecords") == "undefined":
            raise RuntimeError(
                "临时面板页面没打开（端口 %d，正文开头：%s）"
                % (TEST_PORT, (js("return (document.body.innerText||'').slice(0,80)") or "")))

        def dom_rows():
            """DrissionPage 的 "#greetTableBody tr" 取不到子行，只能自己数 DOM"""
            return int(js("return document.querySelectorAll('#greetTableBody tr').length"))

        # 366 条记录要 fetch + 整表重建，等它渲染完再判空
        for _ in range(30):
            if int(js("return allGreetRecords.length")) > 0 and dom_rows() > 0:
                break
            time.sleep(0.5)

        check("页面加载了新行模型 toGreetRow", js("return typeof toGreetRow") == "function",
              "面板还在用改前的模板，重启 Flask 后再测")
        check("旧的 pending 占位分支已不在运行时", js("return typeof greetRowFromRecord") == "undefined")

        rows0 = int(js("return allGreetRecords.length"))
        dom0 = dom_rows()
        check("历史打招呼记录渲染出来了", rows0 > 0 and dom0 > 0, f"模型 {rows0} 条 / DOM {dom0} 行")
        tab = js("return (document.getElementById('greetTabCount')||{}).textContent")
        check("标签页计数跟着数据模型", str(tab) == str(rows0), f"计数 {tab} / 模型 {rows0}")

        # ── 实时推送 ──
        # 20 秒一次的轮询会按服务端数据覆盖整张表，实测行会被冲掉；
        # 测推送/筛选这一段先把轮询停住，测完再放开
        js("""window.__pollOff = true;
              if (!window.__realLoadGreet) { window.__realLoadGreet = loadGreetRecords; }
              loadGreetRecords = function (force) {
                  if (window.__pollOff) return;
                  return window.__realLoadGreet(force);
              };""")
        push_ok_js = json.dumps(PUSH_OK, ensure_ascii=False)
        push_ai_js = json.dumps(PUSH_AI, ensure_ascii=False)
        n1 = int(js(f"addGreetRecord({push_ok_js}); return allGreetRecords.length;"))
        check("推送的行进入数据模型并置顶",
              n1 == rows0 + 1 and js("return allGreetRecords[0].job_name") == PUSH_OK["job_name"],
              f"{rows0} → {n1}")
        def row_texts():
            return js("""return Array.prototype.map.call(
                document.querySelectorAll('#greetTableBody tr'),
                function(tr){ return tr.innerText.replace(/\\s+/g, ' '); });""")

        # 用 JS 数 DOM：这个页面上 DrissionPage 的 CSS 选择器取不到节点（同一批
        # 坑里已经踩过一次），而且整表重建是异步的，推完立刻读会误判成"没渲染"。
        pushed = []
        for _ in range(10):
            time.sleep(0.3)
            pushed = [t for t in row_texts() if PUSH_OK["job_name"] in t]
            if pushed:
                break
        check("推送的行真的渲染进表格", len(pushed) == 1,
              f"匹配到 {len(pushed)} 行: {(pushed or [''])[0][:90]}")
        # 断言用户看得到的徽标文案，而不是模型内部的状态串：
        # 词表归一后模型里是 applied，界面才是"已投递"
        check("推送的行显示为已投递", bool(pushed) and "已投递" in pushed[0],
              pushed[0][:120] if pushed else "没有那一行")

        n2 = int(js(f"addGreetRecord({push_ok_js}); return allGreetRecords.length;"))
        check("重复推送不产生两行", n2 == n1, f"{n1} → {n2}")

        js(f"addGreetRecord({push_ai_js});")
        check("推送的 AI 跳过与落库判定一致（ai_skip）",
              js("return allGreetRecords[0].status") == "ai_skip")

        # ── 真机：否决词跳过那条不许被岗位名里的「AI」带成「AI不匹配」 ──
        push_veto_js = json.dumps(PUSH_VETO, ensure_ascii=False)
        js(f"addGreetRecord({push_veto_js});")
        check("否决词跳过归 skip（不是 ai_skip）",
              js("return allGreetRecords[0].status") == "skip",
              js("return allGreetRecords[0].skip_reason"))
        veto_rows = []
        for _ in range(10):
            time.sleep(0.3)
            veto_rows = [t for t in row_texts() if PUSH_VETO["job_name"] in t]
            if veto_rows:
                break
        check("那一行上屏是「跳过」不是「AI不匹配」",
              bool(veto_rows) and "跳过" in veto_rows[0] and "AI不匹配" not in veto_rows[0],
              veto_rows[0][:130] if veto_rows else "没有那一行")
        状 = page.ele("#greetFilterStatus")
        状.click()
        状.select.by_text("AI不匹配")
        名 = page.ele("#greetFilterJob")
        名.click()
        名.clear(by_js=True)
        名.input(PUSH_VETO["job_name"])
        time.sleep(0.6)
        筛后 = [t for t in row_texts() if PUSH_VETO["job_name"] in t]
        check("挑「AI不匹配」筛不出这条否决词跳过",
              len(筛后) == 0, f"屏上 {len(筛后)} 行: {(筛后 or [''])[0][:90]}")
        js("resetGreetFilter()")
        time.sleep(0.3)

        # ── 真机点筛选：这就是原来的 bug，推送行一动筛选就消失 ──
        kw = page.ele("#greetFilterJob")
        kw.click()
        kw.clear(by_js=True)
        kw.input(PUSH_OK["job_name"])
        time.sleep(0.6)
        rows = row_texts()
        check("按岗位关键词筛选后推送行仍在",
              len(rows) == 1 and PUSH_OK["job_name"] in rows[0], f"DOM {len(rows)} 行: {rows[:1]}")

        sel = page.ele("#greetFilterStatus")
        sel.click()
        sel.select.by_text("AI不匹配")
        kw.clear(by_js=True)
        kw.input(PUSH_AI["job_name"])
        time.sleep(0.6)
        rows = row_texts()
        check("关键词+状态双条件筛选仍命中 AI 推送行",
              len(rows) == 1 and PUSH_AI["job_name"] in rows[0], f"DOM {len(rows)} 行: {rows[:1]}")

        js("resetGreetFilter()")
        time.sleep(0.3)
        js("document.getElementById('greetFilterDateStart').value='2026-09-29';"
           "applyGreetFilter();")
        time.sleep(0.6)
        # 和 applyGreetFilter 用同一个谓词算应显示条数（没有日期的行不受日期条件影响）
        expect = int(js("""return allGreetRecords.filter(function(r){
              var d = (r.timestamp||'').slice(0,10);
              return !d || d >= '2026-09-29'; }).length;"""))
        dom = dom_rows()
        check("日期筛选下推送行没被滤空", dom == expect, f"DOM {dom} / 应显示 {expect}")

        # 实测行留在浏览器内存里会干扰"切范围"的判断，先清掉并放开轮询
        js("""allGreetRecords = allGreetRecords.filter(function(r){
                return (r.job_name||'').indexOf('%s') < 0; });
              applyGreetFilter();
              window.__pollOff = false;""" % UI_TAG)
        js("resetGreetFilter()")
        time.sleep(0.3)

        # ── 数据范围切换要真换数据（右侧只管数据）──
        chips = scope_chips(page)
        js_chips = int(js("return document.querySelectorAll('#metricsScope [data-scope]').length"))
        want = len(load_config_accounts()) + 1   # 全部账号 + 每个账号一个 chip
        if want < 3:
            check("点数据范围 chip 会按账号重拉记录", len(chips) == 0,
                  f"只有一个账号，界面本就不显示切换条（chip={len(chips)}）")
        elif len(chips) == js_chips == want:
            before = js("return allGreetRecords.length")
            assert click_chip(page, -1), "数据范围 chip 点不动"
            time.sleep(1.5)
            scope = js("return String(dataScope)")
            after = js("return allGreetRecords.length")
            # 切范围的唯一正确结果：表里只剩这一个账号的记录，条数不同只是旁证
            mixed = int(js("""return allGreetRecords.filter(function(r){
                    return r.account_index !== Number(dataScope); }).length;"""))
            label = js("return (document.getElementById('greetFilterCount')||{}).textContent")
            check("点数据范围 chip 会按账号重拉记录",
                  scope == str(want - 2) and mixed == 0,
                  f"dataScope={scope} 记录 {before} → {after}，混入其它账号 {mixed} 条（{label}）")
            # 切范围会重渲染整条切换条，旧句柄失效，要回"全部账号"就重新取
            assert click_chip(page, 0), "回不到全部账号"
            time.sleep(1.2)
        else:
            check("点数据范围 chip 会按账号重拉记录", False,
                  f"盘上 {want - 1} 个账号 / DOM {js_chips} 个 chip / 可点 {len(chips)} 个")

        # ── 轮询兜底：放开后重拉，界面回到服务端数据 ──
        js("window.__pollOff = false; loadGreetRecords(true);")
        time.sleep(1.5)
        b = int(js("return allGreetRecords.length"))
        dom = dom_rows()
        check("重拉后回到服务端数据且不丢行", b > 0 and dom == b, f"模型 {b} 条 / DOM {dom} 行")
        left = int(js("""return allGreetRecords.filter(function(r){
              return (r.job_name||'').indexOf('%s') >= 0; }).length;""" % UI_TAG))
        check("实测行不污染真实记录", left == 0, f"残留 {left} 条")

        page.get_screenshot(str(PROJECT_ROOT / "tests/screenshots/greet_records_ui.png"))
        print(f"  截图: tests/screenshots/greet_records_ui.png", flush=True)
    finally:
        close_browser(proc)
        终止进程(panel.pid)

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 60)
    print(f"  共 {len(results)} 项，失败 {len(failed)} 项")
    for name, _, detail in failed:
        print(f"    ✗ {name}: {detail}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
