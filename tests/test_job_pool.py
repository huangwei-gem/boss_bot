# -*- coding: utf-8 -*-
"""岗位候选池的数据层：抓来的岗位要能存下来、能改能删能筛，且两个号各管各的。

2026-10-19 之前的形状（Explore 实测过的行号）：`GreetEngine._parse_job_list()`
在 `greet_engine.py:2504-2514` 把卡片解析成 dict，`2594` 行 `self.jobs = 处理后的岗位`
只存在内存里，`main_loop.py:1917` 拿到就当轮直接逐个投（`2065` 行 send_greeting）。
所以他要"先抓岗位、我再挑着投"缺的不是判据，是**一个能把岗位留下来的地方**。

这一层只管存与查，三条硬要求都在下面的用例里：
- 同一个 URL 不能一遍一轮各建一行（否则界面被重复行灌满，他没法勾）；
- 他已经标过"不投/备注"的行，下一轮再抓到不许被重置（那等于机器悄悄改他的决定）；
- 文件读坏了不能当"池子是空的"然后回写把旧数据盖掉（这份是用户攒的候选清单）。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.job_pool import (       # noqa: E402
    岗位池, 行id, 状态待选, 状态已投, 状态不投)


def 抓一条(**over):
    j = {"job_name": "兼职·数据分析 50-55元/时", "company": "成都心动驿站",
         "salary": "50-55元/时", "location": "成都", "city": "成都",
         "query": "数据分析", "job_type": "兼职",
         "url": "https://www.zhipin.com/job_detail/aaa111.html"}
    j.update(over)
    return j


def 池(tmp, account_index=0):
    return 岗位池(account_index=account_index, 目录=tmp)


class 收岗位Test:
    """抓到就落盘，同一条不许落两次"""

    def test_第一次抓到建一行并带齐界面要用的字段(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            结果 = p.收([抓一条()])
            assert 结果["新增"] == 1, 结果
            行 = p.列表()[0]
            for 字段 in ("id", "url", "job_name", "company", "salary", "city",
                        "query", "captured_at", "status"):
                assert 行.get(字段) not in (None, ""), f"界面要用 {字段}，行里没有: {行}"
            assert 行["status"] == 状态待选
            assert 行["seen_count"] == 1

    def test_同一URL再抓到不建第二行(self):
        """一轮几十上百条，重名重岗靠 URL 认；建第二行就是给界面灌重复行"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            p.收([抓一条()])
            结果 = p.收([抓一条(salary="55-60元/时")])
            assert 结果["新增"] == 0 and 结果["见过"] == 1, 结果
            assert len(p.列表()) == 1
            行 = p.列表()[0]
            assert 行["seen_count"] == 2
            assert "55" in 行["salary"], f"再抓到要拿新值: {行['salary']}"
            assert 行["status"] == 状态待选

    def test_他标过不投的行再抓到不许变回待选(self):
        """状态是他下的决定，机器只有执行的份"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            p.收([抓一条()])
            编号 = p.列表()[0]["id"]
            p.标状态([编号], 状态不投)
            p.收([抓一条()])
            assert p.列表()[0]["status"] == 状态不投, "他标的不投被机器重置了"
            assert p.列表()[0]["seen_count"] == 2

    def test_没有URL的不收(self):
        """没 URL 就认不出重复、也没法投递，塞进去只会脏界面"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            结果 = p.收([抓一条(url=""), 抓一条(url="   ")])
            assert 结果["新增"] == 0
            assert 结果["无URL"] == 2, f"要说清为什么没进池: {结果}"
            assert p.列表() == []

    def test_两个号各写各的文件互看不见(self):
        """和 greet_records 一个口径：串号了他勾的是另一个号的岗位"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            a = 池(td, account_index=0)
            b = 池(td, account_index=1)
            assert os.path.normpath(a.路径) != os.path.normpath(b.路径)
            a.收([抓一条()])
            b.收([抓一条(url="https://www.zhipin.com/job_detail/bbb222.html")])
            assert len(a.列表()) == 1 and len(b.列表()) == 1
            assert a.列表()[0]["url"] != b.列表()[0]["url"]

    def test_行id只认URL且稳定(self):
        assert 行id(抓一条()["url"]) == 行id(抓一条()["url"])
        assert 行id(抓一条()["url"]) != 行id("https://www.zhipin.com/job_detail/ccc.html")


class 改删与回读Test:
    """增删改查都得回读才算成——这条项目里已经栽过一次（「我已登录」没写盘却说成了）"""

    def test_改备注要回读得到(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            p.收([抓一条()])
            编号 = p.列表()[0]["id"]
            成功, 说明 = p.改(编号, note="这家问过，先留着")
            assert 成功 is True, 说明
            assert 岗位池(account_index=0, 目录=td).列表()[0]["note"] == "这家问过，先留着"

    def test_改不存在的编号要说没有这条(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            成功, 说明 = p.改("不存在", note="x")
            assert 成功 is False
            assert "不存在" in 说明 or "没有这条" in 说明, 说明

    def test_删完回读真没了(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            p.收([抓一条(), 抓一条(url="https://www.zhipin.com/job_detail/bbb.html")])
            编号s = [r["id"] for r in p.列表()]
            成功, 说明 = p.删(编号s[:1])
            assert 成功 is True, 说明
            assert len(p.列表()) == 1
            assert "1" in 说明, f"删了几条要报得出: {说明}"

    def test_文件读坏了不许当成空池再回写盖掉(self):
        """这份是他攒的候选清单；半截 JSON 判成"没有岗位"然后写空 = 又丢一次数据"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            p.收([抓一条()])
            原样 = io.open(p.路径, encoding="utf-8").read()
            io.open(p.路径, "w", encoding="utf-8").write(原样[: len(原样) // 2])
            assert p.列表() == []          # 读不出来就是读不出来，不许编
            assert os.listdir(os.path.join(td, "pool_bad")), "坏文件要留证据，不能抹掉"
            结果 = p.收([抓一条(url="https://www.zhipin.com/job_detail/zzz.html")])
            assert 结果["新增"] == 1
            留在盘上的 = io.open(p.路径, encoding="utf-8").read()
            assert "zzz" in 留在盘上的
            assert 原样[:60] not in 留在盘上的, "坏的那份已经被证据接住，池子该重新起"


class 列表筛选Test:
    def _灌(self, p):
        p.收([
            抓一条(),
            抓一条(url="https://www.zhipin.com/job_detail/ccc.html",
                  job_name="主播 日结", company="某某传媒"),
            抓一条(url="https://www.zhipin.com/job_detail/ddd.html",
                  job_name="标注兼职", company="青岛云裁"),
        ])

    def test_按状态筛(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            self._灌(p)
            assert len(p.列表(状态=状态待选)) == 3
            编号 = p.列表()[0]["id"]
            p.标状态([编号], 状态已投)
            assert len(p.列表(状态=状态已投)) == 1
            assert p.列表(状态=状态待选)[0]["id"] != 编号

    def test_按关键字筛岗位名和公司(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            self._灌(p)
            assert len(p.列表(关键字="标注")) == 1
            assert len(p.列表(关键字="云裁")) == 1
            assert p.列表(关键字="不存在的词") == []

    def test_只看方向命中的(self):
        """粗筛标签是机器打的，界面要能只看"像能投的"，但不许把别的删掉"""
        import tempfile
        from boss_bot.job_pool import 筛行
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            self._灌(p)
            编号 = [r["id"] for r in p.列表() if "主播" in r["job_name"]][0]
            p.改(编号, gate={"方向命中": False, "否决命中": "主播", "粗筛结论": "不投这一类"})
            assert len(筛行(p.列表(), 粗筛档="没过过的")) == 2
            assert len(p.列表()) == 3, "筛只是筛，不许顺手删"

    def test_按AI分降序且没分的排后面(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = 池(td)
            self._灌(p)
            行s = {r["job_name"]: r["id"] for r in p.列表()}
            p.改(行s["标注兼职"], ai={"分": 82, "结论": "match", "理由": "线上兼职"})
            p.改(行s["兼职·数据分析 50-55元/时"], ai={"分": 91, "结论": "match", "理由": "对口"})
            排好的 = p.列表(排序="分降")
            assert 排好的[0]["ai"]["分"] == 91 and 排好的[1]["ai"]["分"] == 82
            assert 排好的[2].get("ai", {}).get("分") in (None, 0, ""), \
                f"没判过分的不该挤在前面: {排好的[2]}"


class 配置真生效Test:
    """池子那两个开关不是摆设：默认值、钳制、读写往返都要真的接上"""

    def test_capture_only默认只抓不投(self):
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        assert cfg.greet.capture_only is True, (
            "他 10-19 定的口径是【投哪条由我勾】，默认必须是只抓不投")

    def test_capture_only读写往返(self):
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        cfg.greet.capture_only = False
        块 = cfg.to_dict()["greet"]
        assert "capture_only" in 块, "存不下来 = 界面改了不生效"
        assert 块["capture_only"] is False
        读回 = UnifiedConfig()
        读回._apply_bot_config({"greet": {"capture_only": True}})
        assert 读回.greet.capture_only is True, "界面那个开关得真能读回来"

    def test_精筛每轮上限有钳制(self):
        """上限写成负数或十万都会把 AI 额度打穿；和 probe_max_per_round 一样夹住"""
        from boss_bot.unified_config import UnifiedConfig
        新 = UnifiedConfig()
        新._apply_bot_config({"ai": {"screen_max_per_round": 999999}})
        assert 新.ai.screen_max_per_round <= 400, 新.ai.screen_max_per_round
        负 = UnifiedConfig()
        负._apply_bot_config({"ai": {"screen_max_per_round": -5}})
        assert 负.ai.screen_max_per_round >= 0
