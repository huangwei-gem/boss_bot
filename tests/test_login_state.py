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
                                has_live_auth_cookie, archive_cookie_file)


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
        loop._interruptible_sleep = lambda *a, **k: None
        loop._cookie_file = lambda: "zhipin_cookies.json"
        loop.browser_manager = MagicMock()
        loop.browser_manager.load_cookies.return_value = True
        loop._discard_stale_cookies = MagicMock()
        loop._save_cookies_if_logged_in = MagicMock(return_value=True)
        loop._wait_for_login = MagicMock(return_value=False)
        return loop

    def _urls_touched(self, inst):
        return [c.args[0] for c in inst.get.call_args_list]

    def test_矛盾时先复核而不是立刻推人去登录页(self):
        loop = self._loop()
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        seen = ["uncertain", "logged_in"]
        loop._login_state_now = lambda i: seen.pop(0)
        assert loop._handle_login() is True
        assert not any("/web/user" in u for u in self._urls_touched(inst)), \
            "复核到第二次就登录了，还把人家的页面导航到登录页做什么"
        loop._discard_stale_cookies.assert_not_called()

    def test_复核后仍看不准也不跳登录页(self):
        """跳登录页会把会话页弄脏，回复侧随后就读到登录墙——今天就是这么自绊的"""
        loop = self._loop()
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        loop._login_state_now = lambda i: "uncertain"
        assert loop._handle_login() is False
        assert not any("/web/user" in u for u in self._urls_touched(inst))
        loop._discard_stale_cookies.assert_not_called()
        assert loop._login_reason == "login_uncertain"

    def test_确认登录墙才跳登录页并归档(self):
        loop = self._loop()
        inst = MagicMock()
        loop.browser_manager.get_instance.return_value = inst
        loop._login_state_now = lambda i: "login_wall"
        assert loop._handle_login() is False
        assert any("/web/user" in u for u in self._urls_touched(inst))
        loop._discard_stale_cookies.assert_called_once()


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

    def test_只保留最近几份(self):
        import tempfile
        from boss_bot.browser_launcher import backup_cookie_file, COOKIE_BACKUP_KEEP
        with tempfile.TemporaryDirectory() as td:
            bk = os.path.join(td, "bk")
            for i in range(COOKIE_BACKUP_KEEP + 3):
                target = os.path.join(td, "c.json")
                with io.open(target, "w", encoding="utf-8") as f:
                    json.dump([_auth_cookie(value=str(i))], f)
                backup_cookie_file(target, bk)
            names = sorted(os.listdir(bk))
            self.assertEqual(len(names), COOKIE_BACKUP_KEEP)
            last = json.load(io.open(os.path.join(bk, names[-1]), encoding="utf-8"))
            self.assertEqual(last[0]["value"], str(COOKIE_BACKUP_KEEP + 2))

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
