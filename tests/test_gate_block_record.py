"""发送闸门（输出校验）拦下的草稿要留痕可见。

背景：2026-09-29~30 有 7 条思考过程真发了出去，之后加了 reply_rejection 闸门，
但被闸门拦下/空正文换接口的事件只写进日志，自进化面板完全看不到。
"""
import json
from datetime import datetime, timedelta

import pytest

from boss_bot.self_evolve import SelfEvolveEngine


def _engine(tmp_path):
    data_file = tmp_path / "evolution_data_test.json"
    return SelfEvolveEngine(config={"enabled": True},
                            log_callback=lambda *a, **k: None,
                            data_file=str(data_file))


class RecordGateBlockTest:
    def test_留痕不改效果统计(self, tmp_path):
        e = _engine(tmp_path)
        e.record_gate_block("回复内容不像一句回复（像思考过程）",
                            "首先，分析对话上下文：对方最初拒绝……",
                            {"chat_name": "张三", "job_name": "数据分析"})
        assert len(e._reply_records) == 1
        r = e._reply_records[0]
        assert r["kind"] == "gate_block"
        assert "思考过程" in r["note"]
        assert r["evaluated"] is True
        assert e._reply_stats["total_replies"] == 0

    def test_留痕不会被超时扫描判成ignored(self, tmp_path):
        e = _engine(tmp_path)
        e.record_gate_block("接口没有回复正文", "……", {})
        e._reply_records[0]["timestamp"] = (
            datetime.now() - timedelta(hours=30)).isoformat()
        e.sweep_ignored_replies()
        assert e._reply_records[0]["effect"] is None
        assert e._reply_stats["ignored"] == 0

    def test_报告里可见(self, tmp_path):
        e = _engine(tmp_path)
        e.record_gate_block("回复内容不像一句回复（像 markdown）", "| 表 | 格 |", {})
        report = e.get_evolution_report()
        assert report["gate_blocks"] == 1
        assert report["recent_records"][0]["kind"] == "gate_block"

    def test_留痕落盘可重载(self, tmp_path):
        df = tmp_path / "evolution_data_test.json"
        e = SelfEvolveEngine(config={"enabled": True},
                             log_callback=lambda *a, **k: None,
                             data_file=str(df))
        e.record_gate_block("测试原因", "草稿", {})
        e2 = SelfEvolveEngine(config={"enabled": True},
                              log_callback=lambda *a, **k: None,
                              data_file=str(df))
        assert e2.get_evolution_report()["gate_blocks"] == 1
        assert e2._reply_records[0]["kind"] == "gate_block"


class ReplyEngineHookTest:
    def test_闸门拦截处调用了留痕(self):
        src = open("boss_bot/reply_engine.py", encoding="utf-8").read()
        assert "record_gate_block" in src, \
            "发送闸门拦下草稿时必须调用 record_gate_block 留痕"
