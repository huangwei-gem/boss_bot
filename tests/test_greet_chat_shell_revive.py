"""「立即沟通」后页面自己跳进会话，却被 eager 掐成壳：这一族要能重走一次再核。

2026-11-03 00:57 机器按 11-02 口径给号1 勾了三条（华信柏霖 数据分析师、
洋泰盛海科技 商品数据分析（双休）、百观科技 数据分析师）。01:45~01:52 打招呼腿
逐条试投，三条全撞同一个成因，现场逐字：

    url=https://www.zhipin.com/web/geek/chat?id=…&jobId=…
    抽屉元素=无 按钮=[] 可见输入框=[] 隐藏模板输入框=1 个

那条 URL 本身就是会话，可页面一个元素都没挂出来。这跟 10-31 回复腿那张壳一模一样：
BOSS 的聊天应用把分片脚本与会话接口排在首屏之后，`unified_config.load_mode` 的
eager（它把主站一发 126~135 秒压到 1.6~4.8 秒）把它永远掐成 34 个节点，再等 8 秒
也不会活。回复腿那张当天就治了（`_会话页导航到会话页`：先设 normal 再导航），
投递腿这张一直没人治——所以三条单子一条都投不出去，每行还要烧三次岗位详情页，
而打开详情页正是风控最集中那一步（README「安全验证到底为什么触发」）。

这里只补"重走一次再核"，判据不另起：认不认得到会话仍是 `_wait_chat_open` +
`chat_is_open` 那一份。重走不成、或者重走完还是核不出，仍旧照实报红——
宁可红，不许把没证实的事记成投了。
"""

import unittest
from unittest.mock import MagicMock

from boss_bot.unified_config import UnifiedConfig

会话地址 = "https://www.zhipin.com/web/geek/chat?id=fc69884b886208df0HB7&jobId=891a8"
岗位页地址 = "https://www.zhipin.com/job_detail/ae4a9c329865d2b70nx53Nq0EltS.html"


class 假页:
    """够用的假标签页：只长这一族真会碰的四样——url、get、set.load_mode、page.load_mode。"""

    def __init__(self, 地址=会话地址, 能设=True, 回读认账=True, 导航会炸=False, 原样="eager"):
        self.url = 地址
        self.动作 = []
        self.能设 = 能设
        self.回读认账 = 回读认账
        self.导航会炸 = 导航会炸
        self.page = _空盒()
        self.page.load_mode = 原样
        self.set = _空盒()
        self.set.load_mode = self        # 页.set.load_mode.normal() 就落到下面的 normal()

    def normal(self):
        self._换("normal")

    def eager(self):
        self._换("eager")

    def none(self):
        self._换("none")

    def _换(self, 名):
        self.动作.append("设 %s" % 名)
        if not self.能设:
            raise RuntimeError("这一版 DrissionPage 没有 load_mode")
        if 名 == "normal" and not self.回读认账:
            return                        # 调用没报错，可 page.load_mode 不改口
        self.page.load_mode = 名

    def get(self, 地址):
        self.动作.append("导航 %s" % 地址)
        if self.导航会炸:
            raise RuntimeError("整页加载超时")


class _空盒:
    pass


def _引擎():
    from boss_bot.greet_engine import GreetEngine
    eng = GreetEngine(MagicMock(), UnifiedConfig(), account_index=1)
    eng.running = True
    eng.念 = []
    eng._log = lambda 级别, 话="": eng.念.append("%s %s" % (级别, 话))
    eng._random_delay = lambda *a, **k: None
    eng._interruptible_sleep = lambda *a, **k: None
    return eng


class 重走会话壳Test(unittest.TestCase):
    def test_先设normal再导航走完必须换回原样(self):
        """顺序错了就是白设（导航在前等于再掐一次）；不换来就是拿 126 秒的加载去做搜索。"""
        页 = 假页()
        self.assertTrue(_引擎()._会话壳按normal重走(页))
        self.assertEqual(页.动作, ["设 normal", "导航 %s" % 会话地址, "设 eager"],
                         "设→走→还，少一步这条腿就废在另一半上")
        self.assertEqual(页.page.load_mode, "eager")

    def test_设不上normal要照实说不许导航(self):
        eng = _引擎()
        页 = 假页(能设=False)
        self.assertFalse(eng._会话壳按normal重走(页))
        self.assertEqual(页.动作, ["设 normal"], "没设成就别走：走一次是白烧一发整页加载")
        self.assertTrue(any("没换成「normal」" in 话 for 话 in eng.念),
                        "设不上却不吭声，日志里就只剩一串莫名其妙的失败")

    def test_回读不认账要退回原样并且不导航(self):
        eng = _引擎()
        页 = 假页(回读认账=False)
        self.assertFalse(eng._会话壳按normal重走(页))
        self.assertEqual(页.动作, ["设 normal", "设 eager"], "回读不认账还硬走，记的就是假话")
        self.assertTrue(any("回读是「eager」不是 normal" in 话 for 话 in eng.念))

    def test_还在岗位页就一个字都不动(self):
        """没跳进会话地址时不许碰加载模式：那一支该归抽屉失败那族算。"""
        页 = 假页(地址=岗位页地址)
        self.assertFalse(_引擎()._会话壳按normal重走(页))
        self.assertEqual(页.动作, [])

    def test_导航炸了也要把加载模式还给搜索腿(self):
        eng = _引擎()
        页 = 假页(导航会炸=True)
        self.assertFalse(eng._会话壳按normal重走(页))
        self.assertEqual(页.动作[-1], "设 eager", "炸在导航上就把 normal 留在了这张页上")
        self.assertEqual(页.page.load_mode, "eager")
        self.assertTrue(any("重走这一发没成" in 话 for 话 in eng.念))


class 结算接上重走Test(unittest.TestCase):
    def _引擎(self, 重走行=True):
        eng = _引擎()
        self.标记, self.落库, self.重走次数 = [], [], 0
        eng._mark_chatted = lambda job: self.标记.append(job.get("company"))
        eng._record_sent_now = lambda job: self.落库.append(job.get("_auto_greet_note"))
        页 = 假页()

        def _重走(待核):
            self.重走次数 += 1
            return 重走行
        eng._会话壳按normal重走 = _重走
        return eng, 页

    def test_第一次核不出就重走一次再核(self):
        eng, 页 = self._引擎()
        结果 = iter(["none", "matched"])
        本号 = "你好，我对这个岗位很感兴趣"
        eng._auto_greet_followup = lambda 页, 话, 弹窗, job=None: next(结果)
        job = {"company": "华信柏霖"}
        self.assertEqual(eng._会话页直落结算(页, job, 本号), (True, ""))
        self.assertEqual(self.重走次数, 1, "壳治不了就等于这一族永远投不出去")
        self.assertEqual(self.标记, ["华信柏霖"], "重走后才核到的，照样要按已沟通落账")
        self.assertEqual(len(self.落库), 1)

    def test_重走走不成还是不认领(self):
        """重走不成（这一版设不上 normal）不许顺手把"跳进了会话地址"当送达。"""
        eng, 页 = self._引擎(重走行=False)
        self.assertIsNone(eng._会话页直落结算(页, {"company": "华信柏霖"}, "你好"))
        self.assertEqual(self.标记, [])
        self.assertEqual(self.落库, [])

    def test_形状本来就核得出来就不许多走一发(self):
        eng, 页 = self._引擎()
        eng._auto_greet_followup = lambda 页, 话, 弹窗, job=None: "matched"
        self.assertEqual(eng._会话页直落结算(页, {"company": "华信柏霖"}, "你好"), (True, ""))
        self.assertEqual(self.重走次数, 0, "抽屉/弹窗出得来的那一支不该多吃一发整页加载")

    def test_重走完还是核不出就照实报红(self):
        eng, 页 = self._引擎()
        eng._auto_greet_followup = lambda 页, 话, 弹窗, job=None: "none"
        self.assertIsNone(eng._会话页直落结算(页, {"company": "华信柏霖"}, "你好"))
        self.assertEqual(self.重走次数, 1)
        self.assertEqual(self.标记, [], "两次都核不出还记送达，就是把没证实的事说成投了")


class 判据没长第二份Test(unittest.TestCase):
    def test_重走只借原有判据不改判据(self):
        """这一族以前靠 GIVE_UP_FAMILIES 连着三次放手；治壳之后放手规则照旧，
        只是中间多一次重走。判据若被复制一份，两条腿就会判出两种结果。"""
        import inspect

        from boss_bot.greet_engine import GreetEngine
        结算 = inspect.getsource(GreetEngine._会话页直落结算)
        self.assertIn("self._会话壳按normal重走(instance)", 结算)
        self.assertEqual(结算.count("self._auto_greet_followup(instance, greeting, None, job)"), 2,
                         "重走前后各核一次，缺一次就是治不了壳")
        self.assertNotIn("chat_is_open(", 结算, "结算这儿不许自己起会话判据")


if __name__ == "__main__":
    unittest.main()
