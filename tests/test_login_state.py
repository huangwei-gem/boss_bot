# -*- coding: utf-8 -*-
"""登录态判定与 Cookie 文件保全。

起因（2026-09-29 演练并发跑出来的）：主账号被启动时的登录判定误报"Cookie 已过期"，
代码当场把 zhipin_cookies.json **删了**；随后 _wait_for_login 只等 2 秒就误报
"检测到登录成功"，并把登录页那一份 cookie 写回文件顶掉好会话；回复侧从此每 30 秒
打一次"登录态失效"并再删一次，一路刷到我手动停。三个洞叠在一起，代价是用户的
登录态被弄丢且不可恢复。

这里锁四条：判据要等页面稳定且两路一致；失效只归档不删除；没确认登录不许覆盖
Cookie 文件；回复侧连判 3 次就停下来等人工，不再空刷。
"""

import io
import json
import os
import re
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import boss_bot.unified_config as UC
from boss_bot.main_loop import (UnifiedBotLoop, login_state_of,
                                has_live_auth_cookie, archive_cookie_file,
                                BOSS_LOGIN_URL, 登录复验窗口秒, 登录复验间隔秒,
                                登录导航冷却秒)


def _auth_cookie(**over):
    c = {"name": "zp_at", "value": "x", "expires": time.time() + 86400, "domain": ".zhipin.com"}
    c.update(over)
    return c


class LoginStateOfTest(unittest.TestCase):
    """页面 URL 和浏览器里的登录 cookie 是两路证据，一致才定论。"""

    def test_会话页加登录cookie才算已登录(self):
        self.assertEqual(login_state_of("https://www.zhipin.com/web/geek/chat", True),
                         "logged_in")

    def test_登录页且没有登录cookie才是登录墙(self):
        self.assertEqual(login_state_of("https://www.zhipin.com/web/user/?ka=header-login",
                                        False), "login_wall")

    def test_掉到登录页但cookie齐全是矛盾现场不能定罪(self):
        """今天就是把这种中间态判成"过期"，顺手删了用户 Cookie"""
        self.assertEqual(login_state_of("https://www.zhipin.com/web/user/", True),
                         "uncertain")

    def test_停在会话页却没有登录cookie也是矛盾(self):
        self.assertEqual(login_state_of("https://www.zhipin.com/web/geek/chat", False),
                         "uncertain")

    def test_岗位详情页不构成登录证据(self):
        """详情页游客也能看，拿它判登录必然误判"""
        for url in ("https://www.zhipin.com/job_detail/abc.html",
                    "https://www.zhipin.com/web/geek/job-recommend"):
            self.assertEqual(login_state_of(url, True), "uncertain")

    def test_空url不算登录墙(self):
        self.assertEqual(login_state_of("", False), "uncertain")

    def test_passport页算登录墙(self):
        self.assertEqual(login_state_of("https://passport.zhipin.com/login", False),
                         "login_wall")


class HasLiveAuthCookieTest(unittest.TestCase):
    def test_有未过期的登录项(self):
        self.assertTrue(has_live_auth_cookie([_auth_cookie()]))

    def test_过期了就不算(self):
        self.assertFalse(has_live_auth_cookie([_auth_cookie(expires=time.time() - 10)]))

    def test_session_cookie没有过期时间算有效(self):
        self.assertTrue(has_live_auth_cookie([_auth_cookie(expires=-1)]))

    def test_只有无关cookie不算(self):
        # bst 在 BOSS_AUTH_COOKIES 里，是登录项；拿它当"无关项"会测出假绿
        self.assertFalse(has_live_auth_cookie([{"name": "abtest", "value": "1", "expires": -1}]))

    def test_脏数据不炸(self):
        self.assertFalse(has_live_auth_cookie(None))
        self.assertFalse(has_live_auth_cookie(["x", {"name": "zp_at", "expires": "abc"}]))


class ArchiveCookieFileTest(unittest.TestCase):
    """失效 Cookie 只归档，不 unlink——那是用户唯一能拿去人工排查的东西。"""

    def test_归档后原路径空但内容找得回(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "zhipin_cookies.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump([_auth_cookie()], f)
            dest = archive_cookie_file(src, os.path.join(td, "stale"), "主账号")
            self.assertFalse(os.path.exists(src), "原路径要空掉，下次才会走完整登录")
            self.assertTrue(os.path.exists(dest))
            self.assertIn("主账号", os.path.basename(dest))
            self.assertEqual(json.load(open(dest, encoding="utf-8"))[0]["name"], "zp_at")

    def test_文件本来就不在时不报错(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(archive_cookie_file(os.path.join(td, "none.json"), td, "账号2"), "")

    def test_同一秒归档两次不覆盖(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ad = os.path.join(td, "stale")
            for i in range(2):
                src = os.path.join(td, "c.json")
                with open(src, "w", encoding="utf-8") as f:
                    json.dump([_auth_cookie(value=str(i))], f)
                dest = archive_cookie_file(src, ad, "主账号")
                self.assertTrue(dest and os.path.exists(dest))
            self.assertEqual(len(os.listdir(ad)), 2)


class DiscardStaleCookiesTest(unittest.TestCase):
    def _loop(self, clear_flag):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop.config.login.clear_cookies_on_failure = clear_flag
        loop._log = lambda *a, **k: None
        return loop

    def test_归档而不是删除且带账号标签(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "zhipin_cookies.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump([_auth_cookie()], f)
            loop = self._loop(True)
            loop._cookie_file = lambda: src
            with patch.object(type(loop), "_stale_cookie_dir", lambda self: os.path.join(td, "stale")):
                loop._discard_stale_cookies("启动时 Cookie 验证失败")
            archived = os.listdir(os.path.join(td, "stale"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(json.load(open(os.path.join(td, "stale", archived[0]),
                                           encoding="utf-8"))[0]["name"], "zp_at")

    def test_开关关掉时一个字节都不动(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "zhipin_cookies.json")
            with open(src, "w", encoding="utf-8") as f:
                json.dump([_auth_cookie()], f)
            before = open(src, encoding="utf-8").read()
            loop = self._loop(False)
            loop._cookie_file = lambda: src
            loop._discard_stale_cookies("回复侧登录态失效")
            self.assertEqual(open(src, encoding="utf-8").read(), before)

    def test_源码里不许再有unlink(self):
        import inspect
        from boss_bot import main_loop as ml
        src = inspect.getsource(ml.UnifiedBotLoop._discard_stale_cookies)
        self.assertNotIn("unlink()", src)
        self.assertNotIn("os.remove", src)
        self.assertIn("archive_cookie_file", src)


class SaveCookiesGateTest(unittest.TestCase):
    """没确认真登录就不许把浏览器里的 cookie 写回文件——今天顶掉的是好会话。"""

    def _loop(self, cookies):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda *a, **k: None
        inst = MagicMock()
        inst._get_all_cookies.return_value = cookies
        loop.browser_manager = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        loop.browser_manager.save_cookies = MagicMock(return_value=True)
        loop._cookie_file = lambda: "/tmp/zhipin_cookies.json"
        return loop

    def test_登录页那份没有可用登录项所以不落盘(self):
        loop = self._loop([{"name": "abtest", "value": "1", "expires": -1},
                           {"name": "zp_at", "value": "", "expires": time.time() + 5},
                           {"name": "wt2", "value": "x", "expires": time.time() - 5}])
        self.assertFalse(loop._save_cookies_if_logged_in())
        loop.browser_manager.save_cookies.assert_not_called()

    def test_确认登录后才落盘(self):
        loop = self._loop([_auth_cookie()])
        self.assertTrue(loop._save_cookies_if_logged_in())
        loop.browser_manager.save_cookies.assert_called_once_with("/tmp/zhipin_cookies.json")


class ReplyLoginStreakTest(unittest.TestCase):
    """回复侧连判 3 次登录失效就该停下等人工，不再每 30 秒空刷到天亮。"""

    def _loop(self):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda *a, **k: None
        loop._cookie_file = lambda: ""
        loop._discard_stale_cookies = lambda reason: None
        loop._stop_event = MagicMock()
        loop._running = True
        loop._login_fail_streak = 0
        return loop

    def test_头两次继续等第三次停下(self):
        loop = self._loop()
        self.assertFalse(loop._reply_login_lost_once())
        self.assertFalse(loop._reply_login_lost_once())
        self.assertTrue(loop._reply_login_lost_once())
        self.assertTrue(loop._needs_login)

    def test_中间恢复过一次就清零(self):
        loop = self._loop()
        loop._reply_login_lost_once()
        loop._reply_login_lost_once()
        loop._reply_login_ok()
        self.assertFalse(loop._reply_login_lost_once())

    def test_停下时把原因写清楚好让人看见(self):
        loop = self._loop()
        msgs = []
        loop._log = lambda lvl, m="": msgs.append((lvl, m))
        for _ in range(3):
            loop._reply_login_lost_once()
        self.assertTrue(any("等人工登录" in m for _, m in msgs),
                        f"没有一条说清要人工登录: {msgs}")


class UncertainHandlingTest(unittest.TestCase):
    """23:01 并发实测：主号明明登录着，却白等 80 秒并跳了一次登录页；
    回复侧第一眼判"失效"就把刚存的 Cookie 归档走了。两路矛盾时不能这么处置。"""

    def _loop(self):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda *a, **k: None
        loop._cookie_file = lambda: "zhipin_cookies.json"
        loop.browser_manager = MagicMock()
        loop.browser_manager.load_cookies.return_value = True
        loop._discard_stale_cookies = MagicMock()
        loop._save_cookies_if_logged_in = MagicMock(return_value=True)
        loop._wait_for_login = MagicMock(return_value=False)
        # 复验的"一眼"有两条腿：只读的 `_login_state_read` 和导航之后读的
        # `_login_state_now`。2026-10-22 起启动复验以只读为主（导航 60 秒一次），
        # 所以这里两条腿共用一个队列——测试给的是"这一串眼依次读到什么"，
        # 不管中间是哪条腿读的；`_settle_url` 打桩成中性页，免得真等 8 秒×18 眼。
        loop.眼队列 = []
        loop.读眼 = []
        loop.导航眼 = []

        def _取():
            loop.读眼.append(1)
            return loop.眼队列.pop(0) if loop.眼队列 else "uncertain"

        loop._login_state_read = lambda inst, 早收=True: _取()
        loop._login_state_now = lambda inst: loop.导航眼.append(inst) or _取()
        loop._settle_url = lambda inst, timeout=None, 早收=True: ""
        loop._has_live_auth_cookie = lambda inst: False
        # 登录窗口这一层现在由 show_login_window 管（无头时要重开成有头），
        # 这里记成"摆了几次窗口、各自动没动页面"，下面按这个断言
        loop.摆窗口 = []

        def _窗口(url, navigate=True, **kw):
            loop.摆窗口.append((url, navigate))
            return {"relaunched": False, "windows": 1,
                    "url_shown": url if navigate else "", "error": ""}

        loop.browser_manager.show_login_window.side_effect = _窗口
        return loop

    def _urls_touched(self, inst):
        return [c.args[0] for c in inst.get.call_args_list]

    def test_矛盾时先复核而不是立刻推人去登录页(self):
        loop = self._loop()
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        seen = ["uncertain", "logged_in"]
        loop.眼队列 = seen
        assert loop._handle_login() is True
        assert not any("/web/user" in u for u in self._urls_touched(inst)), \
            "复核到第二次就登录了，还把人家的页面导航到登录页做什么"
        loop._discard_stale_cookies.assert_not_called()

    def test_复核后仍看不准也不跳登录页(self):
        """跳登录页会把会话页弄脏，回复侧随后就读到登录墙——今天就是这么自绊的"""
        loop = self._loop()
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        assert loop._handle_login() is False
        assert not any(n for _, n in loop.摆窗口), \
            "看不准时可以把窗口摆出来让人自己看，但不许把页面导航走"
        loop._discard_stale_cookies.assert_not_called()
        assert loop._login_reason == "login_uncertain"

    def test_确认登录墙才跳登录页并归档(self):
        loop = self._loop()
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        loop.眼队列 = ["login_wall"]
        assert loop._handle_login() is False
        assert any("/web/user" in u and n for u, n in loop.摆窗口), \
            f"确认是登录墙，却没把登录页摆出来: {loop.摆窗口}"
        loop._discard_stale_cookies.assert_called_once()

    def test_确认登录墙时窗口一定摆出来(self):
        """面板那句"请在浏览器中登录"在无头形态下做不到——必须先有窗口"""
        loop = self._loop()
        loop.browser_manager.get_instance.return_value = MagicMock()
        loop.眼队列 = ["login_wall"]
        loop._handle_login()
        assert loop.browser_manager.show_login_window.called


class 自动登录要自己撑住Test(UncertainHandlingTest):
    """他要的是"Cookie 在就自己登进去"，不是每次都伸手点「我已登录」。

    10-20 14:30 实测：主账号光访问主站就用了 56 秒，会话页那一眼很容易读到
    中间态；旧代码只复核一次（等 4 秒）就判"看不准 → 要人工登录"，于是界面摆出
    登录弹窗，他看到的就是"未登录、点我已登录也没用"。慢不等于坏，
    所以复验要在一个有界窗口里多次读，读出来就自己往下走。
    """

    def test_慢几眼之后登进去了就不许再喊人工(self):
        loop = self._loop()
        loop._interruptible_sleep = lambda s: None
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        seen = ["uncertain", "uncertain", "uncertain", "logged_in"]
        loop.眼队列 = seen
        assert loop._handle_login() is True, \
            "复验窗口里明明后来读到了登录态，却还是判成要人工登录"
        assert loop._needs_login is False, "自动登录成功了还留着要人工登录的标记"
        loop.browser_manager.show_login_window.assert_not_called()
        loop._discard_stale_cookies.assert_not_called()

    def test_复验有上界且不导航人正在用的页面(self):
        """看不准要收敛：读满窗口就停手，页面一张都不许拽走。

        上界要按次数算，不能只按墙钟——以前只写"还剩几秒"，把真瞌睡打桩之后
        这个循环会在 90 秒里空转几百万次去反复读页面（测试跑一条要 90 秒就是
        它的现场证据），线上要是哪个线程把时间卡住，它就是拿浏览器做压力测试。
        """
        loop = self._loop()
        睡过 = []
        loop._interruptible_sleep = lambda s: 睡过.append(s)
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        assert loop._handle_login() is False
        上限 = int(登录复验窗口秒 // 登录复验间隔秒)
        assert len(loop.读眼) <= 上限, \
            f"复验没有次数上界：读了 {len(loop.读眼)} 眼（窗口 {登录复验窗口秒}s / 间隔 {登录复验间隔秒}s）"
        assert len(睡过) <= 上限, f"复验没有上界，睡了 {len(睡过)} 轮"
        assert len(loop.导航眼) <= 2, \
            f"复验发了 {len(loop.导航眼)} 次整页导航：一眼一次就是 09:19~09:21 那 15 次（窗口 {登录复验窗口秒}s / 冷却 {登录导航冷却秒}s）"
        assert inst.get.call_count <= 2, \
            f"启动复验往页面上发了 {inst.get.call_count} 次整页加载: %r" % (self._urls_touched(inst),)
        assert loop._needs_login is True
        assert loop._login_reason == "login_uncertain"
        assert not any(n for _, n in loop.摆窗口), "看不准不该把页面导航走"
        loop._discard_stale_cookies.assert_not_called()

    def test_复验途中被叫停不许摆登录窗口(self):
        """停止信号不等于掉登录：这时候喊"请在浏览器中登录"就是狼来了"""
        loop = self._loop()
        loop._interruptible_sleep = lambda s: None
        loop.browser_manager.get_instance.return_value = MagicMock()
        loop._stop_event.set()
        assert loop._handle_login() is False
        assert loop._needs_login is False, "只是被叫停，却标成了要人工登录"
        loop.browser_manager.show_login_window.assert_not_called()

    def test_Cookie文件不在但浏览器还登着要算登进去并把会话补回盘(self):
        """10-20 号2 一天要人工登三次的那条根因：会话在浏览器用户目录里，
        文件却不在了（被判错一次就归档走）。只看文件就喊人工，等于把还登着的
        会话白白扔掉，然后逼他去点那一一下「我已登录」。"""
        loop = self._loop()
        loop._cookie_file = lambda: ""
        loop._save_cookies_if_logged_in = MagicMock(return_value=True)
        loop.browser_manager.get_instance.return_value = MagicMock()
        loop.眼队列 = ["logged_in"]
        assert loop._handle_login() is True, "浏览器里明明登着，却因为 Cookie 文件不在就喊人工"
        loop._save_cookies_if_logged_in.assert_called_once_with(
            loop.browser_manager.get_instance.return_value)
        loop.browser_manager.show_login_window.assert_not_called()
        loop._discard_stale_cookies.assert_not_called()
        assert loop._login_reason == ""

    def test_补不回盘也算登进去不许谎称要人工(self):
        """浏览器是登录态的第一现场：落盘失败只说明下一轮还要再看一眼，
        不能把这一轮已经登着的号推去等人"""
        loop = self._loop()
        loop._cookie_file = lambda: ""
        loop._save_cookies_if_logged_in = MagicMock(return_value=False)
        loop.browser_manager.get_instance.return_value = MagicMock()
        loop.眼队列 = ["logged_in"]
        assert loop._handle_login() is True
        assert loop._needs_login is False

    def test_Cookie注不进去且页面也是登录墙才要人工(self):
        loop = self._loop()
        loop.browser_manager.load_cookies.return_value = False
        loop.browser_manager.get_instance.return_value = MagicMock()
        loop.眼队列 = ["login_wall"]
        assert loop._handle_login() is False
        assert loop._login_reason == "cookie_expired", loop._login_reason
        assert loop._needs_login is True
        loop._discard_stale_cookies.assert_called_once()
        assert (BOSS_LOGIN_URL, True) in loop.摆窗口, \
            f"确认是墙却没导航到登录页: {loop.摆窗口}"

    def test_全新账号要说没登录过而不是过期(self):
        """文件根本不存在 + 页面是登录墙：这是第一次用，不是会话掉了。
        界面这两句话给人的动作不一样，说错了他会去找"过期的 Cookie"。"""
        loop = self._loop()
        loop._cookie_file = lambda: "zhipin_cookies_这个账号还没登录过.json"
        loop.browser_manager.load_cookies.return_value = False
        loop.browser_manager.get_instance.return_value = MagicMock()
        loop.眼队列 = ["login_wall"]
        assert loop._handle_login() is False
        assert loop._login_reason == "no_cookie", loop._login_reason
        assert (BOSS_LOGIN_URL, True) in loop.摆窗口, "新账号更要把登录页摆出来让人登"

    def test_确认是登录墙就早点收工不必撑满窗口(self):
        """坏消息随到随收：撑窗口只会让他多等一分钟才看到真正要他做的事"""
        loop = self._loop()
        睡过 = []
        loop._interruptible_sleep = lambda s: 睡过.append(s)
        loop.browser_manager.get_instance.return_value = MagicMock()
        loop.眼队列 = ["login_wall"]
        assert loop._handle_login() is False
        assert len(睡过) <= 2, f"已经确认登录墙还在原地等: 等了 {len(睡过)} 次"

    def test_自动登录进行中界面要报在验登录而不是卡住(self):
        """phase 里没有这一档，界面就只能拿 starting/未登录 糊他"""
        loop = self._loop()
        loop._running = True
        loop._logged_in = False
        loop._needs_login = False
        loop._checking_login = True
        assert loop.phase == "checking_login", loop.phase
        状态 = loop.get_status()
        assert 状态["needs_login"] is False
        assert 状态["logged_in"] is False


class 界面不许有永远报喜的登录检查Test(unittest.TestCase):
    """socket 那条 check_login 以前直接 emit success:True——没人调、调了必绿。"""

    def test_面板里没有假成功的登录检查(self):
        import io
        import os
        根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        文本 = io.open(os.path.join(根, "flask-version", "app.py"),
                       encoding="utf-8").read()
        assert 'emit("login_result", {"success": True})' not in 文本, \
            "check_login 又变回永远报喜了——这条路径要么真判，要么删掉"
        页面 = io.open(os.path.join(根, "flask-version", "templates", "index.html"),
                       encoding="utf-8").read()
        assert "addLog('ERROR', '未登录')" not in 页面, \
            "前端还留着给假成功事件印'未登录'的死监听，出问题时它会说反话"



class 自动登录那一档要真画出来Test(unittest.TestCase):
    """checking_login 是后端新加的一档。前端 accDotClass 不认它的话，函数会一路
    走到最后的 `return 'running'`——自动登录进行中的号被画成绿点"运行"，正是他
    最恨的那种假绿。这一族改动最容易漏的就是"后端加了档、前端当没看见"，
    所以门要同时盯住分支、颜色、字和悬浮说明四端。
    """

    def setUp(self):
        根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.页面 = io.open(os.path.join(根, "flask-version", "templates",
                                        "index.html"), encoding="utf-8").read()

    def _accDotClass体(self, 页面=None):
        页面 = 页面 if 页面 is not None else self.页面
        起 = 页面.index("function accDotClass(st)")
        止 = 页面.index("function accTitle", 起)
        return 页面[起:止]

    def _自动登录类名(self, 页面=None):
        体 = self._accDotClass体(页面)
        匹 = re.search(r"st\.phase === 'checking_login'\)\s*return '([a-z_]+)';", 体)
        return 匹.group(1) if 匹 else ""

    def test_分支存在且不许落到绿点(self):
        类名 = self._自动登录类名()
        assert 类名, ("accDotClass 里没有 checking_login 分支：后端这一档会被画成 "
                      "函数末尾那个 return 'running'（绿点=运行），自动登录中看着像跑好了")
        assert 类名 != "running", f"自动登录中不许是绿点，拿到了 {类名}"

    def test_类名要有颜色有字有悬浮说明(self):
        类名 = self._自动登录类名()
        assert 类名
        assert f".acc-status-dot.{类名}{{" in self.页面, \
            f"CSS 里没有 .acc-status-dot.{类名}，点会退回默认（没底色），看不出一个号在干什么"
        字典 = re.search(r"var ACC_STATE_WORD = \{(.*?)\};", self.页面, re.S).group(1)
        assert re.search(rf"\b{类名}\s*:", 字典), \
            f"ACC_STATE_WORD 里没有 {类名}，账号条上的字会是空白"
        标签 = re.search(r"var PHASE_LABEL = \{(.*?)\};", self.页面, re.S).group(1)
        assert re.search(r"\bchecking_login\s*:\s*'([^']+)'", 标签), \
            "PHASE_LABEL 缺 checking_login，悬浮标题会说成'未启动'"

    def test_漏掉分支这条门真的会红(self):
        """变异哨兵：把分支那一行删掉，上面两条判据必须当场抓出来"""
        体 = self._accDotClass体()
        删干净 = re.sub(r"[^\n]*st\.phase === 'checking_login'[^\n]*\n", "", 体, count=1)
        assert 删干净 != 体, "变异没生效：分支行根本匹配不到，这条门是假的"
        页面 = self.页面.replace(体, 删干净, 1)
        assert self._自动登录类名(页面) == "", "删掉分支后仍然读得出类名，门形同虚设"


class ReplyLoginGuardTest(unittest.TestCase):
    """回复侧判失效之前，先自己复核一次两路证据。"""

    def _loop(self):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda lvl="", m="": None
        loop._stop_event = MagicMock()
        loop._discard_stale_cookies = MagicMock()
        loop._login_fail_streak = 0
        loop._needs_login = False
        return loop

    def test_复核仍矛盾就不动Cookie也不计数(self):
        loop = self._loop()
        loop._login_state_read = lambda i: "uncertain"
        got = loop._reply_login_guard(MagicMock())
        self.assertEqual(got, "waiting")
        loop._discard_stale_cookies.assert_not_called()
        self.assertEqual(loop._login_fail_streak, 0)

    def test_复核确认登录墙才归档并计数(self):
        loop = self._loop()
        loop._login_state_read = lambda i: "login_wall"
        got = loop._reply_login_guard(MagicMock())
        self.assertEqual(got, "waiting")
        loop._discard_stale_cookies.assert_called_once()
        self.assertEqual(loop._login_fail_streak, 1)

    def test_连续三次确认才停这个号(self):
        loop = self._loop()
        loop._login_state_read = lambda i: "login_wall"
        self.assertEqual(loop._reply_login_guard(MagicMock()), "waiting")
        self.assertEqual(loop._reply_login_guard(MagicMock()), "waiting")
        self.assertEqual(loop._reply_login_guard(MagicMock()), "stop")
        self.assertTrue(loop._needs_login)
        loop._discard_stale_cookies.assert_called_once()   # 只在第一次动文件


class GreetLoginGuardTest(unittest.TestCase):
    """打招呼侧的健康检查同样会一眼误判（它读的是打招呼线程正在跳转的页面）。

    23:01 之后主号的 Cookie 就是被"运行中检测到登录态失效"这一眼归档走的。
    """

    def _loop(self):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda lvl="", m="": None
        loop._discard_stale_cookies = MagicMock()
        loop._login_fail_streak = 0
        loop._needs_login = False
        return loop

    def test_复核不是登录墙就不许归档(self):
        loop = self._loop()
        loop._login_state_read = lambda i: "uncertain"
        self.assertFalse(loop._recheck_before_archive(MagicMock(), "打招呼侧"))
        loop._discard_stale_cookies.assert_not_called()

    def test_复核确认登录墙才放行(self):
        loop = self._loop()
        loop._login_state_read = lambda i: "login_wall"
        self.assertTrue(loop._recheck_before_archive(MagicMock(), "打招呼侧"))

    def test_回复侧守卫走同一个复核(self):
        loop = self._loop()
        loop._login_state_read = lambda i: "logged_in"
        self.assertEqual(loop._reply_login_guard(MagicMock()), "waiting")
        loop._discard_stale_cookies.assert_not_called()

    def test_打招呼循环要先复核再动Cookie(self):
        import inspect
        from boss_bot import main_loop as ml
        src = inspect.getsource(ml.UnifiedBotLoop._greet_loop)
        i = src.index('health == "need_login"')
        seg = src[i:i + 600]
        self.assertIn("_recheck_before_archive", seg)
        self.assertLess(seg.index("_recheck_before_archive"),
                        seg.index("_discard_stale_cookies"),
                        "复核必须在归档之前，否则又是看一眼就搬文件")


class ToolProbeUrlTest(unittest.TestCase):
    """体检脚本自己也不能把登录页报成已登录（今天就是这么漏的）。"""

    def _fn(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import two_account_login_check as t
        return t.logged_in_from_url

    def test_登录页不算已登录(self):
        f = self._fn()
        for url in ("https://www.zhipin.com/web/user/?ka=header-login",
                    "https://login.zhipin.com/",
                    "https://passport.zhipin.com/login"):
            self.assertFalse(f(url), url)

    def test_会话页算已登录(self):
        f = self._fn()
        self.assertTrue(f("https://www.zhipin.com/web/geek/chat"))
        self.assertTrue(f("https://www.zhipin.com/web/geek/chat?_security_check=0"))

    def test_空url不算(self):
        self.assertFalse(self._fn()(""))


class CookieBackupTest(unittest.TestCase):
    """最后一道保险：Cookie 文件不管被覆盖还是被"删除"，旧内容都得还在。"""

    def test_备份是复制且不动原文件(self):
        import tempfile
        from boss_bot.browser_launcher import backup_cookie_file
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies.json")
            with io.open(target, "w", encoding="utf-8") as f:
                json.dump([_auth_cookie(value="旧会话")], f)
            got = backup_cookie_file(target, os.path.join(td, "bk"))
            self.assertTrue(got and os.path.exists(got))
            self.assertEqual(json.load(io.open(got, encoding="utf-8"))[0]["value"], "旧会话")
            self.assertTrue(os.path.exists(target), "备份不能把原文件搬走")

    def test_超出份数只挪走不删(self):
        """10-23 定的口径：备份轮换=挪进 `bk/kept/`，一条都不许消失。

        这一条原来断的是"目录里只剩 5 个文件"——那等于把"删用户唯一登录凭据"钉成规矩；
        实际后果是 `data/cookie_backups/` 里 git 跟踪的 7 份旧备份已经没了。
        """
        import tempfile
        from boss_bot.browser_launcher import backup_cookie_file, COOKIE_BACKUP_KEEP
        with tempfile.TemporaryDirectory() as td:
            bk = os.path.join(td, "bk")
            for i in range(COOKIE_BACKUP_KEEP + 3):
                target = os.path.join(td, "c.json")
                with io.open(target, "w", encoding="utf-8") as f:
                    json.dump([_auth_cookie(value=str(i))], f)
                backup_cookie_file(target, bk)
            热 = sorted(n for n in os.listdir(bk) if n.endswith(".json"))
            留档 = sorted(os.listdir(os.path.join(bk, "kept")))
            self.assertEqual(len(热), COOKIE_BACKUP_KEEP)
            self.assertEqual(len(热) + len(留档), COOKIE_BACKUP_KEEP + 3, "有备份不见了")
            last = json.load(io.open(os.path.join(bk, 热[-1]), encoding="utf-8"))
            self.assertEqual(last[0]["value"], str(COOKIE_BACKUP_KEEP + 2))
            # 挪走的那几份要还能读回来（这才是"当时里面到底是什么"的用途）
            最早 = json.load(io.open(os.path.join(bk, "kept", 留档[0]), encoding="utf-8"))
            self.assertEqual(最早[0]["value"], "0")

    def test_主账号存盘不许动账号1的备份(self):
        """反向哨兵：备份按前缀 glob 会把 `zhipin_cookies_1_*` 也算进主账号的名下，
        于是主账号每存一次盘，账号1 就少一份备份。改按名字形状收口之后要盯住这条。"""
        import tempfile
        from boss_bot.browser_launcher import backup_cookie_file, COOKIE_BACKUP_KEEP
        with tempfile.TemporaryDirectory() as td:
            bk = os.path.join(td, "bk")
            os.makedirs(bk)
            别人 = os.path.join(bk, "zhipin_cookies_1_20261019_140120_0076.json")
            with io.open(别人, "w", encoding="utf-8") as f:
                json.dump([_auth_cookie(value="账号1的会话")], f)
            for i in range(COOKIE_BACKUP_KEEP + 2):
                target = os.path.join(td, "zhipin_cookies.json")
                with io.open(target, "w", encoding="utf-8") as f:
                    json.dump([_auth_cookie(value="主账号%d" % i)], f)
                backup_cookie_file(target, bk)
            self.assertTrue(os.path.exists(别人), "账号1 的备份被动过了")
            self.assertNotIn(os.path.basename(别人),
                             os.listdir(os.path.join(bk, "kept")) if os.path.isdir(
                                 os.path.join(bk, "kept")) else [])

    def test_save_cookies落盘前先备份(self):
        """覆盖写是今天真正丢会话的那一步，必须留底"""
        import tempfile
        from unittest.mock import patch as _p
        from boss_bot.browser_launcher import BrowserInstance
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies.json")
            with io.open(target, "w", encoding="utf-8") as f:
                json.dump([_auth_cookie(value="上一轮的会话")], f)
            inst = BrowserInstance()
            with _p.object(BrowserInstance, "_get_all_cookies",
                           return_value=[_auth_cookie(value="这一轮读到的")]), \
                 _p("boss_bot.browser_launcher._cookie_backup_dir",
                    lambda: os.path.join(td, "bk")):
                inst.save_cookies(target)
            now = json.load(io.open(target, encoding="utf-8"))
            self.assertEqual(now[0]["value"], "这一轮读到的")
            kept = os.listdir(os.path.join(td, "bk"))
            self.assertEqual(len(kept), 1)
            self.assertEqual(json.load(io.open(os.path.join(td, "bk", kept[0]),
                                               encoding="utf-8"))[0]["value"], "上一轮的会话")

    def test_界面删除按钮也只归档不删除(self):
        src = io.open('flask-version/app.py', encoding='utf-8').read()
        i = src.index('def api_cookies_delete')
        seg = src[i:i + 1400]
        self.assertNotIn("cookie_path.unlink()", seg)
        self.assertIn("archive_cookie_file", seg)


if __name__ == "__main__":
    unittest.main()


class 空消息异常要看得出类型(unittest.TestCase):
    """账号2 从 11:52 起每 15 秒打一条「访问会话页异常: 」——冒号后面是空的。

    DrissionPage 有些异常 str() 就是空串，只打 `{e}` 等于什么都没记：看不出是标签页
    没了、导航超时还是连接断了，只能干看着它把整个号判成"要人工登录"而停住不动。
    """

    def test_异常消息为空也要带上类型名(self):
        from boss_bot.main_loop import UnifiedBotLoop

        class _空消息异常(Exception):
            def __str__(self):
                return ""

        class _Inst:
            def get(self, url):
                raise _空消息异常()

        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp._login_state_read = lambda inst, 早收=True: "uncertain"
        logs = []
        lp._log = lambda *a, **k: logs.append(a[-1])

        self.assertEqual("uncertain", UnifiedBotLoop._login_state_now(lp, _Inst()))
        打的 = " ".join(str(x) for x in logs)
        self.assertIn("_空消息异常", 打的, f"类型名没打出来，等于没有线索：{打的!r}")
