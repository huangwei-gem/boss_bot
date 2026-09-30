"""自净化：拒绝一律判 negative + 已读不回判 ignored + 泄漏思考过程剔除。

背景（2026-09-30 依据 data/evolution_data_account_1.json 真实记录）：
- #1/#4 HR 回「不好意思，不太合适哦」被记成 positive——「不太合适」不含连续子串
  「不合适」，却命中积极词表里的「合适」；
- #13 HR 回「祝您在BOSS直聘上找到更匹配的工作机会」、#20「暂不推进……再联系」
  都被记成 neutral；
- #7/#8/#9/#21/#50 的 reply_text 是泄漏的模型思考过程（「我们只需要输出回复内容」
  「Let me analyze」等），被当成正常回复参与模板统计；
- 只在 HR 回消息时才评估，HR 从不回的记录永远停在未评估，24 小时 ignored 分支
  永远走不到。
"""
import json
from datetime import datetime, timedelta

import pytest

from boss_bot.self_evolve import SelfEvolveEngine


def _engine(tmp_path, records=None, stats=None, templates=None):
    data_file = tmp_path / "evolution_data_test.json"
    data = {
        "reply_stats": stats or {"total_replies": 0, "positive": 0,
                                 "neutral": 0, "negative": 0, "ignored": 0},
        "template_effectiveness": templates or {},
        "strategy_adjustments": [],
        "reply_records": records or [],
        "last_saved": datetime.now().isoformat(),
    }
    data_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return SelfEvolveEngine(config={"enabled": True}, log_callback=lambda *a, **k: None,
                            data_file=str(data_file))


def _record(rid, reply, hr, effect, evaluated, source="ai", age_hours=1.0):
    ts = (datetime.now() - timedelta(hours=age_hours)).isoformat()
    return {"id": rid, "reply_text": reply, "chat_name": "测试", "job_name": "数据分析师",
            "source": source, "intent": "", "timestamp": ts, "effect": effect,
            "hr_response": hr, "evaluated": evaluated}


# ── 拒绝判 negative ──

class RejectionTest:
    def test_不太合适是拒绝不是积极(self, tmp_path):
        e = _engine(tmp_path)
        assert e.evaluate_reply_effectiveness("x", "不好意思，不太合适哦") == "negative"

    def test_更匹配的工作机会是拒绝(self, tmp_path):
        e = _engine(tmp_path)
        assert e.evaluate_reply_effectiveness(
            "x", "祝您在BOSS直聘上找到更匹配的工作机会") == "negative"

    def test_暂不推进存人才库是拒绝(self, tmp_path):
        e = _engine(tmp_path)
        assert e.evaluate_reply_effectiveness(
            "x", "感谢投递。经审慎评估，您的经历与职位匹配度稍低，暂不推进。"
                 "简历已存人才库，后续有机会再联系。祝顺利！") == "negative"

    def test_岗位移交他人是拒绝(self, tmp_path):
        e = _engine(tmp_path)
        assert e.evaluate_reply_effectiveness(
            "x", "因为目前实习生岗位的招聘目前已经移交给别的同事，"
                 "可以去公司主页找另一位王女士投递哦~") == "negative"

    def test_真实积极不受影响(self, tmp_path):
        e = _engine(tmp_path)
        assert e.evaluate_reply_effectiveness("x", "明天下午可以来面试吗？") == "positive"


# ── 已读不回 sweep ──

class SweepIgnoredTest:
    def test_超24小时未回复判ignored(self, tmp_path):
        r = _record(1, "方便发一份简历吗", "", None, False, age_hours=25)
        e = _engine(tmp_path, records=[r])
        changed = e.sweep_ignored_replies()
        assert changed >= 1
        loaded = e._reply_records[0]
        assert loaded["effect"] == "ignored"
        assert loaded["evaluated"] is True
        assert e._reply_stats["ignored"] == 1

    def test_等待窗口内不动(self, tmp_path):
        r = _record(1, "方便发一份简历吗", "", None, False, age_hours=2)
        e = _engine(tmp_path, records=[r])
        e.sweep_ignored_replies()
        assert e._reply_records[0]["evaluated"] is False

    def test_sweep是幂等的(self, tmp_path):
        r = _record(1, "方便发一份简历吗", "", None, False, age_hours=30)
        e = _engine(tmp_path, records=[r])
        e.sweep_ignored_replies()
        e.sweep_ignored_replies()
        assert e._reply_stats["ignored"] == 1


# ── 一次性纠正 ──

class PurifyTest:
    def _dirty_data(self):
        records = [
            _record(1, "工作日下午都可以安排面试，您看哪个时间段方便？",
                    "不好意思，不太合适哦", "positive", True),
            _record(2, "好的，感谢您抽出时间查看，祝工作顺利~",
                    "祝您在BOSS直聘上找到更匹配的工作机会", "neutral", True,
                    source="rejection"),
            _record(3, "我们只需要输出回复内容。根据对话历史，对方最初拒绝……",
                    "您好，麻烦发一份简历给我哈", "neutral", True),
            _record(4, "方便发一份简历吗", "", None, False, age_hours=30),
        ]
        stats = {"total_replies": 4, "positive": 1, "neutral": 2,
                 "negative": 0, "ignored": 0}
        templates = {"ai": {"used": 3, "positive": 1, "neutral": 1,
                            "negative": 0, "ignored": 0},
                     "rejection": {"used": 1, "positive": 0, "neutral": 1,
                                   "negative": 0, "ignored": 0}}
        return records, stats, templates

    def test_脏标签重判为negative(self, tmp_path):
        records, stats, templates = self._dirty_data()
        e = _engine(tmp_path, records, stats, templates)
        e.purify_records()
        by_id = {r["id"]: r for r in e._reply_records}
        assert by_id[1]["effect"] == "negative"
        assert by_id[2]["effect"] == "negative"

    def test_统计随重判迁移(self, tmp_path):
        records, stats, templates = self._dirty_data()
        e = _engine(tmp_path, records, stats, templates)
        e.purify_records()
        assert e._reply_stats["positive"] == 0
        assert e._reply_stats["neutral"] == 0
        assert e._reply_stats["negative"] == 2
        te = e._template_effectiveness
        assert te["ai"]["positive"] == 0
        assert te["ai"]["negative"] == 1
        assert te["rejection"]["negative"] == 1
        assert te["rejection"]["neutral"] == 0

    def test_泄漏思考过程被剔除出统计(self, tmp_path):
        records, stats, templates = self._dirty_data()
        e = _engine(tmp_path, records, stats, templates)
        e.purify_records()
        by_id = {r["id"]: r for r in e._reply_records}
        assert by_id[3].get("excluded") is True
        assert e._reply_stats["total_replies"] == 3
        assert e._reply_stats["neutral"] == 0
        assert e._template_effectiveness["ai"]["used"] == 2

    def test_超时未答补判ignored(self, tmp_path):
        records, stats, templates = self._dirty_data()
        e = _engine(tmp_path, records, stats, templates)
        e.purify_records()
        by_id = {r["id"]: r for r in e._reply_records}
        assert by_id[4]["effect"] == "ignored"
        assert e._reply_stats["ignored"] == 1

    def test_改写前先归档且只归档一次(self, tmp_path):
        records, stats, templates = self._dirty_data()
        e = _engine(tmp_path, records, stats, templates)
        e.purify_records()
        archives = list(tmp_path.glob("*archive*"))
        assert len(archives) == 1
        saved = json.loads(archives[0].read_text(encoding="utf-8"))
        assert saved["reply_records"][0]["effect"] == "positive"

    def test_重复运行是幂等的(self, tmp_path):
        records, stats, templates = self._dirty_data()
        e = _engine(tmp_path, records, stats, templates)
        first = e.purify_records()
        second = e.purify_records()
        assert sum(first.values()) > 0
        assert sum(second.values()) == 0
        assert e._reply_stats["negative"] == 2

    def test_真实数据能过purify(self, tmp_path):
        """用生产文件的真实脏记录跑一遍，确认纠正方向正确。"""
        src = json.load(open("data/evolution_data_account_1.json", encoding="utf-8"))
        e = _engine(tmp_path, src["reply_records"], src["reply_stats"],
                    src["template_effectiveness"])
        e.purify_records()
        by_id = {r["id"]: r for r in e._reply_records}
        assert by_id[1]["effect"] == "negative"
        assert by_id[4]["effect"] == "negative"
        assert by_id[13]["effect"] == "negative"
        assert by_id[20]["effect"] == "negative"
        assert by_id[7].get("excluded") is True
        assert by_id[8].get("excluded") is True
        assert by_id[9].get("excluded") is True
        assert by_id[21].get("excluded") is True
        assert by_id[50].get("excluded") is True
        # 原文件不动，归档在新目录
        original = json.load(
            open("data/evolution_data_account_1.json", encoding="utf-8"))
        assert original["reply_records"][0]["effect"] == "positive"
