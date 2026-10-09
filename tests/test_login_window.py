# -*- coding: utf-8 -*-
"""要人工登录时必须真有一个窗口冒出来。

2026-10-09 用户截图：面板弹出「需要登录 Boss 直聘 …请在浏览器中登录后点击
我已登录」，可桌面上什么窗口都没有——因为 browser.headless=true，那个账号的
浏览器是无头起来的，"请在浏览器中登录"这句话在这种形态下永远做不到。
这一组回归锁的是：判定要人工登录的那一刻，代码必须把窗口弄成看得见的。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import browser_launcher as bl  # noqa: E402


class _FakeUser32:
    def __init__(self):
        self.calls = []

    def ShowWindowAsync(self, hwnd, cmd):
        self.calls.append(("show", int(hwnd), cmd))

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("show_sync", int(hwnd), cmd))

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("fg", int(hwnd)))

    def IsIconic(self, hwnd):
        return False


class _StubbornUser32(_FakeUser32):
    """第一次回看还说"缩着"——异步 ShowWindowAsync 没生效的那种系统。"""

    def __init__(self):
        super().__init__()
        self.看的次数 = 0

    def IsIconic(self, hwnd):
        self.看的次数 += 1
        return self.看的次数 <= 2


class _FakeHwnd:
    def __init__(self, v):
        self.v = v

    def __int__(self):
        return self.v


class TestRestoreWindows:

    def test_恢复并前置该进程的窗口(self, monkeypatch):
        fake = _FakeUser32()
        monkeypatch.setattr(bl, "_IS_WINDOWS", True)
        monkeypatch.setattr(bl, "pick_browser_windows",
                            lambda allw, pids, **kw: [111, 222])
        n = bl.restore_browser_windows(4321, user32=fake)
        assert n == 2
        assert ("show", 111, bl.SW_RESTORE) in fake.calls
        assert ("fg", 222) in fake.calls

    def test_非Windows直接返回0(self, monkeypatch):
        monkeypatch.setattr(bl, "_IS_WINDOWS", False)
        assert bl.restore_browser_windows(1) == 0

    def test_没有窗口时不报错(self, monkeypatch):
        monkeypatch.setattr(bl, "_IS_WINDOWS", True)
        monkeypatch.setattr(bl, "pick_browser_windows", lambda a, p, **kw: [])
        assert bl.restore_browser_windows(4321, user32=_FakeUser32()) == 0

    def test_异步没生效就补一次同步的(self, monkeypatch):
        """不然面板会报"窗口已弹出"，用户桌面上却还是任务栏那一条"""
        fake = _StubbornUser32()
        monkeypatch.setattr(bl, "_IS_WINDOWS", True)
        monkeypatch.setattr(bl, "pick_browser_windows", lambda a, p, **k: [111])
        assert bl.restore_browser_windows(4321, user32=fake, timeout=1.0) == 1
        assert ("show_sync", 111, bl.SW_RESTORE) in fake.calls

    def test_已最小化的窗口也要挑得出来(self):
        """收起动作那条"已经最小化就跳过"的省工判断，捞回来时正好把目标筛空"""
        窗口表 = [
            (111, bl.CHROME_WINDOW_CLASS, True, True, 4321),    # 最小化着的
            (222, bl.CHROME_WINDOW_CLASS, True, False, 4321),   # 摆在桌面的
            (333, "Chrome_WidgetWin_0", True, True, 4321),      # 隐藏消息窗口
            (444, bl.CHROME_WINDOW_CLASS, False, True, 4321),   # 不可见的
            (555, bl.CHROME_WINDOW_CLASS, True, True, 9999),    # 别人的浏览器
        ]
        assert bl.pick_browser_windows(窗口表, (4321,), include_iconic=True) == [111, 222]
        assert bl.pick_browser_windows(窗口表, (4321,)) == [222], "收起那一侧不许跟着变"


def _mgr(monkeypatch, headless_launched, port=9222, running=True):
    """造一个 BrowserManager，浏览器/启动/形态探测都换成假的，只验行为。"""
    class FakeTab:
        def __init__(self):
            self.got = []

        def get(self, url):
            self.got.append(url)

    class FakePage:
        def __init__(self):
            self.tab = FakeTab()
            self.process_id = 4321

        def get(self, url):
            self.tab.get(url)

    launched = []

    def fake_launch(**kw):
        launched.append(kw)
        return bl.BrowserInstance(chrome_page=FakePage())

    def fake_mode(p):
        if not running:
            return {"running": False, "headless": None}
        return {"running": True, "headless": bool(headless_launched)}

    monkeypatch.setattr(bl, "launch_browser", fake_launch)
    monkeypatch.setattr(bl, "restore_browser_windows",
                        lambda pid, **kw: 1)
    monkeypatch.setattr(bl, "browser_mode", fake_mode)
    cfg = type("C", (), {"headless": True, "background": True,
                         "cookie_file": ""})()
    m = bl.BrowserManager(cfg, account_index=0, port=port)
    if headless_launched is not None:
        m._instance = bl.BrowserInstance(chrome_page=FakePage())
        m._headless = True
    m._launched_for_test = launched
    return m


class TestShowLoginWindow:

    def test_无头起来的重开成有头(self, monkeypatch):
        m = _mgr(monkeypatch, headless_launched=True)
        果 = m.show_login_window("https://www.zhipin.com/web/user/")
        assert 果["relaunched"] is True
        assert m._launched_for_test, "没重开浏览器"
        kw = m._launched_for_test[-1]
        assert kw["headless"] is False, "还是无头，用户看不见窗口"
        assert kw["background"] is False, "收进任务栏等于没弹"
        assert 果["url_shown"] == "https://www.zhipin.com/web/user/"

    def test_已经有头的只把窗口捞回来(self, monkeypatch):
        m = _mgr(monkeypatch, headless_launched=False)
        果 = m.show_login_window("https://www.zhipin.com/web/user/")
        assert 果["relaunched"] is False
        assert not m._launched_for_test, "有头不该重开"
        assert 果["windows"] >= 1
        assert 果["url_shown"] == "https://www.zhipin.com/web/user/"

    def test_浏览器没起来就直接有头启动(self, monkeypatch):
        m = _mgr(monkeypatch, headless_launched=None)
        果 = m.show_login_window("https://www.zhipin.com/web/user/")
        assert 果["relaunched"] is True
        assert m._launched_for_test[-1]["headless"] is False

    def test_浏览器半路死了也要重开(self, monkeypatch):
        """手里还攥着连不上的旧句柄时，只按"上次是不是有头"判会以为不用重开，
        然后对着死句柄导航，报出来一句空错（12:33 实测）"""
        m = _mgr(monkeypatch, headless_launched=False, running=False)
        果 = m.show_login_window("https://www.zhipin.com/web/user/")
        assert 果["relaunched"] is True
        assert m._launched_for_test[-1]["headless"] is False
        assert 果["error"] == ""

    def test_重开后搜索标签页要重建(self, monkeypatch):
        """close() 会把 _search_tab 置空，不重建下一轮打招呼拿的就是旧句柄"""
        m = _mgr(monkeypatch, headless_launched=True)
        建的 = []
        monkeypatch.setattr(m, "get_search_page", lambda: 建的.append(1))
        果 = m.show_login_window("https://x")
        assert 果["relaunched"] is True
        assert 建的, "重开完没重建搜索标签页"


class TestMainLoopCallsIt:

    def _src(self):
        return (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")

    def test_有专门的登录窗口方法(self):
        src = self._src()
        assert "def _ensure_login_window" in src

    def test_启动时的登录墙会弹窗口(self):
        src = self._src()
        at = src.index("准备把浏览器窗口摆到桌面")
        assert "_ensure_login_window(" in src[max(0, at - 1200):at + 1200]

    def test_运行中打招呼侧掉登录会弹窗口(self):
        src = self._src()
        at = src.index('self._log("WARN", "登录态失效，等待重新登录...")')
        assert "_ensure_login_window(" in src[at:at + 600]

    def test_回复侧掉登录会弹窗口(self):
        src = self._src()
        at = src.index('"回复侧确认登录态失效，等待重新登录..."')
        assert "_ensure_login_window(" in src[max(0, at - 700):at + 400]

    def test_面板登录按钮也走同一条路(self):
        src = self._src()
        at = src.index("def open_login_page")
        assert "_ensure_login_window(" in src[at:at + 1600]

    def test_同一轮只弹一次(self):
        """两个线程都会判掉登录，反复重开浏览器会把登录态自己弄丢"""
        src = self._src()
        at = src.index("def _ensure_login_window")
        seg = src[at:at + 1200]
        assert "_login_window_shown" in seg


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
