# -*- coding: utf-8 -*-
"""被 HR 拒绝时要当场追问原因，并把原因回流到投递侧。

用户口径（2026-11-03，第二次说，带情绪）："碰到拒绝的你要追问原因啊，之前就说让你
追问原因，然后自调整优化投递策略。"

现算的账（改之前）：data/reply_records.json 里 2026-11-03 一整天
reply_source=rejection 真发出去 5 条，正文全是"祝您招聘顺利~"这类收尾，
问原因的 0 条；另有 61 条 skip/rejection。也就是说这条腿从来没对 HR 开口过——
2026-09-29 那版我把口径定成了"只问 AI 不问 HR"，问的是模型不是人。

这几条门钉的是：
① 拒绝当场真带上一句请教（不是只写在提示词里）；
② 每段会话只问一次，"已招满"这种状态类不问；
③ HR 答的那句落进 decline_reason 与 data/reject_reasons.json；
④ 回流：这家 HR 亲口拒过 → 它的岗位 14 天内不再进池，超窗放开。
"""
import json
from datetime import datetime, timedelta

import pytest

import boss_bot.reject_reason as RR
from boss_bot.reply_engine import ReplyEngine, conversation_rejected
from boss_bot.rules import RuleEngine

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
问过的那句 = "好的，理解～方便请教一下这次主要是差在哪一条吗？我好知道接下来该补什么。"


def _引擎():
    e = ReplyEngine()
    e.rule_engine = RuleEngine({})
    e._message_store = None
    e._self_evolve = None
    e._ask_ai = lambda *a, **k: None
    e._record_to_evolve = lambda *a, **k: None
    e._log_decision = lambda *a, **k: None
    return e


def _对话(*项):
    out = []
    for 是我, 文 in 项:
        out.append({"is_mine": bool(是我), "text": 文,
                    "time": "2026-11-03 10:00:00"})
    return out


@pytest.fixture()
def 路(tmp_path, monkeypatch):
    """每条门各自一份文件，别往 data/ 里写。"""
    文件 = tmp_path / "reject_reasons.json"
    monkeypatch.setattr(RR, "DEFAULT_PATH", 文件)
    monkeypatch.setattr(RR, "_缓存", {})
    yield 文件
    monkeypatch.setattr(RR, "_缓存", {})


@pytest.fixture(autouse=True)
def _台账落在临时文件(路):
    """不点名也要隔开：只读判据的那几条门同样不许碰 data/。"""
    return 路


class Test拒绝当场问原因:
    def test_拒绝那句真的带上了请教(self):
        e = _引擎()
        包 = []
        e._add_record = lambda **kw: 包.append(kw)
        action, content, meta = e.get_reply(
            _对话((False, "不好意思，你和我们的岗位不太合适")), company="长沙某某科技")
        assert action == "text"
        assert content, "这一条什么都没发"
        assert any(k in content for k in RR.请教说法), \
            "只回了客套收尾，没问原因：%s" % content
        assert meta["source"] == "rejection"
        assert 包 and 包[0]["reply_content"] == content

    def test_每段会话只问一次(self):
        e = _引擎()
        对话 = _对话((False, "不太合适"), (True, 问过的那句), (False, "祝您顺利"))
        action, content, meta = e.get_reply(对话)
        if action == "text":
            assert not any(k in (content or "") for k in RR.请教说法), \
                "这句又问了一遍：%s" % content

    def test_已招满这种状态不问(self):
        """问「差在哪一条」的对象是评价，岗位没了就没有对象。"""
        assert RR.该问原因("不好意思，这个岗位已经招满了") is False
        assert RR.该问原因("你的经验不太够") is True
        e = _引擎()
        action, content, meta = e.get_reply(
            _对话((False, "这个岗位已招满了，谢谢你")))
        assert not any(k in (content or "") for k in RR.请教说法)

    def test_判据不是恒真没问过就该判成没问过(self):
        """反向哨兵：已问过 要是恒为真，上面那条「只问一次」会永远绿。"""
        assert RR.已问过([]) is False
        assert RR.已问过(_对话((True, "好的，祝您招聘顺利~"))) is False
        assert RR.已问过(_对话((True, 问过的那句))) is True


class TestHR答的原因要落库:
    def test_收下原因这一轮不再回话(self):
        e = _引擎()
        包 = []
        e._add_record = lambda **kw: 包.append(kw)
        对话 = _对话((False, "不太合适"), (True, 问过的那句),
                     (False, "你缺个三年 SQL 经验，我们这岗要熟手的"))
        action, content, meta = e.get_reply(对话, company="长沙某某科技",
                                            job_name="数据分析师")
        assert action == "none", "把 HR 的回答又当成一次拒绝回了出去"
        assert meta["source"] == "reject_reason"
        assert 包 and 包[0]["decline_reason"], "原因没落进 decline_reason 这一列"
        assert "SQL" in 包[0]["decline_reason"]

    def test_落进台账且同一句不记两遍(self, 路):
        第一遍 = RR.记一条("要三年 SQL 经验", company="长沙某某科技",
                          job_name="数据分析师", account_index=1)
        assert 第一遍 is True
        assert 路.exists() and "SQL" in 路.read_text(encoding="utf-8")
        assert RR.记一条("要三年 SQL 经验", company="长沙某某科技") is False
        assert len(json.loads(路.read_text(encoding="utf-8"))["条目"]) == 1, \
            "同一家同一句话被记了两条，日志会越滚越厚"

    def test_邀约不是原因(self):
        """HR 回「其实可以来聊聊」是往下谈，存成拒因等于自己掐自己。"""
        assert RR.是原因回答("要不你明天来公司聊聊") is False
        assert RR.是原因回答("我们要三年以上的熟手") is True
        assert RR.是原因回答("好的") is False

    def test_只认紧邻那一问(self):
        """捕获判据恒真会把后面随便一句都存成拒因——这里要它判不出。"""
        assert RR.刚问过(_对话((False, "不太合适"))) is False
        assert RR.刚问过(_对话((False, "不太合适"), (True, 问过的那句))) is False, \
            "我方才是最后一句，没人回答，不该捕"
        assert RR.刚问过(_对话((False, "不太合适"), (True, 问过的那句),
                              (False, "你经验不够"))) is True


class Test原因回流到投递侧:
    def test_刚拒过的公司不再进池(self, 路):
        RR.记一条("我们要的是有三年经验的", company="A公司", account_index=0)
        可投 = [{"company": "A公司", "job_name": "数据分析师", "url": "u1"},
               {"company": "B公司", "job_name": "AI产品经理", "url": "u2"}]
        剩下, 挡 = RR.筛掉刚拒过的(可投)
        assert [j["company"] for j in 剩下] == ["B公司"]
        assert 挡 == {"A公司": "我们要的是有三年经验的"}, "日志要能报出是哪一家、为什么"

    def test_超出窗口就放开(self, 路):
        """这条钉的是"挡"不是"永久拉黑"：14 天前拒过的，第 15 天要能再进池。"""
        久前 = (datetime.now() - timedelta(days=RR.RECENT_DAYS + 1)).strftime(TIME_FORMAT)
        路.write_text(json.dumps({"条目": [{"at": 久前, "account_index": 0,
                                   "company": "A公司", "reason": "经验不够"}]},
                                 ensure_ascii=False), encoding="utf-8")
        RR._缓存.clear()
        剩下, 挡 = RR.筛掉刚拒过的([{"company": "A公司", "url": "u1"}])
        assert 挡 == {}, "过期那条还在挡，等于永久拉黑这家公司"
        assert len(剩下) == 1

    def test_记一条之后缓存立刻失效(self, 路):
        RR.记一条("要硕士", company="C公司")
        assert RR.这家拒过("C公司").get("reason") == "要硕士"

    def test_没原因的公司一律放行(self):
        可投 = [{"company": "", "url": "u1"}, {"url": "u2"}]
        剩下, 挡 = RR.筛掉刚拒过的(可投)
        assert 挡 == {} and len(剩下) == 2


class Test口径同源:
    def test_提示词不再禁问原因(self):
        """prompts.py 里那句「别追问」是这条腿以前不出声的另一半原因。"""
        from boss_bot import prompts
        源 = (prompts.__file__.replace("\\", "/"))
        文 = open(源, encoding="utf-8").read()
        assert "别追问" not in 文, "提示词还在教模型不许问原因"
        assert "差在哪一条" in 文

    def test_发送出去的那句过形式闸(self):
        """请教话术是代码写死的，也得走同一条长度/痕迹判据，别只测 AI 那一路。"""
        from boss_bot.reply_engine import _format_rejection
        for 句 in RR.请教话术池:
            assert _format_rejection(句) == "", 句

    def test_对话以拒绝收尾这条判据还在用(self):
        """捕获分支依赖 conversation_rejected，它要是改坏了这里要红。"""
        assert conversation_rejected(_对话((False, "不太合适"))) is True
        assert conversation_rejected(_对话((False, "今天天气不错"))) is False
