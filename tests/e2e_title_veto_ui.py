# -*- coding: utf-8 -*-
"""「岗位类型词（只看标题）」这个输入框的真机点击测试（双轨里的真机那一轨）。

pytest 那侧（tests/test_blue_collar_gate.py）证明的是判据、配置往返、投递/回复/卡片
三端都按这张表拦；这里要看到的是用户在界面上敲的那一下走完 回填→收集→落盘 这条路。
少一处就是"改了不生效"，而这个框还有第二种坑：清空之后前端拿默认表兜底，
用户说"这一类我不拦了"结果还在拦——所以空值必须真落盘成空。

绝不碰用户那份 bot_config.json，也不干扰正在跑的 5000 面板：
临时目录放一份副本，起第二个 Flask（端口默认 5057，路径全指向副本），
浏览器用项目内置 cloakbrowser 的独立实例，独立调试端口。全程只点本地面板，不碰 BOSS。

运行：
    python tests/e2e_title_veto_ui.py
    BOSS_PROBE_HEADLESS=1 python tests/e2e_title_veto_ui.py   # 无头重跑一遍
"""

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

import e2e_greet_records_ui as ui  # noqa: E402
from e2e_greet_records_ui import check, close_browser, open_browser  # noqa: E402

results = ui.results
TEST_PORT = int(os.environ.get("BOSS_TITLE_VETO_UI_PORT", "5057"))
PROBE_PORT = int(os.environ.get("BOSS_TITLE_VETO_PROBE_PORT", "9346"))
URL = f"http://127.0.0.1:{TEST_PORT}"
BOX = "aiTitleVetoKeywords"

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


def start_server(tmp_dir):
    script = LAUNCHER % {"root": str(PROJECT_ROOT),
                         "flask": str(PROJECT_ROOT / "flask-version"),
                         "tmp": str(tmp_dir), "port": TEST_PORT}
    env = dict(os.environ, BOSS_BOT_CONFIG=str(tmp_dir / "bot_config.json"),
               PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    import requests
    for _ in range(60):
        if proc.poll() is not None:
            raise AssertionError("实测用的 Flask 实例自己退了，看启动命令的报错")
        try:
            if requests.get(URL + "/api/config", timeout=1).ok:
                return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise AssertionError("实测用的 Flask 实例 30 秒内没起来")


def api_words():
    import requests
    return (requests.get(URL + "/api/config", timeout=5).json()["config"]["ai"]
            .get("title_veto_keywords", "<缺失>"))


def box_value(page):
    return page.run_js(f"return String(document.getElementById('{BOX}').value)")


def type_into(page, value):
    """真键入：直接改 .value 不触发 onchange，等于没测到收集那一段。"""
    el = page.ele(f"@id={BOX}", timeout=8)
    if el is None:
        raise AssertionError(f"面板上没有 #{BOX} 这个输入框")
    el.scroll.to_see()
    time.sleep(0.2)
    page.run_js(f"document.getElementById('{BOX}').value = ''")
    if value:
        el.input(str(value))
    page.run_js(f"document.getElementById('{BOX}').blur()")
    time.sleep(0.3)


def fire_change(page):
    """JS 改 value 之后浏览器不会自己发 change（只有真键入才发），
    上面几步已经证过键入这条路走得通，这里只验"空值"这一端：
    收集→落盘→读回 会不会把空表当成"没填"，偷偷拿默认值填回来。"""
    page.run_js(f"document.getElementById('{BOX}')"
                ".dispatchEvent(new Event('change'))")
    time.sleep(0.3)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="boss_title_veto_ui_"))
    src = json.loads((PROJECT_ROOT / "bot_config.json").read_text(encoding="utf-8"))
    src.setdefault("ai", {})["title_veto_keywords"] = ["普工", "主播"]
    (tmp / "bot_config.json").write_text(
        json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    server = None
    browser = proc = None
    try:
        server = start_server(tmp)
        ui.DEBUG_PORT = PROBE_PORT
        browser, proc = open_browser()
        page = browser
        page.get(URL)
        time.sleep(2.5)

        check("输入框在页面上", page.ele(f"@id={BOX}", timeout=5) is not None)
        check("打开面板回填的是盘上的真值（普工、主播）",
              box_value(page) == "普工、主播", f"实际显示 {box_value(page)!r}")

        # 中文逗号、顿号混着输：以前只按英文逗号切，一整条落成一个词，一个都不生效
        type_into(page, "普工，保洁、快递员")
        time.sleep(1.5)
        got = api_words()
        check("改成 普工，保洁、快递员 之后后端收到三条",
              got == ["普工", "保洁", "快递员"], f"接口给的是 {got}")
        page.get(URL)
        time.sleep(2.5)
        check("刷新后输入框还是这三条（落盘了，不只是内存里改改）",
              box_value(page) == "普工、保洁、快递员", f"实际显示 {box_value(page)!r}")
        check("落盘文件里也是这三条",
              json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
              ["ai"]["title_veto_keywords"] == ["普工", "保洁", "快递员"])

        # 清空必须是真清空：前端拿默认表兜底的话，用户说"这一类别拦了"还在拦
        page.run_js(f"document.getElementById('{BOX}').value = ''")
        fire_change(page)
        time.sleep(1.5)
        got = api_words()
        check("清空之后后端收到空表，没有拿默认值填回来",
              got == [], f"接口给的是 {got}")
        page.get(URL)
        time.sleep(2.5)
        check("刷新后输入框是空的（placeholder 不算值）",
              box_value(page) == "", f"实际显示 {box_value(page)!r}")
    finally:
        if browser is not None:
            close_browser(proc)
        if server is not None:
            subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                           capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + ("全部绿" if not failed else f"失败 {len(failed)} 项: " + ", ".join(failed)),
          flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
