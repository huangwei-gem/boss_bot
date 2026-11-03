# -*- coding: utf-8 -*-
"""配置里手写的句子也要过发送闸门——规则直通与意图话术两条路以前没闸。

背景（本轮现测）：`reply_rejection` 只长在 AI 那一路（`_call_chat`）和招呼语那一路
（`greet_engine`）。`get_reply` 的前两条出口不是这样：

  1. 关键词规则直通：`rule_engine.match()` 命中的就是 bot_config 里 reply_rules 的原文，
     拿到之后直接 `return action, content`；
  2. 意图话术：`意图话术表()` 里的句子取自代码常量与 `getattr(config, 常量)`。

同一个族已经真金白银烫过一次：2026-10-23 查 messages/ 存档，手写的招呼语发出过
218 条「我是双一流的本科」——画像写的是「本科在读（统计学）」，长沙理工大学不在
双一流名单里，其中 13 条对面回的就是「不太合适」。这句话换成写在 reply_rules 里
一样会漏出去，因为那条路上根本没有闸。

反向也要守住：他现在这 17 条 reply_rules、9 条意图话术，一句都不该被拦。闸门要是
把「问经验就说有经验」这种他自己钉的口径静默吃掉，就违背「配置输入框必须真生效」。
"""
import pytest

import boss_bot.reply_engine as RE
from boss_bot.reply_engine import ReplyEngine, reply_rejection, 意图话术表
from boss_bot.rules import RuleEngine

# 真会被闸门拦下的两类句子（不靠 monkeypatch，判据就是线上那两条）
# 学历档次的谎话按盘上那份画像现挑：2026-10-29 用户把「双一流」写进了画像，
# 那句就不算谎话了；钉死任何一档都会变成"画像一改，这批门全绿却不再测东西"。
def _挑一句学历谎话() -> str:
    from boss_bot.prompts import current_profile
    p = current_profile() or {}
    写过 = str(p.get("education") or "") + " " + str(p.get("school") or "")
    for 档次 in ("985", "211", "C9", "双一流"):
        if 档次 not in 写过:
            return "我是%s的本科，想找数据分析的实习" % 档次
    raise AssertionError("画像把这几档全写上了，这条门要换判据，不是换句子")


谎句_学历 = _挑一句学历谎话()
# 理由里点名的档次跟着现挑的那一句走，钉死"双一流"会在画像改动后变成假绿
谎句_档次 = 谎句_学历[2:谎句_学历.index("的")]
谎句_承诺 = "好的，我加您微信了哈"


def _引擎(规则表=None):
    """真引擎，只掐掉外呼与落库：测的是决策链，不碰盘、不碰网。"""
    e = ReplyEngine()
    e.rule_engine = RuleEngine(规则表 or {})
    e._message_store = None
    e._self_evolve = None
    e._ask_ai = lambda *a, **k: None
    e._add_record = lambda **kw: None
    e._record_to_evolve = lambda *a, **k: None
    e._log_decision = lambda *a, **k: None
    return e


def _HR(文):
    return [{"is_mine": False, "text": 文}]


class Test规则直通要过闸:
    def test_规则命中的谎句不发(self):
        e = _引擎({"在吗": 谎句_学历})
        action, content, meta = e.get_reply(_HR("在吗"))
        assert content != 谎句_学历, "配置里这句谎话原样发出去了"
        assert meta["source"] != "rule"

    def test_拦下之后让给下一级而不是不回(self):
        """规则被拦不等于这轮闭嘴：往下还有意图和 AI 两级，AI 那一路自己带闸。

        用「天气」这种不落意图话术表的词，才看得到"一路让到 AI"，
        不然「在吗」会先被 greeting 那级接住。
        """
        e = _引擎({"天气": 谎句_承诺})
        e._ask_ai = lambda *a, **k: "在的，方便聊一下这个岗位吗？"
        action, content, meta = e.get_reply(_HR("今天天气不错啊"))
        assert action == "text"
        assert content == "在的，方便聊一下这个岗位吗？"
        assert meta["source"] == "ai"

    def test_拦下后落到意图那一级也算接住(self):
        """规则句被拦，意图话术能发就发意图——不是非得一路让到 AI。"""
        e = _引擎({"在吗": 谎句_承诺})
        action, content, meta = e.get_reply(_HR("在吗"))
        assert meta["source"] == "intent"
        assert content != 谎句_承诺

    def test_拦下的那句要留痕(self):
        记下的 = []
        e = _引擎({"在吗": 谎句_学历})
        e._record_gate_block = lambda reason, draft, **kw: 记下的.append((reason, draft))
        e.get_reply(_HR("在吗"))
        assert 记下的, "闸门拦下必须留痕，否则配置里的病句永远查不到"
        assert 谎句_学历 in 记下的[0][1]
        assert 谎句_档次 in 记下的[0][0]

    def test_一路让到底的那条记录要说真话(self):
        """规则句被拦、意图没命中、AI 没回——最后落进回复记录的那句理由必须写
        "配置里那句被闸门拦下"，不许写成"无规则/意图命中"。那是假原因：
        命中的，是拦下的。（用户 10-17：不合适的原因要准确）"""
        记下的 = []
        e = _引擎({"天气": 谎句_学历})
        e._add_record = lambda **kw: 记下的.append(kw)
        e._ask_ai = lambda *a, **k: None
        e._ai_fail_action = "skip"
        action, content, meta = e.get_reply(_HR("今天天气不错啊"))
        assert action == "none"
        条 = [r for r in 记下的 if r.get("is_skipped")][-1]
        assert "闸门" in 条["reply_reason"], 条["reply_reason"]
        assert 谎句_档次 in 条["reply_reason"]
        assert "闸门" in 条["skip_reason"]

    def test_干净的规则句照常直通(self):
        好句 = "有的，我做过标注和数据处理这类项目"
        assert reply_rejection(好句) == "", "前提：这句本身能发"
        e = _引擎({"经验": 好句})
        action, content, meta = e.get_reply(_HR("你有经验吗"))
        assert (action, content, meta["source"]) == ("text", 好句, "rule")

    def test_动作类不被文本闸挡住(self):
        """resume 这类动作 content 是 None，闸只管文字，不许顺手把动作也吞了。"""
        e = _引擎({"我想要一份您的附件简历": "send_resume"})
        action, _, meta = e.get_reply(
            _HR("我想要一份您的附件简历，您是否同意 拒绝 同意"))
        assert action == "resume"
        assert meta["source"] == "rule"


class Test意图话术要过闸:
    def test_意图话术是谎句时不发(self):
        e = _引擎()
        原表 = 意图话术表()
        表 = dict(原表)
        表["ask_experience"] = ("text", 谎句_学历)
        monkey = pytest.MonkeyPatch()
        monkey.setattr(RE, "意图话术表", lambda: 表)
        try:
            action, content, meta = e.get_reply(_HR("你有经验吗"))
        finally:
            monkey.undo()
        assert content != 谎句_学历
        assert meta["source"] != "intent"

    def test_意图话术能发时照常直通(self):
        e = _引擎()
        action, content, meta = e.get_reply(_HR("你有经验吗"))
        assert meta["source"] == "intent"
        assert content == 意图话术表()["ask_experience"][1]

    def test_没有正文的动作不被文本闸吞掉(self):
        """ask_experience 这类带句子，ask_resume 只有动作、content 是 None。
        闸只管文字；把 None 也判成"不能发"，索要简历就没人接了。
        规则表换成一句都匹配不上的键，才看得到意图这一级（默认表里"简历"会先直通）。"""
        e = _引擎({"这句话绝不可能出现在测试里": "占位"})
        action, content, meta = e.get_reply(_HR("方便发一份简历过来吗"))
        assert meta["source"] == "intent"
        assert action == "resume"
        assert content is None


class Test兜底话术要过闸:
    """第 4 级那条兜底句也是配置原文（面板「默认回复」可改、热重载生效）。
    这一级下面没有下一级可让，拦下就本轮不回——编不出合规的一句，就不发。"""

    def _引擎(self, monkeypatch, 兜底句):
        monkeypatch.setattr(RE.config, "DEFAULT_REPLY", 兜底句)
        e = _引擎({"这句话绝不可能出现在测试里": "占位"})
        e._ai_fail_action = "default"
        e._ask_ai = lambda *a, **k: None
        return e

    def test_兜底句是谎句时不发(self, monkeypatch):
        e = self._引擎(monkeypatch, 谎句_学历)
        action, content, meta = e.get_reply(_HR("今天天气不错啊"))
        assert action == "none", "谎句当兜底发出去，等于每轮 AI 挂了都发一遍"
        assert content is None

    def test_干净的兜底句照常发(self, monkeypatch):
        e = self._引擎(monkeypatch, "好的，我稍后整理一下发您～")
        action, content, meta = e.get_reply(_HR("今天天气不错啊"))
        assert (action, content, meta["source"]) == (
            "text", "好的，我稍后整理一下发您～", "default")

    def test_现配置那句兜底话术本来就能发(self, monkeypatch):
        """反向哨兵：这条门不该让今天的行为变样。"""
        e = self._引擎(monkeypatch, RE.config.DEFAULT_REPLY)
        action, content, meta = e.get_reply(_HR("今天天气不错啊"))
        assert action == "text"
        assert content == RE.config.DEFAULT_REPLY

    def test_兜底被拦时那条记录要点出是哪一栏(self, monkeypatch):
        """跳过理由要写"兜底话术 DEFAULT_REPLY 被拦"，不能只写"没命中"——
        他看记录是要知道去面板哪一格改，不是想知道本轮没发。"""
        记下的 = []
        e = self._引擎(monkeypatch, 谎句_学历)
        e._add_record = lambda **kw: 记下的.append(kw)
        action, content, meta = e.get_reply(_HR("今天天气不错啊"))
        assert action == "none"
        条 = [r for r in 记下的 if r.get("is_skipped")][-1]
        assert "DEFAULT_REPLY" in 条["reply_reason"], 条["reply_reason"]
        assert 谎句_档次 in 条["reply_reason"]


class Test闸门不许反咬自己的配置:
    """这三条是"门能真的失败"的另一面：现配置一句都不许被拦。

    谁往 reply_rules / 招呼语常量里写进一句谎话，这里会红——那时候该改的是那句
    配置（用户自己钉的口径要他点头），不是把闸门关掉。
    """

    def test_他配的每条规则话术都能过闸(self):
        规则 = RuleEngine().rules
        assert len(规则) >= 10, "读到 %d 条，不像线上那份 reply_rules" % len(规则)
        拦下的 = [句 for 句 in 规则.values()
                  if isinstance(句, str) and reply_rejection(句)]
        assert 拦下的 == [], "配置里出现了不能发的句子：%s" % 拦下的

    def test_九条意图话术都能过闸(self):
        拦下的 = [(意图, 句) for 意图, (动作, 句) in 意图话术表().items()
                  if 句 and reply_rejection(句)]
        assert 拦下的 == [], "意图话术里出现了不能发的句子：%s" % 拦下的


class Test闸门的边界:
    def test_判据本身坏了不许憋死回复(self):
        """reply_rejection 抛错时按"能发"处理，和招呼语那一路一致：
        闸门坏了不该让所有回复都发不出去，坏了有 logger 看得见。"""
        monkey = pytest.MonkeyPatch()
        monkey.setattr(RE, "reply_rejection", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("坏了")))
        e = _引擎({"在吗": "在的，方便聊一下吗？"})
        try:
            action, content, meta = e.get_reply(_HR("在吗"))
        finally:
            monkey.undo()
        assert content == "在的，方便聊一下吗？"
        assert meta["source"] == "rule"

    def test_AI那一路的闸没被挪走(self):
        """这轮是把闸补到配置直通路上，不是把 AI 那一路的闸搬走。"""
        源 = open("boss_bot/reply_engine.py", encoding="utf-8").read()
        正文 = 源[:源.index("    def _call_chat")]
        尾部 = 源[源.index("    def _call_chat"):]
        assert "why = reply_rejection(text)" in 尾部
        assert 正文.count("self._配置句过闸(") >= 3, "三条配置直通路都要接闸"

    def test_过闸函数只在一处实现(self):
        """判据只留一份：配置路和 AI 路都调同一个 reply_rejection，
        谁另写一套关键词表，两边就会开始说不一样的话。"""
        源 = open("boss_bot/reply_engine.py", encoding="utf-8").read()
        assert 源.count("def _配置句过闸") == 1
        assert 源.count("why = reply_rejection(句)") == 1
