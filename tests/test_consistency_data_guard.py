# -*- coding: utf-8 -*-
"""三端一致性脚本里"这一趟没动用户数据"那条门的判据。

10-20 16:17 那一趟 22 项里红了 1 项，红的是这条哈希守卫——它把
`data/reply_records.json`、`data/job_pool.json` 也算进"不许变"，而面板正在跑回复腿
和抓取腿，这两份本来每隔几秒就被自己写一次。一条在线上面板前永远红的门，
等于没有门：下一次真把用户配置写脏了，报告里也只是一行"和往常一样红"。

所以判据拆成两段：配置文件任何时候都钉死；记录文件只在**真读到 running=true** 时豁免。
拆成纯函数就是为了在这里把它逼到红——包括"面板没在跑却动了记录"和"动了配置"两种。
"""

import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

脚本 = os.path.join(ROOT, "tools", "consistency_test", "run_consistency_test.py")


def _载脚本():
    规范 = importlib.util.spec_from_file_location("一致性脚本", 脚本)
    模块 = importlib.util.module_from_spec(规范)
    规范.loader.exec_module(模块)
    return 模块


class 用户数据守卫分得清谁在写Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.模块 = _载脚本()

    def _快照(self, **覆盖):
        基 = {名: "跑前" for 名 in self.模块.用户配置文件 + self.模块.运行中会写}
        基.update(覆盖)
        return 基

    def test_面板在跑时记录文件自己变了不判红(self):
        跑前 = self._快照()
        跑后 = self._快照(**{"data/reply_records.json": "跑后面板自己写的"})
        犯规, 豁免 = self.模块.判定用户数据(跑前, 跑后, True)
        self.assertEqual(犯规, {})
        self.assertIn("data/reply_records.json", 豁免,
                      "豁免要点名是哪几份、前后各是什么，不许糊成一句没动")

    def test_面板没在跑时记录文件变了就是脚本自己犯规(self):
        """这条是门能失败的那一半：同一个变化，面板停着时必须红"""
        跑前 = self._快照()
        跑后 = self._快照(**{"data/reply_records.json": "跑后"})
        犯规, 豁免 = self.模块.判定用户数据(跑前, 跑后, False)
        self.assertIn("data/reply_records.json", 犯规)
        self.assertEqual(豁免, {})

    def test_配置文件任何时候都钉死(self):
        for 在跑 in (True, False):
            跑前 = self._快照()
            跑后 = self._快照(**{"config_overrides.json": "跑后被写脏了"})
            犯规, _ = self.模块.判定用户数据(跑前, 跑后, 在跑)
            self.assertIn("config_overrides.json", 犯规,
                          f"面板在跑({在跑})也不能放过配置被写——10-20 就是丢了这一份")

    def test_清单里每一份都被真的盯着(self):
        """少一份就少一道闸：清单必须等于 用户配置 + 运行中会写 的全集"""
        盯住的 = set(self.模块.用户配置文件) | set(self.模块.运行中会写)
        self.assertEqual(set(self.模块.快照配置文件().keys()), 盯住的)


class 记录文件空了该不该红Test(unittest.TestCase):
    """10-21 这条门在线上永远红：只抓不投下打招呼记录就是 0 条，报告里却写成"数据可能丢了"。
    判据拆成纯函数，逼它在该红的时候真的红。"""

    @classmethod
    def setUpClass(cls):
        cls.模块 = _载脚本()

    def test_只抓不投时零条不算事故(self):
        self.assertEqual(self.模块.数据文件该不该红(0, 143, True, False, 只抓不投=True), [])

    def test_在投时零条必须红(self):
        """反向哨兵：把口径关掉，同样一份空文件就得当场定罪。"""
        红 = self.模块.数据文件该不该红(0, 143, True, False, 只抓不投=False)
        self.assertEqual(len(红), 1)
        self.assertIn("greet_records", 红[0]["warning"])

    def test_回复记录没有预期为空这一档(self):
        红 = self.模块.数据文件该不该红(0, 0, True, True, 只抓不投=True)
        self.assertEqual(len(红), 1)
        self.assertIn("reply_records", 红[0]["warning"])

    def test_备份也是空的就不红(self):
        self.assertEqual(self.模块.数据文件该不该红(0, 5, False, False, 只抓不投=False), [])


class 粗筛标签与词表对得上Test(unittest.TestCase):
    """这条门存在的理由：2026-10-21 号0+号1 共 1623 条池子里 `否决命中` 是 0 条。

    粗筛读的是引擎上根本不存在的属性，`or []` 把"没读到"兜成"空表"，界面画成
    "方向不合"看着像判过了。只有拿配置现算再比盘上才抓得住这种"安静地不干活"。
    """

    @classmethod
    def setUpClass(cls):
        cls.模块 = _载脚本()

    def _行(self, 标题, gate=None):
        return {"id": 标题, "job_name": 标题, "company": "公司",
                "url": "https://www.zhipin.com/job_detail/x.html",
                "gate": gate if gate is not None else {"方向命中": False}}

    def test_盘上没写否决命中但词表明明命中必须红(self):
        红 = self.模块.粗筛标签该不该红(
            [self._行("兼职在线打字客服")], ["打字"], ["客服"])
        self.assertEqual(len(红), 1, "这条就是那个 bug 的形状，必须抓得住")
        self.assertIn("客服", str(红[0]["现算"]))

    def test_标签与词表一致就不红(self):
        """反向哨兵：把盘上那条填成现算出来的值，同一份词表必须零差异。

        没有这一条，上面那条红可能是"函数无论如何都红"，抓不出真回归。
        """
        from boss_bot.greet_engine import veto_keyword_hit
        from boss_bot.job_pool import 岗位行成抓取
        行 = self._行("兼职在线打字客服")
        期 = veto_keyword_hit(["打字"], 岗位行成抓取(行), title_keywords=["客服"])
        行["gate"] = {"方向命中": False, "否决命中": 期}
        self.assertTrue(期, "样例本身要真能被词表命中")
        self.assertEqual(self.模块.粗筛标签该不该红([行], ["打字"], ["客服"]), [])

    def test_压根没贴标的行不算对不上(self):
        """手工粘 URL 进来的行 gate 是空的——那是"没判过"，不是"判错了"。"""
        self.assertEqual(self.模块.粗筛标签该不该红(
            [self._行("兼职在线打字客服", gate={})], [], ["客服"]), [])

    def test_两份词表都空时盘上该写词表没读到(self):
        红 = self.模块.粗筛标签该不该红(
            [self._行("数据分析师", {"方向命中": True})], [], [])
        self.assertEqual(len(红), 1)
        self.assertEqual(红[0]["现算"], "词表没读到")
        绿 = self.模块.粗筛标签该不该红(
            [self._行("数据分析师", {"方向命中": True, "词表没读到": True})], [], [])
        self.assertEqual(绿, [])


if __name__ == "__main__":
    unittest.main()
