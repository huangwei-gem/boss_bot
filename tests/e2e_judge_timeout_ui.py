# -*- coding: utf-8 -*-
"""「判分单接口超时」这个输入框的真机点击测试（task #121 双轨里的真机那一轨）。

pytest 那侧证明的是配置模型会读会写、容灾链真按这个数收时间；这里要看到的是
用户在界面上敲的那一下有没有走完 回填→收集→落盘 这条路——少一处就是"改了不生效"
或者"保存一次把盘上的值抹掉"，这两条都是他明确划的红线。

绝不碰用户那份 bot_config.json，也不干扰正在跑的 5000 面板：
临时目录放一份副本，起第二个 Flask（端口默认 5058，路径全指向副本），
浏览器用项目内置 cloakbrowser 的独立实例，独立调试端口。全程只点本地面板，不碰 BOSS。

运行：
    python tests/e2e_judge_timeout_ui.py
    BOSS_PROBE_HEADLESS=1 python tests/e2e_judge_timeout_ui.py   # 无头重跑一遍
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
TEST_PORT = int(os.environ.get("BOSS_JUDGE_TO_UI_PORT", "5058"))
PROBE_PORT = int(os.environ.get("BOSS_JUDGE_TO_PROBE_PORT", "9345"))
URL = f"http://127.0.0.1:{TEST_PORT}"

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


def api_judge_timeout():
    import requests
    r = requests.get(URL + "/api/config", timeout=5)
    return r.json()["config"]["ai"].get("judge_timeout", "<缺失>")


def input_judge_timeout(page):
    return page.run_js(
        "return String(document.getElementById('aiJudgeTimeout').value)")


def type_into(page, value):
    """真键入：直接改 .value 不会触发收集，等于没测到 onchange 那一段。
    清空得用 JS——el.clear() 在这类 number 输入框上会留着原值，键入变成往后拼
    （实测得到过 129）。"""
    el = page.ele("@id=aiJudgeTimeout", timeout=8)
    if el is None:
        raise AssertionError("面板上没有 #aiJudgeTimeout 这个输入框")
    el.scroll.to_see()
    time.sleep(0.2)
    page.run_js("document.getElementById('aiJudgeTimeout').value = ''")
    el.input(str(value))
    page.run_js("document.getElementById('aiJudgeTimeout').blur()")
    time.sleep(0.3)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="boss_judge_to_ui_"))
    src = json.loads((PROJECT_ROOT / "bot_config.json").read_text(encoding="utf-8"))
    src.setdefault("ai", {})["judge_timeout"] = 12
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

        check("输入框在页面上", page.ele("@id=aiJudgeTimeout", timeout=5) is not None)
        check("打开面板回填的是盘上的真值 12",
              input_judge_timeout(page) == "12", f"实际显示 {input_judge_timeout(page)}")

        type_into(page, 9)
        time.sleep(1.5)
        check("改成 9 保存后后端收到 9", api_judge_timeout() == 9,
              f"接口给的是 {api_judge_timeout()}")
        page.get(URL)
        time.sleep(2.5)
        check("刷新后输入框还是 9（落盘了，不只是内存里改改）",
              input_judge_timeout(page) == "9", f"实际显示 {input_judge_timeout(page)}")

        # 0 是「不另设上限」这个合法取值。用 || 兜底就会把 0 存成 12，
        # 用户选了 0 结果还是被收紧——这条必须单独按一遍。
        type_into(page, 0)
        time.sleep(1.5)
        check("改成 0 后端收到 0，没被兜底成 12", api_judge_timeout() == 0,
              f"接口给的是 {api_judge_timeout()}")
        page.get(URL)
        time.sleep(2.5)
        check("刷新后输入框显示 0（不是 placeholder 的 12）",
              input_judge_timeout(page) == "0", f"实际显示 {input_judge_timeout(page)}")

        check("落盘文件里也是 0",
              json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
              ["ai"]["judge_timeout"] == 0)
    finally:
        if browser is not None:
            close_browser(proc)
        if server is not None:
            subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                           capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + ("全部通过" if not failed
                  else f"失败 {len(failed)} 项: " + ", ".join(failed)), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
