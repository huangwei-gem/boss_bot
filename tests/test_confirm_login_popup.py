# -*- coding: utf-8 -*-
"""点「我已登录」也要把浏览器窗口摆到眼前——无头也一样。

用户 2026-10-09：「记得在点击我一登录的时候也要弹窗，无头模式也一样」。
他点这个按钮是要核对"到底登进去没有"，桌面上没有窗口的话这句确认就是空的。
所以这一步不能只在"本来就有头、只是收进任务栏"时捞窗口，无头那侧必须临时
重开成有头；但也只摆窗口、不把他踢回登录页（他已经登进去了，跳登录页等于
把会话页导航走）。
"""
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.main_loop import UnifiedBotLoop  # noqa: E402


def _loop(调用记录):
    with patch("boss_bot.main_loop.BrowserManager"):
        lp = UnifiedBotLoop(account_index=0)
    lp._log = lambda *a: None
    lp._stop_event = threading.Event()
    lp._login_event = threading.Event()
    lp._greet_engine = None
    lp._chat_handler = None

    def 假摆窗口(侧="", 导航=True):
        调用记录.append({"侧": 侧, "导航": 导航})
        return True

    lp._ensure_login_window = 假摆窗口
    return lp


def test_点我已登录会摆窗口():
    调用 = []
    lp = _loop(调用)
    lp._login_window_shown = True          # 这一轮已经摆过一次也要再摆
    lp.confirm_login()
    lp._popup_thread.join(5)
    assert 调用, "点确认登录却什么都不摆，用户没法核对"
    assert 调用[0]["侧"] == "面板"


def test_摆窗口不许把用户踢回登录页():
    """他已经登进去了，这时导航到登录页等于把会话页冲掉"""
    调用 = []
    lp = _loop(调用)
    lp.confirm_login()
    lp._popup_thread.join(5)
    assert 调用[0]["导航"] is False


def test_摆窗口不压在HTTP请求里做():
    """重开一次浏览器十几秒，confirm_login 是面板 HTTP 线程带着 manager 锁调进来的，
    在那儿等就等于把状态轮询一起卡住"""
    调用 = []
    起的 = []
    lp = _loop(调用)

    class 假线程:
        def __init__(self, target=None, **kw):
            self._target = target

        def start(self):
            起的.append(self._target)

        def join(self, _=None):
            pass

    with patch("boss_bot.main_loop.threading.Thread", 假线程):
        lp.confirm_login()
        assert 调用 == [], "HTTP 线程里自己动手摆窗口会把面板卡住"
    起的[0]()
    assert 调用


def test_登录成功后要登进来的状态照常推进():
    调用 = []
    lp = _loop(调用)
    lp._needs_login = True
    lp._logged_in = False
    lp._login_reason = "cookie_expired"
    lp.confirm_login()
    lp._popup_thread.join(5)
    assert lp._logged_in is True
    assert lp._needs_login is False
    assert lp._login_reason == ""
    assert lp._login_event.is_set()


def test_面板登录按钮那条路径还是原来的样子():
    """open_login_page 是"就是要看登录页"，那边导航必须是 True"""
    src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
    at = src.index("def open_login_page")
    assert '_ensure_login_window("面板")' in src[at:at + 1500]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
