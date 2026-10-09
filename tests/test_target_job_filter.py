# -*- coding: utf-8 -*-
"""只投数据分析类：投递侧第一次有了"正向"门槛。

由来：2026-10-09 用户原话「今天就给我投递数据分析的兼职，其他的不要」。
在此之前这一侧只有否决词（普工/主播/信贷…命中就不投），没命中否决词的
一律放行——所以搜"数据分析"带出来的"数据录入文员""电商运营助理"照投，
他这两天收到的主播/销售单就是这么来的（见 memory: no-positive-target-job-filter）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.greet_engine import jd_gate, target_job_hit  # noqa: E402
from boss_bot.unified_config import (AIConfig,  # noqa: E402
                                     TARGET_JOB_KEYWORDS_DEFAULT,
                                     UnifiedConfig)

DATA_JD = ("岗位职责：1. 负责业务数据的清洗与整理，用 SQL 从数仓取数；"
           "2. 输出日报周报与专题分析报告，定位指标波动原因；"
           "3. 配合业务方搭建看板，Excel/Python 熟练。"
           "任职要求：统计学或数学相关，能线上远程办公，每周交付三次以上。")


def _job(title="数据分析助理（线上兼职）", jd=DATA_JD):
    return {"job_name": title, "jd_description": jd, "jd_requirements": jd,
            "company": "某某科技"}


class TestTargetJobHit:

    def test_标题命中就算命中(self):
        assert target_job_hit(TARGET_JOB_KEYWORDS_DEFAULT,
                              _job(title="数据分析实习生"))

    def test_标题没写但JD里写了也算(self):
        """BOSS 上一堆「远程兼职｜数据方向」把岗位名写得很虚，正文才看得清"""
        assert target_job_hit(TARGET_JOB_KEYWORDS_DEFAULT,
                              {"job_name": "线上兼职助理",
                               "jd_description": DATA_JD})

    def test_不相关岗位不命中(self):
        for title in ("普工包吃住", "不露脸主播", "电商运营助理", "快递分拣"):
            assert not target_job_hit(
                TARGET_JOB_KEYWORDS_DEFAULT,
                {"job_name": title,
                 "jd_description": "负责该岗位日常事务，按要求完成分配的任务，细心负责。"}
            ), title

    def test_词表为空等于不做正向过滤(self):
        """关掉这一侧时不能把所有岗位都拦死——空表就是不过滤"""
        assert target_job_hit([], _job(title="主播")) == ""


class TestJdGateCarriesTarget:

    def test_命中方向的岗位放行(self):
        assert jd_gate([], _job(), title_keywords=[],
                       target_keywords=TARGET_JOB_KEYWORDS_DEFAULT) == ""

    def test_没命中方向的岗位拦下(self):
        reason = jd_gate([], {"job_name": "电商运营助理（线上兼职）",
                              "jd_description": "负责店铺日常运营、上下架、"
                              "客户咨询回复，需要到岗熟悉流程，每周例会。",
                              "jd_requirements": "有电商经验优先，能长期稳定。"},
                         title_keywords=[],
                         target_keywords=TARGET_JOB_KEYWORDS_DEFAULT)
        assert reason and "数据分析" in reason

    def test_否决词优先(self):
        """既命中否决又没命中方向时，报的是否决那一条（原因要能照着修）"""
        reason = jd_gate([], {"job_name": "主播（线上兼职）",
                              "jd_description": "居家直播聊天即可。"},
                         title_keywords=["主播"],
                         target_keywords=TARGET_JOB_KEYWORDS_DEFAULT)
        assert "主播" in reason


class TestConfigWired:

    def test_配置字段存在且默认为空(self):
        cfg = AIConfig()
        assert cfg.target_job_keywords == []

    def test_配置能读出来(self, tmp_path):
        import json
        path = tmp_path / "bot_config.json"
        path.write_text(json.dumps({
            "ai": {"enabled": True,
                   "target_job_keywords": ["数据分析", "数据挖掘"]}},
            ensure_ascii=False), encoding="utf-8")
        cfg = UnifiedConfig.load(str(path),
                                 profile_path=str(tmp_path / "none.json"),
                                 overrides_path=str(tmp_path / "none.json"))
        assert cfg.ai.target_job_keywords == ["数据分析", "数据挖掘"]

    def test_导出不丢字段(self, tmp_path):
        cfg = UnifiedConfig.load(str(ROOT / "bot_config.json"),
                                 overrides_path=str(tmp_path / "none.json"))
        cfg.ai.target_job_keywords = ["数据分析"]
        assert cfg.to_dict()["ai"]["target_job_keywords"] == ["数据分析"]


class TestGreetChainPassesIt:

    def test_闸门调用处带上了方向词表(self):
        src = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
        at = src.index("blocked = jd_gate(")
        seg = src[at:at + 300]
        assert "target_keywords=" in seg, "投递闸门没接正向方向词表"

    def test_引擎从配置取方向词表(self):
        src = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
        assert "_ai_target_job_keywords" in src

    def test_主循环热更新也带上(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert "_ai_target_job_keywords" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
