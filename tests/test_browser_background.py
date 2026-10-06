# -*- coding: utf-8 -*-
"""浏览器要能在幕后跑：启动不抢焦点、窗口收进任务栏、无头可选。

用户原话是"启动浏览器的时候不要把浏览器显示到最前面，在幕后就行"。
两个号一天各启动一次浏览器，前置一次就打断一次他正在敲的东西。

坑在两处：
1. SW_MINIMIZE 会顺带把焦点抢过去，必须用 SW_SHOWMINNOACTIVE；
2. Chrome 把最小化/被遮挡的窗口当后台标签页，定时器降到每分钟一次、
   rAF 直接停 —— 不收这三档节流的话，窗口是藏起来了，投递也一起停了。
"""
import pytest

from boss_bot import browser_launcher as BL
from boss_bot.browser_launcher import (
    BACKGROUND_FLAGS, CHROME_WINDOW_CLASS, pick_browser_windows,
)


def _win(hwnd, cls=CHROME_WINDOW_CLASS, visible=True, iconic=False, pid=100):
    return (hwnd, cls, visible, iconic, pid)


class Test窗口挑选:
    def test_只收可见且没最小化的浏览器窗口(self):
        got = pick_browser_windows([_win(1), _win(2, pid=999),
                                    _win(3, visible=False), _win(4, iconic=True)], (100,))
        assert got == [1]

    def test_别的程序的窗口一个都不碰(self):
        """Chrome_WidgetWin_1 同时是隐藏消息窗口的类名，所以可见性必须查；
        更不许因为"看着像 Chrome"就把用户自己开的浏览器收起来。"""
        rows = [_win(1, cls="Chrome_WidgetWin_1", pid=7),      # 不是我们的 pid
                _win(2, cls="Edge_869042064", pid=100),         # 别的程序
                _win(3, cls=CHROME_WINDOW_CLASS, pid=100)]
        assert pick_browser_windows(rows, (100,)) == [3]

    def test_号都对不上时返回空表(self):
        assert pick_browser_windows([_win(1)], ()) == []


class Test启动接线:
    @pytest.fixture
    def recorded(self, monkeypatch):
        """把 DrissionPage 的两个入口换成记录器，再给 minimize 装个探针。"""
        calls = {"minimize": [], "page": []}

        class FakeOptions:
            def __init__(self):
                self.args = []

            def set_argument(self, s):
                self.args.append(s)

            def set_browser_path(self, p):
                pass

            def set_local_port(self, p):
                pass

            def set_user_agent(self, ua):
                pass

            def set_proxy(self, proxy):
                pass

        class FakePage:
            class _B:
                process_id = 4321
            browser = _B()

        import DrissionPage
        monkeypatch.setattr(DrissionPage, "ChromiumOptions", FakeOptions)
        monkeypatch.setattr(DrissionPage, "ChromiumPage",
                            lambda co: calls["page"].append(co.args) or FakePage())
        monkeypatch.setattr(BL, "_is_port_open", lambda h, p, timeout=1.0: False)
        monkeypatch.setattr(BL, "minimize_browser_windows",
                            lambda pid, timeout=8.0: calls["minimize"].append(pid) or 1)
        return calls

    def _launch(self, calls, **kw):
        opts = dict(chrome_path="chrome.exe", user_agent="", proxy="",
                    viewport_width=1280, viewport_height=800, port=0,
                    user_data_dir="")
        opts.update(kw)
        BL._launch_windows(**opts)
        return calls

    def test_有头且后台时收起窗口(self, recorded):
        self._launch(recorded, headless=False, background=True)
        assert recorded["minimize"] == [4321]

    def test_无头时不去收一个根本不存在的窗口(self, recorded):
        """收不到窗口只会在日志里留一条"没找到需要收起的窗口"，
        把日志搞成每次启动都有告警的假象。"""
        self._launch(recorded, headless=True, background=True)
        assert recorded["minimize"] == []

    def test_关掉后台就保持原来的前置行为(self, recorded):
        self._launch(recorded, headless=False, background=False)
        assert recorded["minimize"] == []

    def test_后台模式必须同时关掉三档节流(self, recorded):
        self._launch(recorded, headless=False, background=True)
        args = recorded["page"][0]
        for flag in BACKGROUND_FLAGS:
            assert flag in args, f"{flag} 没带上，窗口一收起投递就卡死"

    def test_无头时不启动浏览器进程外的多余参数(self, recorded):
        """headless 只由用户配置决定，不许被后台开关偷偷带成默认无头 ——
        BOSS 对无头更敏感，而且没窗口就没法扫码登录。"""
        assert "if headless:" in __import__("inspect").getsource(BL._launch_windows)


class Test配置往返:
    @staticmethod
    def _load(tmp_path, data=None):
        import json
        from boss_bot.unified_config import UnifiedConfig
        path = tmp_path / "bot_config.json"
        if data is None:
            data = {}
        path.write_text(json.dumps(data), encoding="utf-8")
        return UnifiedConfig.load(
            config_path=str(path),
            profile_path=str(tmp_path / "none_profile.json"),
            overrides_path=str(tmp_path / "none_overrides.json"),
        )

    def test_缺省就是后台(self, tmp_path):
        """默认值必须是"不抢焦点"：这条是用户提的需求，不该要他去点开关。"""
        assert self._load(tmp_path).browser.background is True

    def test_配置文件写false就生效(self, tmp_path):
        cfg = self._load(tmp_path, {"browser": {"background": False}})
        assert cfg.browser.background is False

    def test_存出去还读得回来(self, tmp_path):
        cfg = self._load(tmp_path)
        cfg.browser.background = False
        assert self._load(tmp_path, cfg.to_dict()).browser.background is False

    def test_环境变量能把窗口放出来(self, tmp_path, monkeypatch):
        """扫码登录要把窗口摆到桌面上，不能让人去改配置文件。"""
        monkeypatch.setenv("BOSS_BOT_BACKGROUND", "0")
        assert self._load(tmp_path).browser.background is False
        monkeypatch.setenv("BOSS_BOT_BACKGROUND", "1")
        assert self._load(tmp_path).browser.background is True

    def test_BrowserManager把开关传下去(self, monkeypatch):
        seen = {}

        def fake_launch(**kw):
            seen.update(kw)
            return object()
        monkeypatch.setattr(BL, "launch_browser", fake_launch)

        class Cfg:
            headless = True
            background = False
            user_agent = ""
            proxy = ""
            viewport_width = 1280
            viewport_height = 800
            chrome_path = ""
            browser_type = "chrome"
            cookie_file = ""
            user_data_dir = ""

        BL.BrowserManager(config=Cfg(), account_index=0, port=9299).launch()
        assert seen["background"] is False
        assert seen["headless"] is True
