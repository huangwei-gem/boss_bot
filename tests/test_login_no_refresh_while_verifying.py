# -*- coding: utf-8 -*-
"""人在登录页/安全验证页上滑验证时，机器一次都不许刷新那一页。

2026-10-19 13:52~14:00 实测：账号2 进了"等人工登录"的循环以后，
`_wait_for_login` 每 5 秒轮一次，只要浏览器里已经有登录项（那份档案里本来就
留着 wt2/zp_at/wbg），它每轮都 `instance.get("…/web/geek/chat")` 把**人正在用的
那张标签页**整页导航掉。他在那一页滑"当前 IP 地址可能存在异常访问行为"的安全验证，
刚点完就被刷没，反复五次以上——原话是「你刷新太频繁了，我根本就登录不了」。

那一页不需要我们导航：验证滑完 BOSS 自己会跳走，跳到会话页 + 浏览器里有登录项
就是登录成功，这两路证据用只读的一眼就够。导航只能当例外，不能当轮询。
"""
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import login_state_of  # noqa: E402

验证页 = "https://www.zhipin.com/web/passport/zp/verify.html?backUrl=xxx"
登录页 = "https://www.zhipin.com/web/user/?ka=header-login"
会话页 = "https://www.zhipin.com/web/geek/chat"
中性页 = "https://www.zhipin.com/changsha/?seoRefer=index"


def _回路(monkeypatch, 页面, 有登录项=True, 看得到登录=None):
    """只留"眼睛"和"手"：页面读成 页面，导航这件事记在 导航 里。"""
    with patch("boss_bot.main_loop.BrowserManager"):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop(account_index=1)
    lp.日志 = []
    lp._log = lambda level, msg: lp.日志.append((level, msg))
    lp._running = True
    lp._stop_event = threading.Event()
    lp._login_event = threading.Event()
    monkeypatch.setattr(lp._login_event, "wait", lambda timeout=None: False)
    monkeypatch.setattr(lp._stop_event, "wait", lambda timeout=None: False)
    lp.config.login.wait_timeout = 3
    判据 = 看得到登录 if 看得到登录 is not None else 有登录项
    monkeypatch.setattr(lp, "_settle_url", lambda inst, timeout=None, 早收=True: 页面)
    monkeypatch.setattr(lp, "_has_live_auth_cookie", lambda inst: 有登录项)
    monkeypatch.setattr(lp, "_login_state_read",
                        lambda inst, 早收=True: login_state_of(页面, 判据))
    导航 = []
    monkeypatch.setattr(lp, "_login_state_now",
                        lambda inst: 导航.append(页面) or "uncertain")
    return lp, 导航


class 验证页上不许刷新Test:

    def test_停在安全验证页一次都不许导航(self, monkeypatch):
        lp, 导航 = _回路(monkeypatch, 验证页)
        assert lp._wait_for_login(object(), "x.json") is False
        assert 导航 == [], "人在滑验证，每轮 get 一次就是把他刚滑完的那一页掀掉: %r" % (导航,)
        assert any("不刷新" in m or "别刷" in m for _, m in lp.日志), lp.日志

    def test_停在登录页等人填的时候也不许导航(self, monkeypatch):
        """登录页上有二维码和手机号输入框：跳去会话页=二维码刷没、填到一半的号没了。"""
        lp, 导航 = _回路(monkeypatch, 登录页)
        assert lp._wait_for_login(object(), "x.json") is False
        assert 导航 == [], lp.日志

    def test_页面自己跳到会话页就算登进去(self, monkeypatch):
        """反向哨兵：不导航不许把"已经登进来"也一起漏掉——验证滑完 BOSS 会自己跳。"""
        lp, 导航 = _回路(monkeypatch, 会话页)
        assert lp._wait_for_login(object(), "x.json") is True, lp.日志

    def test_中性页上导航要有冷却(self, monkeypatch):
        """这一眼定不了论时才导航去问一次，但不许每 5 秒问一遍。"""
        lp, 导航 = _回路(monkeypatch, 中性页, 有登录项=True, 看得到登录=False)
        assert lp._wait_for_login(object(), "x.json") is False
        assert len(导航) <= 1, "3 秒窗口里导航了 %d 次，按 5 秒一轮的节奏就是一分钟 12 次" % len(导航)

    def test_没有登录项时压根不去动页面(self, monkeypatch):
        """人还没扫完码：这条本来就有，留着当哨兵，别改的时候顺手弄丢。"""
        lp, 导航 = _回路(monkeypatch, 登录页, 有登录项=False)
        assert lp._wait_for_login(object(), "x.json") is False
        assert 导航 == []


class 承诺不许再写反Test:
    """摆窗口那句承诺得和等待循环真做的一致。"""

    def test_等待循环里不许出现每轮都导航的写法(self):
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = 源[源.index("def _wait_for_login"):源.index("def _settle_url")]
        assert "_wait_不刷新这一页" in 块 or "不刷新" in 块, "等待循环没留下'为什么不刷'的自述"
        # 2026-10-22：冷却从这两处各自的一句 if 收进 这一眼该不该导航()，
        # 因为启动复验那条没接进来，一重启就发了 19 次整页加载（见 test_verify_startup_burst.py）
        assert "这一眼该不该导航(" in 块, "导航判据被拆回去了：两份判据早晚一份有冷却一份没有"
        判据 = 源[源.index("def 这一眼该不该导航"):源.index("def archive_cookie_file")]
        assert "登录导航冷却秒" in 判据, "导航没有冷却就是每 5 秒刷人一次"
        assert "是风控验证页(页面)" in 判据, "判据里必须先认验证页：那一页一律不导航"


class 摆窗口那一跳也算刷新Test:
    """`_ensure_login_window(导航=True)` 是启动时那一下：那一页本来就是我们要他
    停的地方，再导航一次就是把他刚刷出来的二维码换一张、填到一半的号清掉。"""

    def _对象(self, monkeypatch, 停在):
        import types
        记 = []
        管理器 = types.SimpleNamespace(
            get_instance=lambda: types.SimpleNamespace(url=停在),
            show_login_window=lambda url, navigate=True: navigate and 跳.append(url) or
            {"windows": 1, "relaunched": False})
        跳 = []
        对象 = types.SimpleNamespace(
            _login_window_shown=False, _login_headed_temp=False,
            account_name="账号2", browser_manager=管理器,
            _log=lambda lvl, msg: 记.append((lvl, msg)), _init_engines=lambda **kw: None)
        monkeypatch.setattr(对象, "_验证页已说", False, raising=False)
        return 对象, 记, 跳

    def test_已经在登录页就不许再导航一次(self, monkeypatch):
        from boss_bot.main_loop import UnifiedBotLoop
        对象, 记, 跳 = self._对象(monkeypatch, 登录页)
        assert UnifiedBotLoop._ensure_login_window(对象, "启动时", 导航=True) is True
        assert 跳 == [], "页本来就在登录页，再 get 一次就是把他那一屏清掉"
        assert any("不再刷新" in m for _, m in 记), 记

    def test_停在安全验证页也不许导航(self, monkeypatch):
        from boss_bot.main_loop import UnifiedBotLoop
        对象, 记, 跳 = self._对象(monkeypatch, 验证页)
        UnifiedBotLoop._ensure_login_window(对象, "启动时", 导航=True)
        assert 跳 == [], 记

    def test_停在别的地方照旧要送去登录页(self, monkeypatch):
        """反向哨兵：会话页/城市页上头什么都不缺，不导航他就看不到登录入口。"""
        from boss_bot.main_loop import UnifiedBotLoop
        对象, 记, 跳 = self._对象(monkeypatch, 中性页)
        UnifiedBotLoop._ensure_login_window(对象, "启动时", 导航=True)
        assert 跳 == ["https://www.zhipin.com/web/user/?ka=header-login"], 记


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
