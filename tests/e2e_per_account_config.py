"""每账号一套配置的真机点击测试（task #34 收尾）。

pytest 那侧证明的是模型和接口；这里要看到的是"用户在界面上改的那一下，
最后落到了哪个账号的覆盖里"——只有真浏览器真输入框能证。

绝不碰用户那份 bot_config.json，也不干扰正在跑的 5000 面板：
临时目录放一份副本，起第二个 Flask（端口 5055，路径全部指向副本），
浏览器用项目内置 cloakbrowser 的独立实例。全程只点本地面板，不碰 BOSS。

运行：python tests/e2e_per_account_config.py
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

from e2e_greet_records_ui import (  # noqa: E402
    check, close_browser, open_browser, scope_chips)

results = __import__("e2e_greet_records_ui").results

TEST_PORT = int(os.environ.get("BOSS_UI_TEST_PORT", "5055"))
URL = f"http://127.0.0.1:{TEST_PORT}"
BASE_THRESHOLD = 70
ACCOUNT2_THRESHOLD = 88
GLOBAL_PROMPT = "全局基准提示词：只看数据分析相关"

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


def seed_config(tmp_dir):
    """复制真实配置到临时目录，并给它一份"账号2 独立阈值"的起始覆盖。"""
    src = Path(PROJECT_ROOT / "bot_config.json")
    data = json.loads(src.read_text(encoding="utf-8"))
    accs = data.get("accounts") or []
    if len(accs) < 2:
        raise AssertionError(f"这份配置只有 {len(accs)} 个账号，测不了按账号独立")
    data.setdefault("ai", {})
    data["ai"]["match_threshold"] = BASE_THRESHOLD
    data["ai"]["custom_scoring_prompt"] = GLOBAL_PROMPT
    accs[0]["settings"] = {}
    accs[1]["settings"] = {"ai": {"match_threshold": ACCOUNT2_THRESHOLD}}
    (tmp_dir / "bot_config.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def start_server(tmp_dir):
    root = str(PROJECT_ROOT)
    script = LAUNCHER % {"root": root, "flask": str(PROJECT_ROOT / "flask-version"),
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


def threshold(page):
    return page.run_js("return String(document.getElementById('aiThreshold').value)")


def prompt_text(page):
    return page.run_js("return document.getElementById('customScoringPrompt').value")


def scope_note(page):
    return page.run_js("return document.getElementById('aiScopeNote').textContent")


def chip_text(chip):
    try:
        return chip.text or ""
    except Exception:
        return ""


def click_chip_by_text(page, wanted):
    """按文字点数据范围 chip：位置会变，文字不会。重渲染会吞掉句柄，重试几次。"""
    from DrissionPage.errors import ElementLostError, NoRectError
    for _ in range(6):
        for chip in scope_chips(page):
            if wanted in chip_text(chip):
                try:
                    chip.scroll.to_see()
                    time.sleep(0.1)
                    chip.click()
                    return True
                except (NoRectError, ElementLostError):
                    break
        time.sleep(0.4)
    return False


def wait_for(js_predicate, page, tries=24):
    for _ in range(tries):
        try:
            if page.run_js(js_predicate):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def disk(tmp_dir):
    return json.loads((tmp_dir / "bot_config.json").read_text(encoding="utf-8"))


def main():
    tmp_dir = Path(tempfile.mkdtemp(prefix="boss_per_account_cfg_"))
    proc = None
    page = br = None
    try:
        seeded = seed_config(tmp_dir)
        acc2_name = seeded["accounts"][1].get("name") or "账号2"
        proc = start_server(tmp_dir)

        print("=" * 60)
        print("  每账号一套配置 — 真机点击测试")
        print("=" * 60)

        page, br = open_browser()
        page.get(URL)
        page.wait.doc_loaded()
        # 面板要等 /api/config 回来才渲染输入框
        assert wait_for("return typeof toGreetRow === 'function'", page), \
            "配置没加载完"
        wait_for("return String(document.getElementById('aiThreshold').value).length > 0", page)

        check("全部账号时写明改的是全局基准", "全局" in scope_note(page), scope_note(page))
        check("全部账号时阈值是基准值", threshold(page) == str(BASE_THRESHOLD),
              f"输入框 {threshold(page)}")

        assert click_chip_by_text(page, acc2_name), "点不到账号2 的数据范围 chip"
        # 切范围会重拉配置：等输入框真的变成 88
        changed = wait_for(
            "return String(document.getElementById('aiThreshold').value) === '%d'"
            % ACCOUNT2_THRESHOLD, page)
        check("切到该账号后阈值输入框变成它的生效值", changed,
              f"输入框 {threshold(page)} / 期望 {ACCOUNT2_THRESHOLD}")
        check("作用对象提示跟着换成该账号", acc2_name in scope_note(page), scope_note(page))
        check("没覆盖的字段仍显示全局值", prompt_text(page) == GLOBAL_PROMPT,
              f"提示词 {prompt_text(page)[:40]}")

        # 在账号2 名下把阈值改成 95：真输入 + 点别处失焦触发 onchange 自动保存
        box = page.ele("xpath://input[@id='aiThreshold']")
        box.scroll.to_see()
        box.click()
        box.clear()
        box.input("95")
        page.ele("xpath://span[@id='aiScopeNote']").click()

        # 保存链路是 onchange → PUT /api/config → 写盘，异步：轮磁盘文件而不是干等
        on_disk = disk(tmp_dir)
        for _ in range(20):
            on_disk = disk(tmp_dir)
            overlay = (on_disk["accounts"][1].get("settings") or {}).get("ai") or {}
            if overlay.get("match_threshold") == 95:
                break
            time.sleep(0.5)
        acc2_settings = (on_disk["accounts"][1].get("settings") or {})
        check("改动落到该账号的覆盖里",
              (acc2_settings.get("ai") or {}).get("match_threshold") == 95,
              f"accounts[1].settings={acc2_settings}")
        check("全局基准没被生效值写脏",
              on_disk["ai"]["match_threshold"] == BASE_THRESHOLD,
              f"全局 ai.match_threshold={on_disk['ai']['match_threshold']}")
        check("另一个账号没被写脏",
              (on_disk["accounts"][0].get("settings") or {}) == {},
              f"accounts[0].settings={on_disk['accounts'][0].get('settings')}")

        assert click_chip_by_text(page, "全部"), "点不到『全部账号』chip"
        back = wait_for(
            "return String(document.getElementById('aiThreshold').value) === '%d'"
            % BASE_THRESHOLD, page)
        check("切回全部账号后输入框回到基准", back, f"输入框 {threshold(page)}")

        page.get_screenshot(str(PROJECT_ROOT / "tests/screenshots/per_account_config.png"))
        print("  截图: tests/screenshots/per_account_config.png", flush=True)
    finally:
        if page is not None:
            close_browser(br)
        if proc is not None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 60)
    print(f"  共 {len(results)} 项，失败 {len(failed)} 项")
    for name, _, detail in failed:
        print(f"    ✗ {name}: {detail}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
