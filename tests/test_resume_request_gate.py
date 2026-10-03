# -*- coding: utf-8 -*-
""""发简历"这个动作只能被真正的索要触发。

判据取自盘上真实数据：messages/*.json 里 105 个会话，按修复后的取话链回放，
有 26 条最新 HR 消息会命中 resume 动作（规则表是子串匹配，键就是"简历"两个字，
意图正则也有 r"简历.{0,4}(发|看|收|给)" 这种宽口子）。其中真在要简历的只有 11 条，
另外 15 条只是"提到"了简历：已收到、看过了、不匹配、内推链接、甚至是我们
自己发出去之后的系统确认卡。对这些回一句"发简历"是答非所问，点下去还会
真的把简历塞给一个刚刚拒绝过我们的 HR。

所以动作级要有一道闸门：命中 resume 之前先确认这句话真的在要简历。
"""
import pytest

from boss_bot.intent import is_resume_request

# 盘上真实存在、确实是在索要简历的说法（含 HR 点"求附件简历"生成的卡片）
REAL_ASKS = [
    "我想要一份您的附件简历，您是否同意 拒绝 同意",
    "可以发一份你的简历过来",
    "您好，方便发一份简历过来吗？",
    "方便发一份你的简历过来吗？",
    "请问现在在上海吗？是否有电商行业的影刀RPA经验？有的话麻烦发一份简历过来。",
    "27届暂未开放，可以先发我一份简历~",
    "您好，麻烦发一份简历给我哈",
    "您好，最近是否有考虑换工作，有空可以看下我们这边的岗位，要是有兴趣可以发份简历，",
    "方便发份简历看看吗",
    "简历发我一份",
    "可以发一下简历吗",
]

# 同样命中"简历"关键字，但一个字都没在要简历
NOT_ASKS = [
    "好滴。简历收到；如果合适会有通知",
    "简历已收到，感谢投递！后续流程我会主动跟进并及时告知你，请保持电话畅通～",
    "你好，简历会同步给人事部门初筛。此阶段需要1-3天，如果3天内没收到消息就是未通过",
    "您好，看了您的在线简历，感觉和我们的岗位匹配度很高，请问是否可以聊聊呢？",
    "你好，看过了你的简历，希望和你交流一下",
    "你好，我认真看了你的简历，觉得非常适合我们这边的27届实习生岗位 纯文职电商审核",
    "对方已同意，您的附件简历已发送给对方",
    "您的附件简历 黄维简历 已发送给Boss点击查看附件",
    "尽管我们对您的简历印象深刻，但是您的简历与我们的岗位要求有些差距",
    "您好，看您简历和招聘方需求不是很符合，您再看看其他岗位",
    "【拼多多27届校招正式批】官方内推链接： https://careers.pddjob.cn/campus",
]


class PredicateTest:

    @pytest.mark.parametrize("text", REAL_ASKS)
    def test_真在要简历的必须认出来(self, text):
        assert is_resume_request(text), f"漏判会少发一份简历: {text}"

    @pytest.mark.parametrize("text", NOT_ASKS)
    def test_只是提到简历的不能算索要(self, text):
        assert not is_resume_request(text), f"误判会把简历塞给没要的人: {text}"

    def test_空串不是索要(self):
        assert not is_resume_request("")


class ActionGateTest:
    """闸门接在决策链上：规则表和意图正则都不许绕过它发动作。"""

    def _engine(self):
        """真引擎，但把两处副作用掐掉：不外呼 AI、不落回复记录。

        规则表用线上那份的写法（键就是"简历"两个字的子串匹配），
        这样测的才是用户配置真实会走的那条直通路径。
        """
        from boss_bot.reply_engine import ReplyEngine
        from boss_bot.rules import RuleEngine
        e = ReplyEngine()
        e.rule_engine = RuleEngine({"简历": "send_resume", "发简历": "send_resume"})
        e._message_store = None
        e._self_evolve = None
        e._ask_ai = lambda *a, **k: None
        e._add_record = lambda **kw: None
        e._record_to_evolve = lambda *a, **k: None
        e._log_decision = lambda *a, **k: None
        return e

    def test_卡片照样发动作(self):
        action, _, meta = self._engine().get_reply(
            [{"is_mine": False, "text": "我想要一份您的附件简历，您是否同意 拒绝 同意"}])
        assert action == "resume"
        assert meta["source"] == "rule"

    def test_已收到简历不再触发发送(self):
        action, _, meta = self._engine().get_reply(
            [{"is_mine": False, "text": "好滴。简历收到；如果合适会有通知"}])
        assert action != "resume", "HR 说收到了还再发一次，是答非所问"

    def test_看过简历的开场白不再触发发送(self):
        action, _, meta = self._engine().get_reply(
            [{"is_mine": False, "text": "你好，看过了你的简历，希望和你交流一下"}])
        assert action != "resume"
        assert meta["intent"] != "ask_resume", "意图也被宽正则带偏了，要一起收敛"

    def test_系统确认卡不算索要(self):
        """我们发出去之后 BOSS 回的"您的附件简历已发送给对方"也是条消息，
        把它当成 HR 在要简历会形成自己催自己发的循环"""
        action, _, _ = self._engine().get_reply(
            [{"is_mine": False, "text": "对方已同意，您的附件简历已发送给对方"}])
        assert action != "resume"
