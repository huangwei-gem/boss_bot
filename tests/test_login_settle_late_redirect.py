# -*- coding: utf-8 -*-
"""跳转晚到一秒，就不许把登录页认成"已自动登录"。

2026-10-19 08:35 实测：账号2 启动时两路证据矛盾，复核走完 _settle_url()
读到 https://www.zhipin.com/web/geek/chat 就判「Cookie 有效，已自动登录」，
日志、界面全绿；可 08:36 从 CDP 只读那张标签页，地址是
https://www.zhipin.com/web/user/，标题「BOSS直聘注册登录」。也就是说
BOSS 的 SPA 先把会话页渲染出来，跳转发生在"连续两次读到同一个地址"之后——
判据收工收得太早，拿一个转瞬即逝的好消息当了定论。

这类假绿比假红更贵：号其实登不进去，界面却说他好了，于是一整天 0 投递，
他还得自己发现。规则改成不对称的：**坏消息（被踢回登录页/风控页）随到随收，
好消息要撑满整个观察窗口才算数**。代价是登录检查最多多花几秒，
而这几秒只在启动与等待人工登录时出现。
"""

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.browser_launcher import login_state_of
from boss_bot.main_loop import UnifiedBotLoop

会话页 = "https://www.zhipin.com/web/geek/chat"
登录页 = "https://www.zhipin.com/web/user/"
风控页 = "https://www.zhipin.com/web/passport/zp/verify.html?code=xxx"


class 会变的页面:
    """按读取次数发地址，模拟"先停在会话页、晚一点才被踢走"。"""

    def __init__(self, 序列):
        self.序列 = 序列
        self.第几次 = 0

    @property
    def url(self):
        值 = self.序列[min(self.第几次, len(self.序列) - 1)]
        self.第几次 += 1
        return 值


def _环():
    环 = UnifiedBotLoop.__new__(UnifiedBotLoop)
    环._log = lambda *a, **k: None
    return 环


class Test迟到跳转不许冒充已登录:
    def test_晚到的跳转要在同一轮里被看到(self):
        页 = 会变的页面([会话页, 会话页, 会话页, 登录页, 登录页, 登录页])
        结果 = _环()._settle_url(页, timeout=4.0)
        assert 结果 == 登录页, f"判据提前收工，把 {结果} 当成了定论"

    def test_这一眼不能判成已登录(self):
        页 = 会变的页面([会话页, 会话页, 会话页, 登录页, 登录页])
        结果 = _环()._settle_url(页, timeout=4.0)
        # 浏览器里 Cookie 还在（这正是 10-18 晚上账号2 的样子）：
        # 页面被踢回登录页 + Cookie 有效 = 两路矛盾，绝不许单独认好消息
        assert login_state_of(结果, True) != "logged_in", \
            "迟到的登录页没被看见，号会被报成健康但其实一点活都干不了"

    def test_晚到的风控页也随时收(self):
        页 = 会变的页面([会话页, 会话页, 风控页, 风控页, 风控页])
        t0 = time.time()
        结果 = _环()._settle_url(页, timeout=6.0)
        assert 结果 == 风控页
        assert time.time() - t0 < 5.0, "坏消息不该等满整个窗口"

    def test_一直在登录页不必等满窗口(self):
        页 = 会变的页面([登录页] * 20)
        t0 = time.time()
        assert _环()._settle_url(页, timeout=6.0) == 登录页
        assert time.time() - t0 < 5.0

    def test_真登录着的页面照样认(self):
        """反向哨兵：改成"好消息要看满窗口"之后，稳定停在会话页仍然算登录着。"""
        页 = 会变的页面([会话页] * 40)
        结果 = _环()._settle_url(页, timeout=2.0)
        assert 结果 == 会话页
        assert login_state_of(结果, True) == "logged_in"

    def test_读不到地址时不许假装稳定(self):
        页 = 会变的页面(["", "", ""])
        assert _环()._settle_url(页, timeout=1.0) == ""


class Test界面那一眼还是要快:
    """闲着的标签页没有"跳转晚到"这回事，快读两次一样就走。

    /api/status 每几秒就会问一次这个号的登录态（两个号串着问），要是每次都
    陪一张停着的页等满 8 秒，界面就变成幻灯片——那条路本来就是"路过看一眼"，
    满窗口的盯法只给"刚导航过"的那一眼。
    """

    def test_路过看一眼不许等满窗口(self):
        环 = UnifiedBotLoop.__new__(UnifiedBotLoop)
        环._log = lambda *a, **k: None
        环._has_live_auth_cookie = lambda instance: True
        页 = 会变的页面([会话页] * 200)
        t0 = time.time()
        assert 环._login_state_read(页) == "logged_in"
        花了 = time.time() - t0
        assert 花了 < 3.0, f"闲着的页也等满窗口：{花了:.1f} 秒"

    def test_刚导航过的那一眼要等满窗口(self):
        环 = UnifiedBotLoop.__new__(UnifiedBotLoop)
        环._log = lambda *a, **k: None
        环._has_live_auth_cookie = lambda instance: True

        class 会导航的页(会变的页面):
            def get(self, url):
                self.第几次 = 0

        页 = 会导航的页([会话页] * 200)
        t0 = time.time()
        assert 环._login_state_now(页) == "logged_in"
        assert time.time() - t0 >= 1.5, "导航后那一读没有看满窗口，晚到的跳转就看不见"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
