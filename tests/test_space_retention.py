# -*- coding: utf-8 -*-
"""历史数据体积闸（10-23 他定的口径：到 500MB 自动清，只留 3 天）。

他原话：「仓库公有没事，不怕泄露，历史数据有多少了，到了 500M 吧自动清理吧，
最好是只保留 3 天的。」判据全写在 boss_bot/space_retention.py 一处，面板
（flask-version/app.py 的 cleanup_old_logs / 历史数据体检）和主循环
（boss_bot/main_loop.py 的 _clean_old_archives）调的是同一个函数。

测三件事：
1. 没到线**一个字节都不动**（最重要，误删不可逆）；
2. 到线了只删「按天的」条目——Cookie 那一族、非日期名的盒、data/ 根下的 live 记录
   结构上就够不着；
3. 删之前先落台账，删失败不许冒充已删。
每条护栏配了反向变异（窗口放到负数、白名单少写一格），证明它不是摆设。
"""
import json
import os
import shutil
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

from boss_bot.space_retention import (MB, 最小保留天数, 最小阈值MB, 历史体积,
                                      清理超期历史, 盒状范围, 文件范围, 台账路径,
                                      _候选, _名字日期)
from tests.conftest import mk测试临时目录

今天 = date(2026, 10, 23)
项目根 = Path(__file__).resolve().parents[1]


def _填(路径: Path, 字节: int) -> Path:
    """造一个真有这个大小的文件（truncate 扩位，不用落 51MB 的实际内容）。"""
    路径.parent.mkdir(parents=True, exist_ok=True)
    with open(路径, "wb") as f:
        f.write(b"\0")
        f.truncate(字节)
    return 路径


def _盒(根: Path, 相对: str, 天龄: int, 字节: int = 1024, 名字: str = None) -> Path:
    目录 = 根 / 相对 / (名字 or (今天 - timedelta(days=天龄)).isoformat())
    _填(目录 / "greet_records.json", 字节)
    return 目录


def _打桩旧(路径: Path, 天龄: int) -> Path:
    # 基准必须是测试里那个固定的 今天，不能拿真实时钟：这条用例的窗口是相对
    # 2026-10-23 算的，用真实时间打桩会随日子漂移（10-29 跑 9 天前正好落进
    # 窗口内，白红一次）。
    秒 = time.mktime((今天 - timedelta(days=天龄)).timetuple())
    os.utime(路径, (秒, 秒))
    return 路径


class 一棵建好的树(unittest.TestCase):
    def setUp(self):
        self.临时 = mk测试临时目录("体积闸")
        self.addCleanup(shutil.rmtree, str(self.临时), True)
        (self.临时 / "data").mkdir(parents=True, exist_ok=True)
        (self.临时 / "logs").mkdir(parents=True, exist_ok=True)

    def 清(self, 阈值MB=最小阈值MB, 保留天数=3, 干跑=False):
        return 清理超期历史(self.临时, 阈值MB=阈值MB, 保留天数=保留天数,
                            今天=今天, 干跑=干跑)

    def 凑到线(self):
        """一个 10 天前的归档盒，体积刚好压过最小阈值——所有「到线」用例的共同前提。"""
        return _盒(self.临时, "data/archive", 10, 字节=最小阈值MB * MB + MB)


class Test到线才动手(一棵建好的树):
    def test_没到线一个字节都不删(self):
        旧盒 = _盒(self.临时, "data/archive", 30, 字节=1024)
        _盒(self.临时, "data/archive_kept", 40)
        _填(self.临时 / "logs" / ("boss_bot.log.%s"
                                  % (今天 - timedelta(days=40)).isoformat()), 2048)
        结果 = self.清(阈值MB=500)
        self.assertFalse(结果["触发"])
        self.assertEqual(结果["删的"], [])
        self.assertTrue(旧盒.is_dir(), "没到线却把 30 天前的盒删了")
        self.assertIn("没到", 结果["原因"])
        self.assertIn("500 MB", 结果["原因"], "到没到线要带阈值念出来，不许只回一句「没删」")

    def test_到线就删窗口以外的按天盒(self):
        self.凑到线()
        窗口内 = _盒(self.临时, "data/archive", 2, 字节=1024)
        窗口外 = _盒(self.临时, "data/archive_kept", 4, 字节=1024)
        结果 = self.清(阈值MB=最小阈值MB, 保留天数=3)
        self.assertTrue(结果["触发"], "凑到 %d MB 了还说没到线" % 最小阈值MB)
        self.assertFalse(窗口外.exists(), "4 天前的盒到线了还不删，等于这条没生效")
        self.assertTrue(窗口内.is_dir(), "窗口内的盒被顺手删了")
        self.assertGreater(结果["释放字节"], 1024)
        self.assertLess(结果["清理后字节"], 结果["清理前字节"])
        self.assertNotEqual(结果["原因"], "", "删完必须念一句")

    def test_阈值填0等于每轮都清(self):
        _盒(self.临时, "data/archive", 10, 字节=1024)
        结果 = self.清(阈值MB=0, 保留天数=3)
        self.assertTrue(结果["触发"], "0 是「每轮都清」这个合法值，不该被兜回 500")
        self.assertEqual(结果["阈值MB"], 500, "0 当「不看体积」，报告里仍写默认阈值")
        self.assertEqual(len(结果["删的"]), 1)

    def test_手滑的阈值与天数被钳住(self):
        self.凑到线()
        今天的盒 = _盒(self.临时, "data/archive", 0, 字节=1024)
        结果 = self.清(阈值MB=1, 保留天数=0)
        self.assertEqual(结果["阈值MB"], 最小阈值MB)
        self.assertEqual(结果["保留天数"], 最小保留天数)
        self.assertTrue(结果["钳制了"])
        self.assertTrue(今天的盒.is_dir(), "留 0 天也不许把今天删掉")

    def test_正好卡在窗口边界那天也留着(self):
        """反向变异：判据从 `<= 保留天数` 写成 `< 保留天数`（差一天）就被这条抓到。"""
        self.凑到线()
        边界 = _盒(self.临时, "data/archive_kept", 3, 字节=1024)
        结果 = self.清(阈值MB=最小阈值MB, 保留天数=3)
        self.assertTrue(边界.is_dir(), "正好第 3 天的盒被删了：留 3 天含第 3 天")
        self.assertNotIn(边界.as_posix(), {c["路径"] for c in 结果["清单"]})

    def test_窗口内没有可删的就照实说还超线(self):
        _盒(self.临时, "data/archive", 1, 字节=最小阈值MB * MB + MB)
        结果 = self.清(阈值MB=最小阈值MB, 保留天数=3)
        self.assertTrue(结果["触发"])
        self.assertEqual(结果["删的"], [])
        self.assertTrue(结果["仍超线"], "一项没删却报「省了 0 MB」是假通过：要说还超线")
        self.assertIn("窗口内", 结果["原因"])


class Test只删按天的(一棵建好的树):
    def test_非日期名的盒一律不碰(self):
        过期 = self.凑到线()
        证据 = _盒(self.临时, "data/archive", 10, 名字="unmix_backup")
        结果 = self.清(阈值MB=最小阈值MB)
        self.assertTrue(证据.is_dir(), "unmix_backup 是别的流程留下的证据，不许当过期盒")
        self.assertEqual([x["路径"] for x in 结果["删的"]], [过期.as_posix()],
                         "动手的不止那一盒按天的")

    def test_非日期名的盒哪怕mtime很老也不碰(self):
        """反向变异：目录名认不出日期就退回去读 mtime，这条会红。

        盒状范围只认名字里的日期，是因为 data/archive/ 里混着别的流程留下的证据盒
        （unmix_backup 那一类）——它们的 mtime 和真归档一样老，按 mtime 判就一起没了。
        """
        过期 = self.凑到线()
        证据 = _盒(self.临时, "data/archive", 10, 名字="unmix_backup")
        _打桩旧(证据, 40)
        _打桩旧(证据 / "greet_records.json", 40)
        结果 = self.清(阈值MB=最小阈值MB)
        self.assertTrue(证据.is_dir(), "名字不是日期的盒被删了")
        self.assertEqual([x["路径"] for x in 结果["删的"]], [过期.as_posix()])

    def test_live记录结构上够不着(self):
        self.凑到线()
        活 = _打桩旧(_填(self.临时 / "data" / "greet_records.json", 4096), 60)
        池 = _打桩旧(_填(self.临时 / "data" / "job_pool.json", 4096), 60)
        结果 = self.清(阈值MB=最小阈值MB)
        self.assertEqual(len(结果["删的"]), 1, "只该删那个过期归档盒")
        self.assertTrue(活.is_file() and 池.is_file(), "data 根下的 live 文件被删了")

    def test_logs里认不出日期的按mtime办(self):
        self.凑到线()
        旧日志 = _打桩旧(_填(self.临时 / "logs" / "某次巡检输出.txt", 2048), 9)
        新日志 = _填(self.临时 / "logs" / "今天的输出.txt", 2048)
        结果 = self.清(阈值MB=最小阈值MB, 保留天数=3)
        路径集 = {x["路径"] for x in 结果["清单"]}
        self.assertIn(旧日志.as_posix(), 路径集)
        self.assertNotIn(新日志.as_posix(), 路径集)
        self.assertFalse(旧日志.exists(), "清单里列了却没删成——mtime 那一路是摆设")
        self.assertTrue(新日志.exists())

    def test_留档副本也在射程里(self):
        self.凑到线()
        旧留档 = _填(self.临时 / "data" / "kept" / "记录_20261001_120000_reply_records.json", 2048)
        旧截断 = _填(self.临时 / "logs" / "kept" / "boss_bot.log.2026-10-02.截断前", 2048)
        结果 = self.清(阈值MB=最小阈值MB)
        self.assertFalse(旧留档.exists())
        self.assertFalse(旧截断.exists())

    def test_cookie那一族永不进候选(self):
        """反向变异：窗口放到 -1（什么都算过期），Cookie 那几条依然不能在候选里。"""
        self.凑到线()
        _盒(self.临时, "data/archive_kept", 40, 名字="2026-09-01_cookie备份")
        _填(self.临时 / "data" / "cookie_backups" / "2026-09-01.json", 1024)
        _填(self.临时 / "data" / "stale_cookies" / "2026-09-01.json", 1024)
        对照 = _盒(self.临时, "data/archive", 40, 字节=1024)
        路径集 = {c["路径"].as_posix() for c in _候选(self.临时, 今天, -1)}
        self.assertIn(对照.as_posix(), 路径集, "对照组都没进候选，这个测试证明不了任何事")
        for 路径 in 路径集:
            self.assertNotIn("cookie", 路径.lower(), "豁免被绕过去了：%s" % 路径)


class Test白名单不是黑名单(一棵建好的树):
    def test_反向变异_白名单少一格就会漏(self):
        self.凑到线()
        _盒(self.临时, "data/archive_kept", 10, 字节=1024)
        _填(self.临时 / "logs" / ("boss_bot.log.%s"
                                  % (今天 - timedelta(days=10)).isoformat()), 1024)
        _填(self.临时 / "logs" / "kept" / ("boss_bot.log.%s"
                                           % (今天 - timedelta(days=10)).isoformat()), 1024)
        _填(self.临时 / "data" / "kept" / "记录_20261001_120000_x.json", 1024)
        结果 = self.清(阈值MB=最小阈值MB)
        for 相对 in list(盒状范围) + list(文件范围):
            格 = (self.临时 / 相对).as_posix() + "/"
            self.assertTrue(any(c["路径"].startswith(格) for c in 结果["清单"]),
                            "%s 里的过期条目一个都没进候选——白名单漏了这一格" % 相对)

    def test_白名单外的路径一条都不许进候选(self):
        self.凑到线()
        别处 = _打桩旧(_填(self.临时 / "data" / "resume_steps" / "2026-10-01_现场.json", 4096), 60)
        _填(self.临时 / "cloakbrowser" / "chrome.exe", 4096)
        结果 = self.清(阈值MB=最小阈值MB)
        self.assertNotIn(别处.as_posix(), {c["路径"] for c in 结果["清单"]})
        self.assertTrue(别处.is_file())
        self.assertTrue((self.临时 / "cloakbrowser" / "chrome.exe").is_file())


class Test台账与失败诚实(一棵建好的树):
    def test_删之前先落台账_台账自己不在可删范围(self):
        self.凑到线()
        _盒(self.临时, "data/archive_kept", 20, 字节=1024)
        结果 = self.清(阈值MB=最小阈值MB)
        self.assertTrue(结果["台账"], "删了却没留下「哪天删了什么」的账")
        账 = json.loads((self.临时 / 台账路径).read_text(encoding="utf-8"))
        这一轮 = [x for x in 账["轮次"] if x["时刻"]]
        self.assertEqual(len(这一轮[-1]["删的"]), 2)
        self.assertEqual(这一轮[-1]["保留天数"], 3)
        self.assertEqual(这一轮[-1]["阈值MB"], 最小阈值MB)
        self.assertFalse(any(台账路径 in c["路径"] for c in 结果["清单"]),
                         "台账落在可删范围里，会被下一轮自己吃掉")

    def test_台账只留最近二十轮(self):
        self.凑到线()
        for 次 in range(25):
            _盒(self.临时, "data/archive", 10 + 次, 字节=64)
            self.清(阈值MB=0)
        账 = json.loads((self.临时 / 台账路径).read_text(encoding="utf-8"))
        self.assertLessEqual(len(账["轮次"]), 20)

    def test_删失败不冒充已删(self):
        盒 = _盒(self.临时, "data/archive", 10, 字节=最小阈值MB * MB + MB)
        好 = self.清(阈值MB=最小阈值MB)
        self.assertEqual(好["失败"], [])
        self.assertTrue(all(x["实际"] == "已删" for x in 好["删的"]))
        self.assertFalse(盒.exists())

        新树 = mk测试临时目录("体积闸_删不动")
        self.addCleanup(shutil.rmtree, str(新树), True)
        _盒(新树, "data/archive", 10, 字节=最小阈值MB * MB + MB)
        真rmtree = shutil.rmtree

        def 装死(src, *a, **k):
            raise OSError(13, "Permission denied")

        shutil.rmtree = 装死
        try:
            坏 = 清理超期历史(新树, 阈值MB=最小阈值MB, 保留天数=3, 今天=今天)
        finally:
            shutil.rmtree = 真rmtree
        self.assertEqual(坏["删的"], [], "删不动却记成「已删」")
        self.assertEqual(len(坏["失败"]), 1)
        self.assertTrue(坏["触发"], "到线了要照实说动手了，只是这一项没删成")
        self.assertGreaterEqual(坏["清理后字节"], 最小阈值MB * MB)

    def test_干跑只算账不删(self):
        盒 = self.凑到线()
        结果 = self.清(阈值MB=最小阈值MB, 干跑=True)
        self.assertTrue(结果["触发"])
        self.assertEqual(len(结果["清单"]), 1)
        self.assertTrue(盒.is_dir(), "干跑却把盒删了")
        self.assertFalse((self.临时 / 台账路径).exists(), "干跑不许写台账")
        self.assertIn("干跑", 结果["原因"])


class Test体积数得对(一棵建好的树):
    def test_历史体积按目录分得清(self):
        _盒(self.临时, "data/archive", 1, 字节=3 * MB)
        _盒(self.临时, "data/archive_kept", 1, 字节=2 * MB)
        _填(self.临时 / "logs" / "boss_bot.log", 1 * MB)
        _填(self.临时 / "data" / "greet_records.json", 1024)
        量 = 历史体积(self.临时)
        self.assertAlmostEqual(量["按目录"]["data/archive"], 3 * MB, delta=2048)
        self.assertAlmostEqual(量["按目录"]["data/archive_kept"], 2 * MB, delta=2048)
        self.assertAlmostEqual(量["按目录"]["logs/(根下散文件)"], 1 * MB, delta=2048)
        self.assertIn("data/(根下散文件)", 量["按目录"])
        self.assertEqual(量["字节"], sum(量["按目录"].values()))
        self.assertGreaterEqual(量["文件数"], 4)

    def test_名字日期认得出也拒得掉(self):
        self.assertEqual(_名字日期("记录_20261001_120000_reply_records.json"), date(2026, 10, 1))
        self.assertEqual(_名字日期("boss_bot.log.2026-10-21"), date(2026, 10, 21))
        self.assertEqual(_名字日期("2026-10-09.挪走于20261020010101"), date(2026, 10, 9))
        self.assertIsNone(_名字日期("unmix_backup"))
        self.assertIsNone(_名字日期("job_pool.json"))
        self.assertIsNone(_名字日期("2026-13-45"), "认不出合法日期就返回 None，不许猜")


class Test两端口径一份(一棵建好的树):
    def test_面板与主循环调的是同一份判据(self):
        import inspect
        from boss_bot import main_loop as ml
        面板源 = (项目根 / "flask-version" / "app.py").read_text(encoding="utf-8")
        主循环源 = inspect.getsource(ml.UnifiedBotLoop._clean_old_archives)
        for 源, 名 in ((面板源, "面板"), (主循环源, "主循环")):
            self.assertIn("space_retention import 清理超期历史", 源,
                          "%s 没调体积闸（或者自己另写了一份）" % 名)
            self.assertIn("history_cleanup_mb", 源, "%s 的阈值没读配置" % 名)
            self.assertIn("history_keep_days", 源, "%s 的保留天数没读配置" % 名)

    def test_配置里那两个键读得写得出(self):
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        self.assertEqual(cfg.log.history_cleanup_mb, 500)
        self.assertEqual(cfg.log.history_keep_days, 3)
        cfg._apply_bot_config({"log": {"history_cleanup_mb": 800, "history_keep_days": 5,
                                       "log_retention_days": 14}})
        self.assertEqual(cfg.log.history_cleanup_mb, 800)
        self.assertEqual(cfg.log.history_keep_days, 5)
        out = cfg.to_dict()["log"]
        self.assertEqual(out["history_cleanup_mb"], 800)
        self.assertEqual(out["history_keep_days"], 5)
        self.assertIn("log_retention_days", out)
        # 0 是「每轮都清」这个合法值，不许被 max(1,...) 那类写法兜掉
        cfg._apply_bot_config({"log": {"history_cleanup_mb": 0}})
        self.assertEqual(cfg.log.history_cleanup_mb, 0)

    def test_面板上那两格有入口且落得进配置(self):
        页面 = (项目根 / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="advHistoryCleanupMb"', 页面)
        self.assertIn('id="advHistoryKeepDays"', 页面)
        self.assertIn("config.log.history_cleanup_mb = ", 页面, "输入框没写回配置=假选项")
        self.assertIn("config.log.history_keep_days = ", 页面)
        self.assertIn("setVal('advHistoryCleanupMb'", 页面, "存进去却读不回来，刷新就回默认值")
        self.assertIn("setVal('advHistoryKeepDays'", 页面)
        self.assertIn("loadRetention()", 页面)
        self.assertIn("'/api/retention'", 页面)

    def test_存的那段不许用或零兜底(self):
        """0 是合法值；`|| 500` 那种写法永远存不进 0（上一轮在 memory_recycle_mb 踩过）。"""
        页面 = (项目根 / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
        起 = 页面.index("var _清MB")
        段 = 页面[起:页面.index("config.log.history_keep_days =") + 120]
        self.assertIn("isNaN", 段, "没用 isNaN，0 会被当成留空")
        self.assertNotIn("value) || 500", 段)
        self.assertNotIn("value) || 3", 段)
        self.assertIn("Math.max(0, _清MB)", 段, "阈值下限要收在 0（0=每轮都清），不能钳成 500")

    def test_面板那个日志窗口天数也读配置(self):
        """log_retention_days 原来钉死 7，配置写 14 没人读——顺手接到清理日志上。"""
        源 = (项目根 / "flask-version" / "app.py").read_text(encoding="utf-8")
        起 = 源.index("def cleanup_old_logs")
        段 = 源[起:起 + 2600]
        self.assertIn("保留天数=配置.log.log_retention_days", 段)


class Test真实仓库现在动不得(一棵建好的树):
    def test_今天这台机器没到线(self):
        """跑在真仓库上，但只干跑——现在离 500MB 还远，一条都不该被删。"""
        量 = 历史体积(项目根)
        预演 = 清理超期历史(项目根, 阈值MB=500, 保留天数=3, 干跑=True)
        # 两次扫描之间线上面板还在写日志，所以只能要求"不许变小"：
        # 10-31 全量跑这条红过一次，比的还是"两次一模一样"，而后一次多出 575 字节——
        # 红的是拿着一块正在被写的盘子取两次全等，不是闸坏了。
        self.assertGreaterEqual(预演["清理前字节"], 量["字节"],
                                "干跑之后盘子反而小了 %.1f KB，那是删了东西"
                                % ((量["字节"] - 预演["清理前字节"]) / 1024))
        if 量["字节"] < 500 * MB:
            self.assertFalse(预演["触发"],
                             "实测 %.1f MB 还没到 500 MB，闸却说会动手" % (量["字节"] / MB))
            self.assertEqual(预演["删的"], [])
        else:
            self.assertTrue(预演["触发"], "已经 %.1f MB 了却说没到线" % (量["字节"] / MB))
        self.assertGreater(量["字节"], 100 * MB, "真仓库体积小于 100MB？那这个测试数错了范围")


if __name__ == "__main__":
    unittest.main(verbosity=2)
