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
from pathlib import Path

import pytest

from boss_bot.self_evolve import SelfEvolveEngine, _is_leaked_reply


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

class 纠正要能重跑Test:
    """"净化过一次"不能等于"永远不再净化"。

    `purify_records` 以前只挑 `not purified` 的记录，而引擎只在**账号启动**
    时跑它一次（main_loop:1515）。于是判据后来补的词、收紧的模式，对已经标过
    purified 的存量永远不生效——2026-10-16 那轮补了一批拒绝话术词表，盘上标签
    一个都没动，就是这个形状。测试以前靠"先把 purified 摘掉"绕开这个限制
    （见 test_真实数据能过purify 原来的写法），绕开限制不等于限制不存在。
    """

    def test_标过purified的脏标签下次还得重判(self, tmp_path):
        r = _record(1, "好的，感谢回复", "不好意思，不太合适哦", "positive", True)
        r["purified"] = True
        e = _engine(tmp_path, [r],
                    {"total_replies": 1, "positive": 1, "neutral": 0,
                     "negative": 0, "ignored": 0},
                    {"ai": {"used": 1, "positive": 1, "neutral": 0,
                            "negative": 0, "ignored": 0}})
        改 = e.purify_records()
        assert e._reply_records[0]["effect"] == "negative", "净化过一次就再也不看了"
        assert 改["reclassified"] == 1

    def test_全库都净化过时超时扫描仍然要跑(self, tmp_path):
        """`pending` 为空就整段 return，连超时未答那一遍也一起跳过了"""
        r = _record(2, "方便发一份简历吗", "", None, False, age_hours=30)
        r["purified"] = True
        e = _engine(tmp_path, [r],
                    {"total_replies": 1, "positive": 0, "neutral": 0,
                     "negative": 0, "ignored": 0},
                    {"ai": {"used": 1, "positive": 0, "neutral": 0,
                            "negative": 0, "ignored": 0}})
        改 = e.purify_records()
        assert 改["ignored"] == 1
        assert e._reply_records[0]["effect"] == "ignored"

    def test_零改动也要在日志里留一句话(self, tmp_path):
        """不然"跑过且干净"和"压根没跑"在日志里长得一模一样"""
        记 = []
        data_file = tmp_path / "evolution_data_log.json"
        data_file.write_text(json.dumps({
            "reply_stats": {"total_replies": 1, "positive": 0, "neutral": 0,
                            "negative": 1, "ignored": 0},
            "template_effectiveness": {}, "strategy_adjustments": [],
            "reply_records": [dict(_record(1, "好的", "不太合适哦", "negative", True),
                                   purified=True)],
        }, ensure_ascii=False), encoding="utf-8")
        e = SelfEvolveEngine(config={"enabled": True},
                             log_callback=lambda 行: 记.append(str(行)),
                             data_file=str(data_file))
        assert sum(e.purify_records().values()) == 0
        assert any("无改动" in m and "1" in m for m in 记), 记

    def test_闸门留痕不剔除也不扣统计(self, tmp_path):
        """发送闸门拦下的草稿只是留痕，从来没进过效果统计：
        对这种行做"剔除"会把没加过的数扣成负数"""
        r = _record(3, "我们只需要输出回复内容。根据对话历史……", "", None, True)
        r.update({"kind": "gate_block", "effect": None, "purified": True})
        e = _engine(tmp_path, [r],
                    {"total_replies": 0, "positive": 0, "neutral": 0,
                     "negative": 0, "ignored": 0},
                    {"ai": {"used": 0, "positive": 0, "neutral": 0,
                            "negative": 0, "ignored": 0}})
        改 = e.purify_records()
        assert not e._reply_records[0].get("excluded")
        assert e._reply_stats["total_replies"] == 0
        assert sum(改.values()) == 0

    def test_重跑仍然幂等(self, tmp_path):
        r = _record(1, "好的，感谢回复", "不好意思，不太合适哦", "positive", True)
        r["purified"] = True
        e = _engine(tmp_path, [r],
                    {"total_replies": 1, "positive": 1, "neutral": 0,
                     "negative": 0, "ignored": 0},
                    {"ai": {"used": 1, "positive": 1, "neutral": 0,
                            "negative": 0, "ignored": 0}})
        assert e.purify_records()["reclassified"] == 1
        第二遍 = e.purify_records()
        assert sum(第二遍.values()) == 0, "同一条被扣了两次统计"
        assert e._reply_stats == {"total_replies": 1, "positive": 0, "neutral": 0,
                                 "negative": 1, "ignored": 0}


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
        """生产文件要能过 purify：不炸、方向不越界、幂等、不改源文件。

        以前这里按 id 断言（by_id[1]、by_id[7]…），也断言"数据里必须有拒绝记录"。
        生产文件是一直往前滚的 200 条窗口，昨天的 id 今天就被挤出去了——
        测试会因为"账本换了"而红，跟被测代码对不对没关系。
        "拒绝话术必须判成 negative"这条方向性断言留在上面的合成用例里，那里数据是钉死的。

        读这份文件要带重试：线上引擎此刻就在写它（回复轮每条都落盘），
        一次撕裂读会让这个用例红得不讲道理（2026-10-17 全量跑就出现过一次
        "1 failed" 而单独跑怎么都复现不出来）。
        """
        import copy
        源文件 = Path("data/evolution_data_account_1.json")
        for 第几次 in range(6):
            try:
                原文 = 源文件.read_bytes()
                pristine = json.loads(原文.decode("utf-8"))
                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                if 第几次 == 5:
                    raise
                import time
                time.sleep(0.3)
        snapshot = tmp_path / "prod_snapshot.json"
        snapshot.write_bytes(原文)
        src = copy.deepcopy(pristine)
        # 生产文件里大多条已经标了 purified——正是以前"净化过一次就再也不看"留下的。
        # 现在 purify 每趟都重审全库，所以这里不许再把标记摘掉：摘掉就是在绕开被测的缺陷
        e = _engine(tmp_path, src["reply_records"], src["reply_stats"],
                    src["template_effectiveness"])
        e.purify_records()

        legal = {None, "positive", "neutral", "negative", "ignored"}
        odd = [(r.get("id"), r.get("effect")) for r in e._reply_records
               if r.get("effect") not in legal]
        assert not odd, f"purify 不该产出没定义过的标签：{odd[:5]}"
        # 闸门留痕（kind=gate_block）存的就是被拦下的泄漏草稿，它本来就没进统计，
        # "剔除"对它没有意义——只要求真发出去过的泄漏被剔掉
        leaked = [r for r in e._reply_records
                  if _is_leaked_reply(r.get("reply_text") or "")
                  and r.get("kind") != "gate_block"]
        assert all(r.get("excluded") for r in leaked), "泄漏思考过程的记录要被剔除出统计"

        again = e.purify_records()
        assert sum(again.values()) == 0, "purify 必须幂等：第二次不该再改任何东西"
        # purify 不动读进来的那份数据，归档写到引擎自己的目录
        assert json.load(open(snapshot, encoding="utf-8")) == pristine
