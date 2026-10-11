# -*- coding: utf-8 -*-
"""自进化弹窗上"总回复数 911"和"明细只有 200 条"必须各说各的话。

2026-10-10 复盘弹窗时看到的对不上：卡片写 911，往下翻明细只有 200 条，
因为 _save_internal 把 reply_records 削成最近 200 条，而 total_replies 是
一个只增不减的累计计数。两个数都没错，错在界面上没交代口径，看上去像丢数据。
另外"待评估数 41"当时只是卡片上一个数字，明细里翻不到是哪 41 条——
数字看得见、东西看不见，等于没给。

这组测试锁三件事：报告里同时给出"存了多少条/上限多少/累计多少"，
未标注的那几条要能一条条列出来（已标注的不许混进来），
以及存盘确实按同一个上限削（口径别只写在注释里）。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.self_evolve import SelfEvolveEngine  # noqa: E402


def _引擎(tmp_path):
    return SelfEvolveEngine(config={"enabled": True},
                            data_file=str(tmp_path / "evolution_data.json"))


def _塞(eng, 会话, 文本, 几小时前=2.0, 已标注=False, 效果=None):
    eng._reply_records.append({
        "id": len(eng._reply_records) + 1,
        "reply_text": 文本, "chat_name": 会话, "job_name": "兼职·数据标注",
        "source": "ai", "intent": "other",
        "timestamp": (datetime.now() - timedelta(hours=几小时前)).isoformat(),
        "effect": 效果, "hr_response": None, "evaluated": 已标注,
    })


class 明细口径Test:
    def test_报告要同时给累计和明细条数(self, tmp_path):
        eng = _引擎(tmp_path)
        for i in range(30):
            _塞(eng, f"HR{i}", f"回复{i}", 已标注=True, 效果="neutral")
        eng._reply_stats["total_replies"] = 911
        rep = eng.get_evolution_report()
        assert rep["records_kept"] == 30, "报告没交代明细里现在到底有几条"
        assert rep["records_cap"] == eng.明细保留条数, "上限要从引擎说出去，不能让前端猜"
        assert rep["reply_stats"]["total_replies"] == 911

    def test_存盘按同一个上限削(self, tmp_path):
        eng = _引擎(tmp_path)
        for i in range(eng.明细保留条数 + 50):
            _塞(eng, f"HR{i}", f"回复{i}", 已标注=True, 效果="neutral")
        eng.save_evolution_data()
        import json
        存的 = json.loads((tmp_path / "evolution_data.json").read_text(encoding="utf-8"))
        recs = 存的["reply_records"]
        assert len(recs) == eng.明细保留条数, f"存了 {len(recs)} 条，上限是 {eng.明细保留条数}"
        assert recs[-1]["reply_text"] == f"回复{len(recs) + 49}", "削的要是最老的，留下的要是最远的"


class 未标注可见Test:
    def test_未标注的一条条列出来(self, tmp_path):
        eng = _引擎(tmp_path)
        _塞(eng, "甲女士", "甲的回复", 已标注=False)
        _塞(eng, "乙女士", "乙的回复", 已标注=True, 效果="positive")
        _塞(eng, "丙先生", "丙的回复", 已标注=False)
        rep = eng.get_evolution_report()
        列 = rep["pending_records"]
        assert [x["chat_name"] for x in 列] == ["丙先生", "甲女士"], \
            f"未标注的应当新在前、已标注的不许进来：{[x['chat_name'] for x in 列]}"
        assert 列[0]["reply_text"] == "丙的回复"

    def test_未标注多了要限量别把弹窗拖死(self, tmp_path):
        eng = _引擎(tmp_path)
        for i in range(300):
            _塞(eng, f"HR{i}", f"回复{i}")
        rep = eng.get_evolution_report()
        assert eng.get_pending_evaluations() == 300
        assert len(rep["pending_records"]) == rep["pending_records_limit"], \
            "列的要限量，但得让界面知道限量了——总数另给"
        assert rep["pending_total"] == 300

    def test_全标注完了明细是空的(self, tmp_path):
        eng = _引擎(tmp_path)
        _塞(eng, "甲女士", "甲的回复", 已标注=True, 效果="neutral")
        rep = eng.get_evolution_report()
        assert rep["pending_records"] == []
        assert rep["pending_total"] == 0
