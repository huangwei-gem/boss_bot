# -*- coding: utf-8 -*-
"""登录时临时换成有头，登完必须换回配置里的无头。

2026-10-09 账号2 为了扫码登录被重开成有头，登录完成后就一直有头跑着：配置写着
无头、桌面上挂着一个窗口，用户改的东西没生效。
这一手不省内存——同一台机器、同一份参数配对实测（tools/measure_browser_memory.py
--ab-headed，两轮）：无头 2479 MB、有头 2484 MB。真正吃内存的是 BOSS 那两个页面
的渲染进程（约 1.3 GB/号），跟开不开窗口无关。
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import browser_launcher as bl  # noqa: E402


def _mgr(monkeypatch, 现在无头, 配置无头, running=True, port=9223):
    class FakePage:
        def __init__(self):
            self.process_id = 4321

        def get(self, url):
            pass

    launched = []
    closed = []

    def fake_launch(**kw):
        launched.append(kw)
        return bl.BrowserInstance(chrome_page=FakePage())

    monkeypatch.setattr(bl, "launch_browser", fake_launch)
    monkeypatch.setattr(bl, "browser_mode",
                        lambda p: {"running": running,
                                   "headless": 现在无头 if running else None})
    monkeypatch.setattr(bl.time, "sleep", lambda s: None)
    cfg = type("C", (), {"headless": 配置无头, "background": True,
                         "cookie_file": ""})()
    m = bl.BrowserManager(cfg, account_index=1, port=port)
    m._instance = bl.BrowserInstance(chrome_page=FakePage())
    real_close = m.close
    m.close = lambda: (closed.append(1), real_close())[1]
    m._launched = launched
    m._closed = closed
    return m


class TestRealignToConfig:

    def test_有头换回无头(self, monkeypatch):
        m = _mgr(monkeypatch, 现在无头=False, 配置无头=True)
        assert m.realign_to_config() is True
        assert m._closed, "没关旧浏览器就重开，端口会撞"
        assert m._launched[-1]["headless"] is True

    def test_形态本来就一致就不动手(self, monkeypatch):
        """正在投递时重开一次浏览器代价很大，没差就别折腾"""
        m = _mgr(monkeypatch, 现在无头=True, 配置无头=True)
        assert m.realign_to_config() is False
        assert not m._launched

    def test_配置要有头而当前也有头(self, monkeypatch):
        m = _mgr(monkeypatch, 现在无头=False, 配置无头=False)
        assert m.realign_to_config() is False

    def test_浏览器没在跑就不动(self, monkeypatch):
        m = _mgr(monkeypatch, 现在无头=False, 配置无头=True, running=False)
        assert m.realign_to_config() is False

    def test_换完重建搜索标签页(self, monkeypatch):
        m = _mgr(monkeypatch, 现在无头=False, 配置无头=True)
        建的 = []
        monkeypatch.setattr(m, "get_search_page", lambda: 建的.append(1))
        m.realign_to_config()
        assert 建的, "close() 把标签页置空了，不重建下一轮拿的是旧句柄"


def _loop():
    with patch("boss_bot.main_loop.BrowserManager"):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop(account_index=1)
    lp._log = lambda *a: None
    lp._stop_event = threading.Event()
    lp._running = False
    lp._init_engines = MagicMock()
    lp.browser_manager = MagicMock()
    return lp


class TestLoopSwitchesBack:

    def test_登录时换过有头_登完换回(self):
        lp = _loop()
        lp.browser_manager.realign_to_config.return_value = True
        lp._login_headed_temp = True
        lp._login_window_shown = True
        lp._finish_login_window()
        assert lp.browser_manager.realign_to_config.called
        assert lp._init_engines.called, "换了浏览器就要重建引擎"
        assert lp._login_window_shown is False

    def test_没换过有头就不重开(self):
        """面板上把无头那侧的浏览器无故重启一次，等于自己把投递打断"""
        lp = _loop()
        lp._login_headed_temp = False
        lp._login_window_shown = True
        lp._finish_login_window()
        assert not lp.browser_manager.realign_to_config.called

    def test_换回之后标记清干净(self):
        lp = _loop()
        lp.browser_manager.realign_to_config.return_value = True
        lp._login_headed_temp = True
        lp._finish_login_window()
        assert lp._login_headed_temp is False
        lp._finish_login_window()          # 第二次不该再动
        assert lp.browser_manager.realign_to_config.call_count == 1

    def test_换回只在安全的线程上做(self):
        """换形态要 close+launch 十几秒，只许在"这一侧自己的安全点"上做。

        面板的"我已登录"按钮是 HTTP 请求带着 manager 锁直接调进 confirm_login
        的，在那儿重开浏览器会把状态轮询一起卡住；回复线程动手则是从另一条腿
        脚下抽走打招呼正在用的标签页。所以锚点只有三处：启动登录成功、面板登录
        成功、打招呼轮健康通过后的那一句。
        """
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        for 锚 in ("登录确认后保存 Cookie", "def _finish_manual_login",
                   "self._current_mode = \"greet\""):
            at = src.index(锚)
            assert "_finish_login_window()" in src[max(0, at - 900):at + 900], 锚
        块 = src[src.index("def confirm_login"):
                 src.index("def _check_and_archive_daily_data")]
        assert "_finish_login_window(" not in 块

    def test_回复侧借来的有头等打招呼腿去还(self):
        """回复侧只负责把窗口摆出来，不动浏览器——它没有安全点"""
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = src[src.index("def _reply_login_guard"):src.index("def _reply_login_ok")]
        assert "realign_to_config" not in 块


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
