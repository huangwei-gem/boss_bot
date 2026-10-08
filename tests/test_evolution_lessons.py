# -*- coding: utf-8 -*-
"""经验沉淀（lessons）：把 Continual Harness 的思路融进自进化。

三条原则（来自 Prime Agent 的 /refine 设计与它踩过的"奖励作弊"坑）：
1. 小范围更新 —— 经验只做提示补充追加，永不重写基础提示词；
2. 质量门槛 —— 单次侥幸不能沉淀成规则，证据 ≥2 次才准入库，连负两次自动退役；
3. 快照回滚 —— 任何沉淀动作之前留快照，错了能退回去。
"""
from types import SimpleNamespace

import pytest

from boss_bot.self_evolve import (
    LESSON_MAX_ACTIVE,
    LESSON_MIN_EVIDENCE,
    LESSON_RETIRE_NEGATIVE,
    SelfEvolveEngine,
)


def _engine(tmp_path, enabled=True):
    data_file = tmp_path / "evolution_data_test.json"
    return SelfEvolveEngine(config={"enabled": enabled},
                            log_callback=lambda *a, **k: None,
                            data_file=str(data_file))


class LessonStoreTest:
    def test_证据不足不许沉淀(self, tmp_path):
        e = _engine(tmp_path)
        with pytest.raises(ValueError):
            e.add_lesson("回复要短", evidence_count=LESSON_MIN_EVIDENCE - 1)

    def test_空文本不许沉淀(self, tmp_path):
        e = _engine(tmp_path)
        with pytest.raises(ValueError):
            e.add_lesson("  ", evidence_count=3)

    def test_入库并带证据字段(self, tmp_path):
        e = _engine(tmp_path)
        l = e.add_lesson("回复控制在两句话内", trigger="闸门拦截复盘",
                         evidence_count=3, samples=["草稿一", "草稿二"],
                         source="refine")
        assert l["created"] is True
        assert l["status"] == "active"
        assert l["evidence_count"] == 3
        assert len(e._lessons) == 1

    def test_同文本去重不重复入库(self, tmp_path):
        e = _engine(tmp_path)
        e.add_lesson("回复要短", evidence_count=2)
        l = e.add_lesson("回复要短", evidence_count=2)
        assert l["created"] is False
        assert len(e._lessons) == 1

    def test_在库经验最多5条(self, tmp_path):
        e = _engine(tmp_path)
        for i in range(LESSON_MAX_ACTIVE):
            e.add_lesson(f"经验{i}", evidence_count=2)
        with pytest.raises(ValueError):
            e.add_lesson("第六条", evidence_count=2)


class LessonEffectTest:
    def test_连负两次自动退役(self, tmp_path):
        e = _engine(tmp_path)
        l = e.add_lesson("这条经验可能是错的", evidence_count=2)
        e.confirm_lesson(l["id"], "negative")
        l2 = e.confirm_lesson(l["id"], "negative")
        assert l2["status"] == "retired"
        assert "退役" in (l2.get("retire_reason") or "")

    def test_正效果只累计不退役(self, tmp_path):
        e = _engine(tmp_path)
        l = e.add_lesson("回复要短", evidence_count=2)
        for _ in range(3):
            l = e.confirm_lesson(l["id"], "positive")
        assert l["status"] == "active"
        assert l["effect_positive"] == 3

    def test_退役后不再进提示(self, tmp_path):
        e = _engine(tmp_path)
        l = e.add_lesson("回复要短", evidence_count=2)
        e.retire_lesson(l["id"], "人工判断不对")
        assert e.get_prompt_supplement() == ""

    def test_未知id与未知效果报错(self, tmp_path):
        e = _engine(tmp_path)
        with pytest.raises(ValueError):
            e.confirm_lesson(999, "positive")
        l = e.add_lesson("x", evidence_count=2)
        with pytest.raises(ValueError):
            e.confirm_lesson(l["id"], "magic")


class PromptSupplementTest:
    def test_有经验时输出补充块(self, tmp_path):
        e = _engine(tmp_path)
        e.add_lesson("回复控制在两句话内", evidence_count=2)
        s = e.get_prompt_supplement()
        assert "沉淀经验" in s
        assert "回复控制在两句话内" in s

    def test_没经验时空串(self, tmp_path):
        e = _engine(tmp_path)
        assert e.get_prompt_supplement() == ""

    def test_自进化关闭时空串(self, tmp_path):
        e = _engine(tmp_path, enabled=False)
        e.add_lesson("回复要短", evidence_count=2)
        assert e.get_prompt_supplement() == ""


class RefineTest:
    def test_只差数字的同一件拦截要算同一类(self, tmp_path):
        """线上实测：闸门累计拦了 169 条，经验条却是 0 条。

        根因在这里——分组的键是整句原文，而那句里带着「思考过程写了 2116 字」这种
        每次都不同的数字，于是每一条拦截都是"一个新的模式"，永远凑不够证据数，
        自进化等于从没跑过。
        """
        e = _engine(tmp_path)
        for n in (2116, 1943, 2030):
            e.record_gate_block(
                f"这句不能发给 HR（接口没有回复正文，思考过程写了 {n} 字）", "草稿", {})
        out = e.refine_lessons()
        assert out["created"] == 1, f"同一件事被拆成 {out['patterns_seen']} 类：{out}"
        lesson = [x for x in e._lessons if x["status"] == "active"][0]
        assert lesson["evidence_count"] == 3, lesson
        assert "接口没有回复正文" in lesson["text"], lesson["text"]
    def test_同因闸门拦截两次才沉淀(self, tmp_path):
        e = _engine(tmp_path)
        e.record_gate_block("回复内容不像一句回复（像思考过程）", "草稿甲", {})
        out = e.refine_lessons()
        assert out["created"] == 0, "单次拦截是侥幸，不能沉淀成规则"
        e.record_gate_block("回复内容不像一句回复（像思考过程）", "草稿乙", {})
        out = e.refine_lessons()
        assert out["created"] == 1
        l = [x for x in e._lessons if x["status"] == "active"][0]
        assert "思考过程" in l["text"]
        assert l["evidence_count"] == 2
        assert l["source"] == "refine"

    def test_重复复盘不会叠加同一条(self, tmp_path):
        e = _engine(tmp_path)
        e.record_gate_block("接口没有回复正文", "草稿", {})
        e.record_gate_block("接口没有回复正文", "草稿2", {})
        e.refine_lessons()
        out = e.refine_lessons()
        assert out["created"] == 0

    def test_复盘前留快照(self, tmp_path):
        e = _engine(tmp_path)
        e.record_gate_block("接口没有回复正文", "草稿", {})
        e.record_gate_block("接口没有回复正文", "草稿2", {})
        e.refine_lessons()
        snaps = list((tmp_path / "evolution_snapshots").glob("*.json"))
        assert snaps, "沉淀之前必须留快照"


class SnapshotRollbackTest:
    def test_快照与回滚往返(self, tmp_path):
        e = _engine(tmp_path)
        e.record_gate_block("原因A", "草稿1", {})
        e.snapshot_evolution_data("测试留档")
        e.record_gate_block("原因B", "草稿2", {})
        assert len(e._reply_records) == 2
        assert e.rollback_evolution_data() is True
        assert len(e._reply_records) == 1

    def test_没有快照时回滚失败(self, tmp_path):
        e = _engine(tmp_path)
        assert e.rollback_evolution_data() is False

    def test_快照只保留最近5份(self, tmp_path):
        import time
        e = _engine(tmp_path)
        for i in range(8):
            e.record_gate_block(f"原因{i}", "草稿", {})
            e.snapshot_evolution_data(f"第{i}次")
            time.sleep(0.01)
        snaps = sorted((tmp_path / "evolution_snapshots").glob("*.json"))
        assert len(snaps) == 5


class PersistenceTest:
    def test_经验落盘重载不丢(self, tmp_path):
        df = tmp_path / "evolution_data_test.json"
        e = SelfEvolveEngine(config={"enabled": True},
                             log_callback=lambda *a, **k: None, data_file=str(df))
        e.add_lesson("跨会话要还在", evidence_count=2)
        e2 = SelfEvolveEngine(config={"enabled": True},
                              log_callback=lambda *a, **k: None, data_file=str(df))
        assert any(x["text"] == "跨会话要还在" for x in e2._lessons)

    def test_报告里有经验区块(self, tmp_path):
        e = _engine(tmp_path)
        e.add_lesson("回复要短", evidence_count=2)
        report = e.get_evolution_report()
        assert report["lessons"]["active"][0]["text"] == "回复要短"


class InjectionTest:
    def test_经验以增量追加不重写基础提示词(self, tmp_path):
        """Continual Harness 原则：/refine 不重写基础系统提示，只做小范围补充。"""
        import boss_bot.reply_engine as re_mod
        e = _engine(tmp_path)
        e.add_lesson("回复控制在两句话内", evidence_count=2)
        eng = re_mod.ReplyEngine(self_evolve=e)
        saved = re_mod.build_system_prompt
        re_mod.build_system_prompt = lambda profile=None: "基础提示词"
        try:
            fake = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **kw: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(
                            content="您好，方便看看机会吗？", reasoning_content=None))]))))
            eng._call_chat(fake, "m", "你好", "张三", "数据分析", [])
            assert eng._last_ai_system_prompt.startswith("基础提示词"), \
                "基础提示词必须原样保留在开头"
            assert "沉淀经验" in eng._last_ai_system_prompt
            assert "回复控制在两句话内" in eng._last_ai_system_prompt
        finally:
            re_mod.build_system_prompt = saved

    def test_没经验时提示词与基础完全一致(self, tmp_path):
        import boss_bot.reply_engine as re_mod
        e = _engine(tmp_path)
        eng = re_mod.ReplyEngine(self_evolve=e)
        saved = re_mod.build_system_prompt
        re_mod.build_system_prompt = lambda profile=None: "基础提示词"
        try:
            fake = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **kw: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(
                            content="您好，方便看看机会吗？", reasoning_content=None))]))))
            eng._call_chat(fake, "m", "你好", "张三", "数据分析", [])
            assert eng._last_ai_system_prompt == "基础提示词"
        finally:
            re_mod.build_system_prompt = saved
