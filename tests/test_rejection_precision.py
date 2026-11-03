# -*- coding: utf-8 -*-
"""「HR 拒绝」这条判据的准确率——判错的代价不是话说错，是机器自己放弃一单。

命中 REJECTION_PATTERNS 之后，reply_engine 不调 AI、直接回一句"祝您招聘顺利"，
main_loop 还会把这段会话标成"HR已拒绝"从此不再推销（#232 那条口径）。
所以判据宁可窄，也不能把还在谈的当成已拒。

判据用的原话全部取自盘上现算（.scratch/拒绝词表现算.py，2026-11-03：
14 天里 362 条去重 HR 消息，7 条判拒，逐条抽出来看过——其中 2 条根本不是拒绝）：
- 「不露脸语音直播…可以加个微信先了解再考虑！」——HR 在拉人加微信；
- 「陈一鸣您好，岗位若不匹配也理解，只找线上远程兼职，方便告知是否有合适机会吗？」——假设句 + 在问情况。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.reply_engine import _is_rejection, conversation_rejected  # noqa: E402

# 盘上真出现过的拒绝原话
真拒绝 = [
    "我们认真审阅了您的简历，但暂不完全匹配岗位情况。祝您在BOSS直聘找到更合适的机会！",
    "我们非常感谢您的投递，但是很遗憾，我们的职位需要一些与您不完全匹配的技能和经验。",
    "很高兴收到您的应聘，但您的技能组合和我们目前项目的需求不完全吻合。",
    "不合适",
    "您好，看您简历和招聘方需求不是很符合，您再看看其他岗位",  # 缓和词但整句没在拉人
    "这个岗位已招满，后面有坑位再联系",
]

# 长得像拒绝、其实不是拒绝的盘上原话
不算拒绝 = [
    "不露脸语音直播时间自由日་结的，可以加个微信先了解再考虑！",
    "陈一鸣您好，岗位若不匹配也理解，只找线上远程兼职，方便告知是否有合适机会吗？",
    "hi～咱还考虑主播岗位吗？点击卡片加我企微就能详聊哈，期待和你沟通呀～",
    "后续简历通过会有邮件约时间",
]


class 拒绝判据Test:
    def test_真拒绝一条都不许漏(self):
        for 文 in 真拒绝:
            assert _is_rejection(文), "这条是真拒绝，漏了会一直追着人家发消息：%s" % 文[:30]

    def test_还在拉人的句子不许判成拒绝(self):
        for 文 in 不算拒绝:
            assert not _is_rejection(文), \
                "判成拒绝会让机器回一句祝您招聘顺利并永远放弃这一单：%s" % 文[:40]

    def test_会话收尾是拉人句子时不再判已拒(self):
        """conversation_rejected 决定这条会话以后还追不追——同源判据，不能各写一套。"""
        对话 = [
            {"is_mine": True, "type": "text", "text": "您好，我对这个岗位很感兴趣"},
            {"is_mine": False, "type": "text", "text": 不算拒绝[0]},
        ]
        assert not conversation_rejected(对话)
        对话[-1]["text"] = 真拒绝[0]
        assert conversation_rejected(对话), "真拒绝收尾时这条得翻成红，否则上面的门是摆设"

    def test_判据改动只影响形状不影响老口径(self):
        """空消息、纯客套不许命中；拒绝词表本身还在（撤词会让一批真拒漏掉）。"""
        from boss_bot.reply_engine import REJECTION_PATTERNS
        assert not _is_rejection("")
        assert not _is_rejection("好的")
        assert "不完全匹配" in REJECTION_PATTERNS and "已招满" in REJECTION_PATTERNS
