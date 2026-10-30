# -*- coding: utf-8 -*-
"""安全验证（BOSS「当前 IP 地址可能存在异常访问行为」）这一族的四条闸。

起因（2026-10-20 用户截图 + 原话）：
「我在安全验证就不要刷新」「你刷新太频繁了，我根本就登录不了」
「你要看一下为什么会触发，要避免，以及要识别，来让我去过安全验证」。

当天 13:52~14:00 实测：等登录那 5 秒一轮的导航把人正在滑的验证页整页掀掉，五趟
都没登进去。10-21 复查又确认另一头：健康检查只读自己那一张页，9222/9223 各有一张
岗位页停在 verify.html 时，日志里一整天是 1057 条「会话页判据=ok」对 1 条 captcha——
验证页在别的标签上等着，机器当它不存在。

这里锁四件事：
1. 判据要遍历全部标签页（一张都不许漏，漏了就没人提示）；
2. 任何一条腿都不许把验证页/登录页导航走（go_to_chat 也要守）；
3. 连续打开岗位详情页要有最小间隔（8 天日志里被甩验证页的 111/112 处，上一条动作
   都是打开详情页；两天 381 次搜索页整页加载只 1 处，所以节流钉在详情页不是搜索页）；
4. 验证刚过完那一阵要冷却，别立刻又以同样的节奏去撞。
"""

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
from boss_bot.browser_launcher import 挑验证页, 是风控验证页
from boss_bot.greet_engine import 打开要等秒
from boss_bot.main_loop import UnifiedBotLoop


VERIFY_URL = "https://www.zhipin.com/web/passport/zp/verify.html?backUrl=xxx"


def _标签(id_, url, type_="page", title="安全验证"):
    return {"id": id_, "url": url, "type": type_, "title": title}


class 全标签页挑验证页Test(unittest.TestCase):
    """/json 那份清单里哪些是"要人去滑"的那一页——判据只能有一份。"""

    def test_只挑出验证页(self):
        清单 = [_标签("1", VERIFY_URL),
                _标签("2", "https://www.zhipin.com/web/geek/chat"),
                _标签("3", "https://www.zhipin.com/web/user/?ka=header-login"),
                _标签("4", "https://www.zhipin.com/web/geek/job-recommend")]
        中 = 挑验证页(清单)
        self.assertEqual([t["id"] for t in 中], ["1"])
        # 挑出来的那一条，单独用判据 also 得说是验证页——两者不许各说一套
        self.assertTrue(是风控验证页(中[0]["url"]))

    def test_非page类型不算标签页(self):
        """socket-worker 的 URL 也带 zhipin.com，把它当验证页提示就是狼来了。"""
        清单 = [_标签("w", VERIFY_URL, type_="service_worker"),
                _标签("s", VERIFY_URL, type_="shared_worker")]
        self.assertEqual(挑验证页(清单), [])

    def test_没有验证页时返回空而不是None(self):
        self.assertEqual(挑验证页([_标签("1", "https://www.zhipin.com/web/geek/chat")]), [])
        self.assertEqual(挑验证页(None), [])
        self.assertEqual(挑验证页([]), [])

    def test_判据只写一处(self):
        """补一类验证页地址只能改 是风控验证页，挑验证页 里不许再抄一份关键词。"""
        源 = open(os.path.join(ROOT, "boss_bot", "browser_launcher.py"),
                  encoding="utf-8").read()
        i = 源.index("def 挑验证页")
        j = 源.index("\ndef ", i + 10)
        全 = 源[i:j]
        # 只看代码：注释里写"这些关键词别抄"不算抄
        函数体 = 全.split('"""')[-1]
        self.assertIn("是风控验证页", 函数体)
        for 抄的关键词 in ("verify.html", "security.html", "passport"):
            self.assertNotIn(抄的关键词, 函数体,
                             "验证页的 URL 判据住在 是风控验证页 里，这里抄一份就会走偏")


class 健康检查要遍历全部标签Test(unittest.TestCase):
    """验证页常常开在岗位页那张 _blank 标签上，不在任何一条腿盯着的那张页里。"""

    def _loop(self, 验中=()):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop.browser_manager = MagicMock()
        loop.browser_manager.验证页标签.return_value = list(验中)
        loop._log = lambda *a, **k: None
        loop._health_why = ""
        return loop

    def test_别的标签上有验证页也要判成captcha(self):
        loop = self._loop([_标签("9", VERIFY_URL)])
        inst = MagicMock()
        inst.url = "https://www.zhipin.com/web/geek/chat"
        inst.run_js.return_value = {"inputs": 0, "text": ""}
        loop.browser_manager.get_instance.return_value = inst
        loop.browser_manager.get_search_page.return_value = inst
        loop._chat_handler = MagicMock()
        loop._chat_handler.check_health.return_value = "ok"
        self.assertEqual(loop._check_health(side="reply"), "captcha")
        # 会话页检查器说 ok 不算数：全标签那一眼更宽
        loop._chat_handler.check_health.assert_not_called()
        self.assertIn("验证页", loop._health_why)

    def test_扫到的是浏览器没起来就不硬判(self):
        """端口读不到时 验证页标签() 只能回空列表——不许把"没读到"当成"有验证页"。"""
        loop = self._loop([])
        inst = MagicMock()
        inst.url = "https://www.zhipin.com/web/geek/chat"
        inst.run_js.return_value = {"inputs": 0, "iframe": 0, "text": "", "img": 0}
        loop.browser_manager.get_instance.return_value = inst
        loop._chat_handler = None
        结 = loop._check_health(side="reply")
        self.assertNotEqual(结, "captcha")

    def test_浏览器对象不在时照旧回断开(self):
        loop = self._loop([_标签("9", VERIFY_URL)])
        loop.browser_manager.get_instance.return_value = None
        loop.browser_manager.get_search_page.return_value = None
        self.assertEqual(loop._check_health(side="greet"), "browser_disconnected")

    def test_扫描拿回来的是怪东西不许判成有验证页(self):
        """ MagicMock、None、字符串都不是"有验证页"。

        这一条同时管两头：测试里没设过 验证页标签 的 mock 不能把两条腿全判停；
        线上句柄没建好时也不能界面上写着"等你滑验证"而其实什么都没读到。
        """
        for 怪 in (MagicMock(), None, "verify.html", {"url": VERIFY_URL}, [None, "x"]):
            loop = self._loop()
            loop.browser_manager.验证页标签.return_value = 怪
            self.assertEqual(loop._扫验证页(), [], repr(怪))
            self.assertFalse(loop._被验证页挡着())


    def test_一场验证只提示一次且摆窗口不许导航(self):
        """提示要狼来了地省着说；摆窗口只能"捞回来给他看"，不能把那一页刷掉。"""
        loop = self._loop([_标签("9", VERIFY_URL)])
        loop._emit_wind = MagicMock()
        loop._ensure_login_window = MagicMock(return_value=True)
        self.assertTrue(loop._被验证页挡着())
        self.assertTrue(loop._被验证页挡着())
        self.assertTrue(loop._被验证页挡着())
        loop._emit_wind.assert_called_once()
        loop._ensure_login_window.assert_called_once_with("验证侧", 导航=False)
        # 他滑完了：记下这一场几点结束的，冷却才开始计时
        loop.browser_manager.验证页标签.return_value = []
        self.assertFalse(loop._被验证页挡着())
        self.assertGreater(loop._验证结束时刻, 0)
        # 下一场要能重新提示
        loop.browser_manager.验证页标签.return_value = [_标签("9", VERIFY_URL)]
        self.assertTrue(loop._被验证页挡着())
        self.assertEqual(loop._emit_wind.call_count, 2)


class 会话页导航不许掀掉验证页Test(unittest.TestCase):
    """go_to_chat 以前是"URL 里没 chat 就 get(会话页)"，验证页恰好没 chat。"""

    def _处理器(self, 当前url):
        from boss_bot.page_handler import BossChatHandler
        h = BossChatHandler.__new__(BossChatHandler)
        h.page = MagicMock()
        h.page.url = 当前url
        return h

    def test_停在验证页时不许导航(self):
        h = self._处理器(VERIFY_URL)
        h.go_to_chat()
        h.page.get.assert_not_called()

    def test_停在登录页时也不许导航(self):
        h = self._处理器("https://www.zhipin.com/web/user/?ka=header-login")
        h.go_to_chat()
        h.page.get.assert_not_called()

    def test_停在会话页照旧不动(self):
        h = self._处理器("https://www.zhipin.com/web/geek/chat")
        h.go_to_chat()
        h.page.get.assert_not_called()

    def test_停在岗位页才真的跳会话页(self):
        """反向哨兵：这条要是不跳，采集就永远到不了会话页。"""
        h = self._处理器("https://www.zhipin.com/web/geek/job-recommend")
        h.go_to_chat()
        h.page.get.assert_called_once()


class 内存回收不许拆掉验证页Test(unittest.TestCase):
    """11:45 现场：2526 MB 触发回收重开，同一分钟那张 security.html 就没了——
    「我根本就登录不了」到这里才有个完整答案：不只是腿在刷那一页，回收在拆那一页。"""

    def _loop(self, 验中=()):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda *a, **k: None
        loop.browser_manager = MagicMock()
        loop.browser_manager.验证页标签.return_value = [_标签(i, VERIFY_URL) for i in 验中]
        loop.browser_manager.memory_mb.return_value = 2526
        loop.config.browser.memory_recycle_mb = 1600
        loop._重建浏览器 = MagicMock(return_value={"前": 2526, "后": 369})
        return loop

    def test_有验证页时回收不动手(self):
        loop = self._loop(["1"])
        self.assertFalse(loop._内存回收())
        loop._重建浏览器.assert_not_called()

    def test_没有验证页时回收照做(self):
        """反向哨兵：这条要是不重开，内存回收就成了空话，整机跟着换页。"""
        loop = self._loop([])
        self.assertTrue(loop._内存回收())
        loop._重建浏览器.assert_called_once()

    def test_回收受阻只说一遍(self):
        说过 = []
        loop = self._loop(["1"])
        loop._log = lambda 级, 消息, *a, **k: (
            说过.append(消息) if "回收先不做" in str(消息) else None)
        for _ in range(3):
            loop._内存回收()
        self.assertEqual(len(说过), 1)


class 岗位详情打开节流Test(unittest.TestCase):
    """"为什么会触发"查到的答案：被挡的那一页几乎全是打开 job_detail 打开出来的。"""

    def test_间隔没到就要等(self):
        self.assertAlmostEqual(打开要等秒(100.0, 110.0, 30.0), 20.0)

    def test_间隔到了就不等(self):
        self.assertAlmostEqual(打开要等秒(100.0, 140.0, 30.0), 0.0)

    def test_填0等于关掉节流(self):
        self.assertEqual(打开要等秒(100.0, 100.0, 0.0), 0.0)

    def test_上次没记过就不等(self):
        """第一趟（上次=0）不许等到天荒地老。"""
        self.assertEqual(打开要等秒(0.0, time.time(), 30.0), 0.0)

    def test_打开详情的出口真的调了节流(self):
        源 = open(os.path.join(ROOT, "boss_bot", "greet_engine.py"),
                  encoding="utf-8").read()
        i = 源.index('self._log("INFO", f"导航到: {url}")')
        段 = 源[max(0, i - 900):i]
        self.assertIn("_节流岗位详情打开()", 段,
                      "打开岗位详情页是唯一真被挡出来的口子，它不过节流等于没这条闸")

    def test_搜索页加载不许被节流(self):
        """反向哨兵：抓取腿整页加载搜索页不该等——两天 381 次加载 0 次被挡，
        给它加间隔只会少出货，不会少风控。"""
        源 = open(os.path.join(ROOT, "boss_bot", "greet_engine.py"),
                  encoding="utf-8").read()
        i = 源.index("def _parse_job_list")
        j = 源.index("\n    def ", i + 10)
        段 = 源[i:j]
        self.assertIn("instance.get(search_url)", 段)
        self.assertNotIn("_节流岗位详情打开()", 段,
                         "节流挪到搜索页上就是把抓取腿拖慢，这条不许发生")


class 验证后冷却Test(unittest.TestCase):
    """刚滑完就照原来的频率去撞，等于再要一张验证页。"""

    def _loop(self):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda *a, **k: None
        loop.browser_manager = MagicMock()
        loop.browser_manager.验证页标签.return_value = []
        return loop

    def test_刚过完验证这两条腿都不许动页(self):
        loop = self._loop()
        loop._验证结束时刻 = time.time()
        loop.config.greet.rate_limit.after_verify_cooldown = 300
        self.assertTrue(loop._验证后冷却中())

    def test_冷却期过了就放行(self):
        loop = self._loop()
        loop._验证结束时刻 = time.time() - 301
        loop.config.greet.rate_limit.after_verify_cooldown = 300
        self.assertFalse(loop._验证后冷却中())

    def test_冷却填0就是不等(self):
        loop = self._loop()
        loop._验证结束时刻 = time.time()
        loop.config.greet.rate_limit.after_verify_cooldown = 0
        self.assertFalse(loop._验证后冷却中())

    def test_健康检查那条路过完验证也记结束时刻(self):
        """今天 11:13 两张验证页都是 _captcha_gate 这条路认出来的，不是全标签页那道闸；
        这条不记结束时刻的话，滑完立刻照原速回去开页，冷却形同虚设。"""
        loop = self._loop()
        loop._running = True
        loop._stop_event = MagicMock()
        loop._stop_event.is_set.return_value = False
        loop._emit_wind = MagicMock()
        loop._ensure_login_window = MagicMock(return_value=False)
        loop._check_health = MagicMock(return_value="ok")
        loop._验证结束时刻 = 0.0
        self.assertTrue(loop._captcha_gate("greet"))
        self.assertGreater(loop._验证结束时刻, 0,
                           "验证过完的时刻必须记下，两条路共用同一份冷却")

    def test_两条腿的开轮闸门都认这个冷却(self):
        源 = open(os.path.join(ROOT, "boss_bot", "main_loop.py"),
                  encoding="utf-8").read()
        for 函数 in ("def _greet_loop", "def _reply_loop"):
            i = 源.index(函数)
            段 = 源[i:i + 2500]
            self.assertIn("_验证后冷却中()", 段, 函数 + "：这条腿开轮前要看验证冷却")
            self.assertIn("_被验证页挡着()", 段, 函数 + "：这条腿开轮前要看有没有验证页")


class 两个开关要三端齐Test(unittest.TestCase):
    """配置不许只在某一段里出现——见 memory「改了必须生效」。"""

    def setUp(self):
        self.cfg = UC.UnifiedConfig()

    def test_默认值在且讲得通(self):
        self.assertEqual(self.cfg.greet.rate_limit.job_detail_min_interval, 30)
        self.assertEqual(self.cfg.greet.rate_limit.after_verify_cooldown, 300)

    def test_默认间隔不会拖住额度(self):
        """每小时 30 单本来就是 120 秒一单：30 秒的地板只摊平连着抢，不该卡住正常节奏。"""
        每小时 = self.cfg.greet.rate_limit.max_per_hour
        self.assertLess(self.cfg.greet.rate_limit.job_detail_min_interval,
                        3600.0 / 每小时)

    def test_读进来要夹住(self):
        c = UC.UnifiedConfig()
        c._apply_bot_config({"rate_limit": {"job_detail_min_interval": 9999,
                                            "after_verify_cooldown": -5}})
        self.assertLessEqual(c.greet.rate_limit.job_detail_min_interval, 600)
        self.assertGreaterEqual(c.greet.rate_limit.job_detail_min_interval, 0)
        self.assertEqual(c.greet.rate_limit.after_verify_cooldown, 0)

    def test_序列化要带出去(self):
        d = self.cfg.to_dict()
        rl = d["rate_limit"]
        self.assertIn("job_detail_min_interval", rl)
        self.assertIn("after_verify_cooldown", rl)

    def test_界面有两个输入框且夹同一范围(self):
        html = open(os.path.join(ROOT, "flask-version", "templates", "index.html"),
                    encoding="utf-8").read()
        self.assertIn('id="jobDetailMinInterval"', html)
        self.assertIn('id="afterVerifyCooldown"', html)
        self.assertIn("job_detail_min_interval", html)
        self.assertIn("after_verify_cooldown", html)
        self.assertIn("600", html)
        self.assertNotIn("search_page_min_interval", html,
                         "旧键名留着会让界面写的键后端读不到（改了不生效）")

    def test_一致性脚本认识这两个键(self):
        源 = open(os.path.join(ROOT, "tools", "consistency_test",
                               "run_consistency_test.py"), encoding="utf-8").read()
        self.assertIn("job_detail_min_interval", 源)
        self.assertIn("after_verify_cooldown", 源)
        self.assertNotIn("search_page_min_interval", 源)


if __name__ == "__main__":
    unittest.main()
