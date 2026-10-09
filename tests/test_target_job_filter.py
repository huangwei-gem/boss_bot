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


class TestLabelingHalfStillCounts:
    """目标有两半（数据分析 + 数据标注），词表却是一个"标注"都没有。

    10-09 现读 `ai.target_job_keywords` = 下面 NO_LABEL_TABLE 那份，是照着
    "只投数据分析类"那句话建的；结果当天标题含"标注"的 19 单里
    **新投出去 0 单**，6 单被这道闸以"标题和 JD 里都没有数据分析类字样"拦死
    （视频数据标注/AI训练师 5 单 + 数据标注专员-影视方向 1 单）。
    账号默认招呼语本身就写着"想找线上兼职（数据分析/数据标注方向）"。
    """

    NO_LABEL_TABLE = ["数据分析", "数据挖掘", "数据治理", "数据建模", "商业分析",
                      "经营分析", "数据仓库", "数据清洗", "指标体系", "BI", "SQL"]
    LABEL_JD = ("【岗位职责】 1. 负责视频通话场景下多模态数据标注与模型评测工作，"
                "依据评测标准判定模型回复质量，精准识别、定位模型各类问题。"
                "2. 从多维度开展评测：画面识别、属性细节、空间指代、OCR、时序实时性。"
                "【岗位要求】 1. 本科及以上学历，专业不限；"
                "2. 有 1 年及以上数据标注、数据质检、AI 模型评测相关经验优先。")

    def _job(self, title):
        return {"job_name": title, "jd_description": self.LABEL_JD,
                "jd_requirements": self.LABEL_JD, "company": "嵩聿奕科技有限公司"}

    def test_词表里没有标注字样时数据标注岗仍算方向(self):
        assert target_job_hit(self.NO_LABEL_TABLE,
                              self._job("视频数据标注/AI训练师"))

    def test_标题只写标注员没写数据也算(self):
        """「兼职·居家2d标注员(接受无经验)」正文常常一句话都没有，判据得认标题"""
        assert target_job_hit(self.NO_LABEL_TABLE,
                              {"job_name": "兼职·居家2d标注员(接受无经验)15-20元/时"})

    def test_真实标注岗过闸门不再被拦(self):
        assert jd_gate([], self._job("数据标注专员-影视方向"),
                       title_keywords=[], target_keywords=self.NO_LABEL_TABLE) == ""

    def test_标题写着到岗的标注岗仍拦得住(self):
        """补标注这半壁不能把"线下面试+到岗"那种放出去——否决词表接不住这一写法"""
        reason = jd_gate([], {"job_name": "数据标注兼职（可转正-线下面试和到岗）",
                              "jd_description": self.LABEL_JD,
                              "jd_requirements": self.LABEL_JD},
                         title_keywords=[], target_keywords=self.NO_LABEL_TABLE)
        assert reason, "这一单自己写了要来公司，不该投"

    def test_同一份JD去掉到岗字样就该放行(self):
        """上一条拦的是"到岗"这几个字，不是标注方向本身"""
        assert jd_gate([], {"job_name": "数据标注兼职（可转正）",
                            "jd_description": self.LABEL_JD,
                            "jd_requirements": self.LABEL_JD},
                       title_keywords=[], target_keywords=self.NO_LABEL_TABLE) == ""

    def test_跑偏的岗照旧拦(self):
        for title in ("实验室分析员", "电商运营助理", "项目经理（数据采集）"):
            reason = jd_gate([], {"job_name": title,
                                  "jd_description": "负责样本检测与原始记录，按规程操作仪器。",
                                  "jd_requirements": "化学、环境监测相关专业优先。"},
                             title_keywords=[], target_keywords=self.NO_LABEL_TABLE)
            assert reason and "方向" in reason, title

    def test_空词表仍然是不做正向过滤(self):
        assert target_job_hit([], {"job_name": "普工包吃住"}) == ""

    REAL_ONLINE_LABEL_JD = (
        "工作周期：6个月 每周工期：5天及以上 工作时间：不限 结算方式：月结\n"
        "**项目背景** 归属【高价值专家】长期大项目，线上远程长期协作，"
        "**要求每日稳定在线 4-8 小时，长期参与**\n"
        "**工作内容** 1. 依据 rubrics 规则，做专业评估、要素提取、内容校验；"
        "2. 按照 rubrics 标准完成判别、标注、要点梳理工作；"
        "3. 输出结构化评估结论，配合团队迭代评测标准。")

    def test_标题写在线标注的也算这半壁(self):
        """10-10 这一天被同一判据拦了 15 次：Centific「医疗合规质量与数据决策专家‑在线标注兼职」

        标题写着"在线标注兼职"，正文只有一句"完成判别、标注、要点梳理"，
        既没有"数据标注"连写也没有"标注员/标注专员"——现判据回放命中为空，
        于是这一单在 04:32 等时刻被"不是要投的方向"反复拦掉。
        """
        assert not any("标注" in k for k in self.NO_LABEL_TABLE)
        assert target_job_hit(self.NO_LABEL_TABLE, {
            "job_name": "医疗合规质量与数据决策专家‑在线标注兼职",
            "jd_description": self.REAL_ONLINE_LABEL_JD,
            "description": self.REAL_ONLINE_LABEL_JD,
            "company": "Centific"},
        ), "标题写着「在线标注兼职」却按没命中方向拦掉"

    def test_正文里的标注两个字不算方向(self):
        """方向词只认标题里的"标注"：正文写"按要求标注通话质检标签"的客服岗不是标注岗"""
        assert not target_job_hit(self.NO_LABEL_TABLE, {
            "job_name": "电话客服（线上兼职）",
            "jd_description": "负责接听来电，按要求标注通话场景的质检标签，记录工单。"})

    def test_标题写标注的岗仍不许带到岗(self):
        assert not target_job_hit(self.NO_LABEL_TABLE, {
            "job_name": "在线标注专员（需到岗坐班）",
            "jd_description": self.LABEL_JD})

    def test_用户配置那张表现读仍不含标注(self):
        """这条是回归哨兵：他自己补上「数据标注」后本条会红，届时删掉它即可"""
        cfg = UnifiedConfig.load(str(ROOT / "bot_config.json"),
                                 overrides_path=str(ROOT / "__none__.json"))
        if not cfg.ai.target_job_keywords:
            pytest.skip("配置里没有正向方向词表，无从核对")
        assert not any("标注" in str(k) for k in cfg.ai.target_job_keywords)


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
