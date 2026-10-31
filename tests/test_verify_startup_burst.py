# -*- coding: utf-8 -*-
"""重启那 110 秒里不许拿整页加载做压力测试——这就是安全验证页的来路。

2026-10-22 09:19:18 面板重启，两个号一起起来。日志逐行数出来的账：

    09:19:24 [主账号] 正在访问主站 https://www.zhipin.com        ← 第 1 次整页加载
    09:19:36 [主账号] 主站访问完成，当前URL: …/verify.html…      ← 一开就撞在验证页上
    09:19:36 [主账号] 正在访问聊天页面验证登录态...
    09:19:42 → 09:21:05  第 1..14 眼定不了论                      ← 每一眼 = get(会话页)
    09:21:11 [主账号] 复验 15 眼仍看不准
    09:21:12 浏览器已关闭 → 以有头形态重开（为了把窗口摆到他眼前）
    09:21:15 已复用初始标签页作为搜索/打招呼页                     ← get(搜索页)
    09:21:18 已创建回复引擎专用聊天标签页                          ← new_tab(get(会话页))

主账号 19 次、账号2 18 次，两台同一个出口 IP，全挤在 130 秒里。CDP 只读一查，
每个号两张一模一样的 `安全验证 - BOSS直聘` 标签页——就是最后那两下开出来的。

三条都是白烧的请求，而且每一条都在violating他的红线「我在安全验证就不要刷新」：
1. 复验那 15 眼根本不用导航：定论用只读的一眼就够（`_wait_for_login` 10-19 已经改成
   只读优先 + 60 秒导航冷却，启动这条却漏了，`登录导航冷却秒` 没管住它）。
2. 摆登录窗口重开之后先加载一遍搜索页，紧接着又把它导航去登录页——那一趟搜索页
   白加载，还多撞一次验证。
3. `_init_engines` 顺手 new_tab(会话页)，那张页在没登录时必然被踢到 verify.html，
   于是"一个号两张验证页"，第二张还是他正要滑的那一张的副本。

这一组回归锁的是"重启一次到底发几次整页加载"，不是"验证页能不能识别"（那在
test_verify_page_watch.py / test_login_no_refresh_while_verifying.py）。
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import main_loop as ML                                    # noqa: E402
from boss_bot import browser_launcher as BL                             # noqa: E402

验证页 = "https://www.zhipin.com/web/passport/zp/verify.html?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2F"
登录页 = "https://www.zhipin.com/web/user/?ka=header-login"
会话页 = "https://www.zhipin.com/web/geek/chat"
中性页 = "https://www.zhipin.com/changsha/?seoRefer=index"


class 假钟:
    """只给这一条流程用的钟：导航冷却和复验窗口都按它走，测试才能既不真等 90 秒
    又不把"一分钟 12 次导航"那种节奏混过去。"""

    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def 走(self, s):
        self.t += s


class 页面替身:
    """一张页：读 URL 免费（CDP），导航记账。"""

    def __init__(self, url):
        self._url = url
        self.导航 = []

    @property
    def url(self):
        return self._url

    def get(self, url):
        self.导航.append(url)
        self._url = url
        return True

    def _get_all_cookies(self):
        return []


def _复验回路(monkeypatch, 起手页, 状态序列=None, 有登录项=False, 一秒走=5.0):
    """把 _自动登录尝试 需要的眼睛全部换成替身，只留"导航了几次"这一件事可测。"""
    with patch("boss_bot.main_loop.BrowserManager"):
        lp = ML.UnifiedBotLoop(account_index=1)
    lp.日志 = []
    lp._log = lambda level, msg: lp.日志.append((level, msg))
    lp._running = True
    lp._stop_event = threading.Event()
    lp._login_event = threading.Event()
    lp._cookie_file = lambda: "zhipin_cookies.json"
    lp.browser_manager = MagicMock()
    lp.browser_manager.load_cookies.return_value = True
    lp._discard_stale_cookies = MagicMock()
    lp._save_cookies_if_logged_in = MagicMock(return_value=True)
    页 = 页面替身(起手页)
    lp.browser_manager.get_instance.return_value = 页
    序列 = list(状态序列 or [])
    眼 = []

    def _读(inst, 早收=True):
        眼.append("只读")
        return 序列.pop(0) if 序列 else "uncertain"

    def _导航读(inst):
        眼.append("导航")
        inst.get(会话页)
        return 序列.pop(0) if 序列 else "uncertain"

    monkeypatch.setattr(lp, "_login_state_read", _读)
    monkeypatch.setattr(lp, "_login_state_now", _导航读)
    monkeypatch.setattr(lp, "_has_live_auth_cookie", lambda inst: 有登录项)
    monkeypatch.setattr(lp, "_interruptible_sleep", lambda s: 钟.走(一秒走))
    monkeypatch.setattr(lp, "_settle_url",
                        lambda inst, timeout=None, 早收=True: inst.url)
    钟 = 假钟()
    monkeypatch.setattr(ML, "time", 钟)
    lp.眼 = 眼
    return lp, 页, 钟


class 启动复验不许按秒导航Test:

    def test_一开就停在验证页时一次都不许导航(self, monkeypatch):
        """09:19:36 就是现场：主站那一读已经撞在 verify.html 上，
        后面 15 眼每一眼都是把那一页整页掀掉——他正在滑的话全白做。"""
        lp, 页, 钟 = _复验回路(monkeypatch, 验证页)
        assert lp._自动登录尝试(页) is False
        assert 页.导航 == [], "浏览器已经停在验证页，还发 %d 次整页加载: %r" % (
            len(页.导航), 页.导航)
        assert lp._login_reason == "login_uncertain", lp._login_reason
        assert lp._确认登录墙 is False, "验证页不是登录墙，不许据此把 Cookie 文件处理掉"
        lp._discard_stale_cookies.assert_not_called()
        assert any("不刷新" in m or "一次都不" in m or "不再" in m for _, m in lp.日志), lp.日志

    def test_看不准时导航次数要有冷却而不是一眼一次(self, monkeypatch):
        """90 秒窗口、5 秒一眼 = 18 眼，按 登录导航冷却秒 最多问 2 次。
        反向哨兵在下面那条：只读的那几眼照样要能认出登录成功。"""
        lp, 页, 钟 = _复验回路(monkeypatch, "", 有登录项=True)
        assert lp._自动登录尝试(页) is False
        整页 = [u for u in 页.导航]
        assert len(整页) <= ML.登录复验窗口秒 // ML.登录导航冷却秒 + 2, (
            "重启一趟发了 %d 次整页加载（原样是 15+ 次）: %r" % (len(整页), 整页))
        assert len(lp.眼) > len(整页), (
            "导航次数和眼数一样多说明还是每眼导航一次: 眼=%d 导航=%d" % (len(lp.眼), len(整页)))

    def test_只读的那几眼照样能认出登录成功(self, monkeypatch):
        """反向哨兵：不许为了少发请求把"Cookie 在、后来登进去了"这一档也一起漏掉
        （10-20 14:30 主站光加载就 56 秒，慢不等于坏）。"""
        lp, 页, 钟 = _复验回路(monkeypatch, 中性页, 状态序列=["uncertain", "uncertain", "logged_in"],
                              有登录项=True)
        assert lp._自动登录尝试(页) is True, lp.日志
        assert lp._needs_login is False

    def test_登录页上也不许跳去会话页(self, monkeypatch):
        """那一页上有二维码、有填到一半的手机号——和 _wait_for_login 同一条规矩。
        浏览器里又没有登录项，所以这一眼没什么可裁的。"""
        lp, 页, 钟 = _复验回路(monkeypatch, 登录页)
        lp._确认登录墙 = False
        assert lp._自动登录尝试(页) is False
        assert 会话页 not in 页.导航, 页.导航

    def test_页说没登但Cookie说登着时要导航问一次(self, monkeypatch):
        """反向哨兵：不许把"少发请求"做成"Cookie 有效也得他点一下「我已登录」"。
        这种两路证据矛盾的现场只有把会话页加载一次才裁得开，但要裁得克制——
        60 秒内就一次。"""
        lp, 页, 钟 = _复验回路(monkeypatch, 登录页, 状态序列=["logged_in"], 有登录项=True)
        assert lp._自动登录尝试(页) is True, lp.日志
        assert 页.导航 == [会话页], 页.导航

    def test_这一眼该不该导航是唯一一份判据(self):
        """等待循环和启动复验各写一份"要不要导航"，早晚又走偏成一份有冷却一份没有。"""
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = 源[源.index("def _自动登录尝试"):源.index("def _handle_login")]
        assert "这一眼该不该导航(" in 块, "启动复验没接共用判据，就是一眼一次整页加载"
        等待块 = 源[源.index("def _wait_for_login"):源.index("def _settle_url")]
        assert "这一眼该不该导航(" in 等待块, "等待循环那份也要收到同一份函数上"
        assert 块.count("_login_state_now(") <= 1, "不许在循环外面再直接导航读一次"


class 摆登录窗口那一趟不许加载业务页Test:

    def _管理器(self, monkeypatch, headless_launched=True, 恢复页=""):
        class 假标签:
            def __init__(self, url=""):
                self.url = url
                self.导航 = []

            def get(self, u):
                self.导航.append(u)
                self.url = u
                return True

        class 假页:
            def __init__(self):
                self.导航 = []
                self.tab_id = "T0"
                self.process_id = 4321
                self.url = 恢复页

            def get(self, u):
                self.导航.append(u)
                self.url = u
                return True

            def new_tab(self, url=""):
                t = 假标签(url)
                self.新建.append(t)
                return t

        新建 = []

        def 起(**kw):
            p = 假页()
            p.新建 = 新建
            return BL.BrowserInstance(chrome_page=p)

        monkeypatch.setattr(BL, "launch_browser", 起)
        monkeypatch.setattr(BL, "restore_browser_windows", lambda pid, **kw: 1)
        monkeypatch.setattr(BL, "browser_mode",
                            lambda p: {"running": True,
                                       "headless": bool(headless_launched)})
        cfg = type("C", (), {"headless": True, "background": True, "cookie_file": ""})()
        m = BL.BrowserManager(cfg, account_index=0, port=9222)
        m._instance = 起()
        m._新建 = 新建
        return m

    def test_重开成有头以后不许再整页加载搜索页(self, monkeypatch):
        """09:21:15 那一下：close() 之后重建句柄是必要的，"顺手 get 一遍搜索页"不是。
        紧接着还要把同一张页导航去登录页——那一趟搜索页纯粹是白撞一次验证。"""
        m = self._管理器(monkeypatch)
        果 = m.show_login_window(登录页, navigate=False)
        assert 果["relaunched"] is True
        导航了 = m._instance._get_active().导航
        assert m._search_tab is not None, "句柄没重建，下一轮打招呼拿的是旧对象"
        assert BL.BrowserManager.BOSS_SEARCH_URL not in 导航了, (
            "重开完先把搜索页整页加载了一遍，等于又撞一次验证: %r" % (导航了,))

    def test_重开后浏览器自己恢复了验证页就不要再推登录页(self, monkeypatch):
        """同一份用户目录重开，Chrome 会把上次那页带回来。那一页正是他要滑的，
        推去登录页=二维码换一张、刚滑的一半没了；登录页自己也进不去验证。"""
        m = self._管理器(monkeypatch, 恢复页=验证页)
        果 = m.show_login_window(登录页, navigate=False)
        assert 果["relaunched"] is True
        assert m._instance._get_active().导航 == [], (
            "重开完把停在验证页的那一张导航走了: %r" % (m._instance._get_active().导航,))

    def test_重开后是空白页还是得把登录页摆出来(self, monkeypatch):
        """反向哨兵：不许为了少一次加载把他摆到一张什么都没有的窗口前。"""
        m = self._管理器(monkeypatch)
        m.show_login_window(登录页, navigate=False)
        assert m._instance._get_active().导航 == [登录页], "空白页上没把登录入口摆出来"

    def test_等登录时聊天标签页开成空白(self, monkeypatch):
        """09:21:18 那一下 new_tab(会话页)：没登录时那张页必然被踢成
        verify.html——这就是"一个号两张一模一样的验证页"的来路。"""
        m = self._管理器(monkeypatch)
        页 = m.get_chat_page(导航=False)
        assert 页.url == "", "聊天标签页一开就导航会话页，那张页会被踢到安全验证页"
        assert m._新建, "没开新标签页，回复腿后面没页可用"

    def test_空白聊天页在腿真要用的时候必须自己开出去(self, monkeypatch):
        """改了必须生效：不许把回复腿留在一张 about:blank 上，那等于"没有会话"。"""
        m = self._管理器(monkeypatch)
        空白 = m.get_chat_page(导航=False)
        又 = m.get_chat_page()
        assert 又 is 空白, "换了一张页，钉住的句柄和回复腿用的对不上"
        assert 空白.url == 会话页, (
            "回复腿来要页了却没把空白页导航到会话页: %r" % (空白.url,))
        实页 = 空白._get_active()
        assert 实页.导航 == [会话页], 实页.导航
        m.get_chat_page()
        assert len(实页.导航) == 1, (
            "已经在会话页上还反复重载，这就是又造了一轮整页加载: %r" % (实页.导航,))

    def test_已经在boss页上的聊天页不许被反复导航(self, monkeypatch):
        """反向哨兵：懒导航只能兜空白页，不能变成"每次拿页都刷一遍"。

        10-31 起这张页是"先开空白页 → 把这一张的加载模式设成 normal → 再导航"，
        所以开页那一次 `导航` 是**必须有的**（会话页不在这里加载就没页可用），
        判据落在"一共只加载这一次"：`new_tab(会话页)` 那一版是 0 条，
        现在这一版是 1 条，多出来的一条才是反复重载。
        """
        m = self._管理器(monkeypatch)
        页 = m.get_chat_page()
        assert 页.url == 会话页
        m.get_chat_page()
        m.get_chat_page()
        实页 = 页._get_active()
        assert 实页.导航 == [会话页], (
            "会话页加载不止一次——后面再 get 一遍就是白加整页: %r" % (实页.导航,))
        assert len(m._新建) == 1, m._新建

    def test_等登录那条重建引擎的路要传不导航(self):
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = 源[源.index("def _ensure_login_window"):源.index("def _finish_login_window")]
        assert "_init_engines(开页=False)" in 块, (
            "摆登录窗口之后重建引擎还开着导航，就会多出一张验证页副本")


    def test_等登录时重连不许把搜索页会话页加载出去(self):
        """断线重连/内存回收走 `_重建浏览器`：10-21 那三条
        「重开后已接回搜索标签页: …/security.html」就是没登录时重开、又去加载
        搜索页——每加载一张就多在验证页上挂一次。等登录中只钉句柄。"""
        with patch("boss_bot.main_loop.BrowserManager"):
            lp = ML.UnifiedBotLoop(account_index=1)
        lp._log = lambda *a, **k: None
        lp.browser_manager = MagicMock()
        lp.browser_manager.recycle.return_value = {"前": 0.0, "后": 0.0}
        lp.browser_manager.load_cookies.return_value = True
        lp._cookie_file = lambda: "zhipin_cookies.json"
        lp._init_engines = MagicMock()
        lp._needs_login = True
        lp._重建浏览器()
        assert lp.browser_manager.get_search_page.call_args.kwargs == {"导航": False}, \
            lp.browser_manager.get_search_page.call_args
        assert lp._init_engines.call_args.kwargs == {"开页": False}, lp._init_engines.call_args
        lp._needs_login = False
        lp.browser_manager.get_search_page.reset_mock()
        lp._重建浏览器()
        assert lp.browser_manager.get_search_page.call_args.kwargs == {"导航": True}, \
            "登进去了还不敢开页，抓取腿就永远拿着一张登录页"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
