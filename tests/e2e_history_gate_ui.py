# -*- coding: utf-8 -*-
"""「历史数据体积闸」两个输入框 + 那一行实测的真机点击测试（task #221 真机那一轨）。

pytest 那侧（tests/test_space_retention.py）证明的是判据本身：没到线不删、到线只删
按天的、Cookie 豁免、删前落台账。这里要看的是他在界面上敲那一下走完没走完
回填→收集→落盘→后端读到 这条路——少一处就是"改了不生效"（他划过红线），
而那一行"现在多少 MB"要是拿的是假数，就等于在他眼前骗他。

绝不碰用户那份 bot_config.json，也不干扰 5000 面板：临时目录放一份副本，
起第二个 Flask（端口默认 5059，路径全指向副本），浏览器用项目内置 cloakbrowser
的独立实例、独立调试端口。全程只点本地面板，不碰 BOSS。

跑完还回读一次真仓库 data/+logs/ 的条目清单：闸门的干跑在真实数据上必须"一个字节都没少"。

运行：
    BOSS_PROBE_HEADLESS=1 python tests/e2e_history_gate_ui.py
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
from tests.conftest import mk测试临时目录, 挑空闲端口  # noqa: E402
from boss_bot.platform_compat import 终止进程  # noqa: E402
from boss_bot.space_retention import 历史体积  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import e2e_greet_records_ui as ui  # noqa: E402
from e2e_greet_records_ui import check, close_browser, open_browser  # noqa: E402

results = ui.results
TEST_PORT = 挑空闲端口(int(os.environ.get("BOSS_HISTORY_GATE_UI_PORT", "5059")), "历史体积闸实测")
PROBE_PORT = int(os.environ.get("BOSS_HISTORY_GATE_PROBE_PORT", "9346"))
URL = "http://127.0.0.1:%d" % TEST_PORT

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


def 盘上的(临时目录):
    import requests
    回 = requests.get(URL + "/api/config", timeout=5).json()["config"].get("log", {})
    return 回.get("history_cleanup_mb", "<缺失>"), 回.get("history_keep_days", "<缺失>")


def 框里(页面, id):
    return str(页面.run_js("return String(document.getElementById('%s').value)" % id))


def 键入(页面, id, 值):
    """真键入：直接改 .value 不触发 onchange，等于没测到收集那一段。"""
    el = 页面.ele("@id=%s" % id, timeout=8)
    if el is None:
        raise AssertionError("面板上没有 #%s 这个输入框" % id)
    el.scroll.to_see()
    time.sleep(0.2)
    页面.run_js("document.getElementById('%s').value = ''" % id)
    el.input(str(值))
    页面.run_js("document.getElementById('%s').blur()" % id)
    time.sleep(0.3)


def 条目清单(根):
    """data/+logs/ 里每个文件的路径与字节——前后各数一遍，比对有没有少。"""
    记 = {}
    for 名 in ("data", "logs"):
        for 文 in (根 / 名).rglob("*"):
            if 文.is_file():
                记[文.as_posix()] = 文.stat().st_size
    return 记


def main():
    tmp = mk测试临时目录("boss_history_gate_ui")
    src = json.loads((PROJECT_ROOT / "bot_config.json").read_text(encoding="utf-8"))
    src.setdefault("log", {})["history_cleanup_mb"] = 500
    src["log"]["history_keep_days"] = 3
    (tmp / "bot_config.json").write_text(
        json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")

    前 = 条目清单(PROJECT_ROOT)
    server = None
    browser = proc = None
    try:
        server = start_server(tmp)
        ui.DEBUG_PORT = PROBE_PORT
        browser, proc = open_browser()
        page = browser
        page.get(URL)
        time.sleep(2.5)

        check("两个输入框都在页面上",
              page.ele("@id=advHistoryCleanupMb", timeout=5) is not None
              and page.ele("@id=advHistoryKeepDays", timeout=5) is not None)
        check("打开面板回填的是盘上的真值 500/3",
              (框里(page, "advHistoryCleanupMb"), 框里(page, "advHistoryKeepDays")) == ("500", "3"),
              "实际显示 %s / %s" % (框里(page, "advHistoryCleanupMb"),
                                     框里(page, "advHistoryKeepDays")))

        import requests
        回 = requests.get(URL + "/api/retention", timeout=8).json()
        数 = 回.get("data") or {}
        现算 = 历史体积(PROJECT_ROOT)
        check("接口回的是实测数（和现场再数一遍对得上）",
              回.get("ok") and 数.get("字节") == 现算["字节"],
              "接口 %r / 现算 %d" % (数.get("字节"), 现算["字节"]))
        check("阈值与窗口也带出来了", 数.get("阈值MB") == 500 and 数.get("保留天数") == 3,
              json.dumps({k: 数.get(k) for k in ("阈值MB", "保留天数", "到线")},
                         ensure_ascii=False))
        check("没到线时那一行照实说没到线",
              (数.get("到线") is False and "没到" in (数.get("说法") or ""))
              or 数.get("到线") is True,
              "说法=%r 到线=%r" % (数.get("说法"), 数.get("到线")))
        文案 = str(page.run_js("return String(document.getElementById('advHistoryNote')"
                              ".textContent)"))
        check("界面那一行念出了实测体积和阈值", "MB" in 文案 and "500" in 文案,
              文案[:120])

        键入(page, "advHistoryCleanupMb", 480)
        键入(page, "advHistoryKeepDays", 2)
        time.sleep(1.8)
        check("改成 480/2 后端收到 480/2", 盘上的(tmp) == (480, 2),
              "接口给的是 %r" % (盘上的(tmp),))
        page.get(URL)
        time.sleep(2.5)
        check("刷新后输入框还是 480/2（落盘了，不只是内存里改改）",
              (框里(page, "advHistoryCleanupMb"), 框里(page, "advHistoryKeepDays")) == ("480", "2"),
              "实际显示 %s / %s" % (框里(page, "advHistoryCleanupMb"),
                                     框里(page, "advHistoryKeepDays")))
        落盘 = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8")).get("log", {})
        check("落盘文件里也是 480/2",
              落盘.get("history_cleanup_mb") == 480 and 落盘.get("history_keep_days") == 2,
              json.dumps(落盘, ensure_ascii=False)[:160])

        # 0 是"不看体积、每轮都清"这个合法值：用 || 兜底就永远存不进 0
        键入(page, "advHistoryCleanupMb", 0)
        time.sleep(1.8)
        check("阈值填 0 后端收到 0，没被兜回 500", 盘上的(tmp)[0] == 0,
              "接口给的是 %r" % (盘上的(tmp),))

        回2 = requests.get(URL + "/api/retention", timeout=8).json().get("data") or {}
        check("填 0 后接口照实报每轮都清",
              回2.get("到线") is True and "0" in (回2.get("说法") or ""),
              json.dumps({k: 回2.get(k) for k in ("阈值MB", "到线", "说法")},
                         ensure_ascii=False)[:200])
    finally:
        if browser is not None:
            close_browser(proc)
        if server is not None:
            终止进程(server.pid)
        shutil.rmtree(tmp, ignore_errors=True)

    后 = 条目清单(PROJECT_ROOT)
    少了 = sorted(set(前) - set(后))
    变了 = [k for k, v in 前.items() if k in 后 and 后[k] != v]
    check("这一趟在真仓库上跑完，data/+logs/ 一个文件都没少",
          not 少了, "少了 %d 个：%s" % (len(少了), 少了[:6]))
    check("已有文件没有被截断或清空（字节数只有变大没有变小）",
          all(后[k] >= 前[k] for k in 变了),
          "变小的：%s" % [k for k in 变了 if 后[k] < 前[k]][:6])

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + ("全部通过" if not failed
                  else "失败 %d 项: " % len(failed) + ", ".join(failed)), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
