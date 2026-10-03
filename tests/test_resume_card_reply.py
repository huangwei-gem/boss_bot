# -*- coding: utf-8 -*-
"""HR 发"要附件简历"的卡片时，必须真的把简历发出去，不是回一句稍后。

实测 2026-10-02 23:43（账号2 / 江女士 / 孤波）：BOSS 那张
「我想要一份您的附件简历，您是否同意」的卡片，正文按口径存在 card_text、
text 是空的；而决策层 _split_messages 只认 text，于是"对方最新说了什么"
成了空串 —— 规则（默认表里"简历"→send_resume）和意图全部跳过，AI 对着
空气回了一句"好的，我稍后把简历整理好发给您，感谢您的关注。"
HR 等的是简历，这句话等于没办事。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.reply_engine import ReplyEngine  # noqa: E402

CARD = "我想要一份您的附件简历，您是否同意 拒绝 同意"


def _hr(text="", card_text="", **kw):
    row = {"is_mine": False, "text": text, "time": "23:40"}
    if card_text:
        row["card_text"] = card_text
        row["kind"] = "card"
    row.update(kw)
    return row


def _mine(text):
    return {"is_mine": True, "text": text, "time": "23:41"}


class SplitMessagesCardTest:

    def test_卡片行要能当对方最新消息(self):
        latest, _ = ReplyEngine._split_messages([_hr(text="你好"), _hr(card_text=CARD)])
        assert latest == CARD

    def test_空正文的行不许挡住上一条真实消息(self):
        """卡片没读到内容时也不能返回空串——那会让规则和意图一起失灵"""
        rows = [_hr(text="方便发一份简历过来吗？"), _hr(text="", card_text="")]
        latest, _ = ReplyEngine._split_messages(rows)
        assert latest == "方便发一份简历过来吗？"

    def test_我方消息不算(self):
        rows = [_hr(text="方便发份简历吗"), _mine("[简历已发送]")]
        latest, _ = ReplyEngine._split_messages(rows)
        assert latest == "方便发份简历吗"

    def test_对方一句没说才返回空(self):
        latest, _ = ReplyEngine._split_messages([_mine("您好，我对这个岗位很感兴趣")])
        assert latest == ""

    def test_卡片文本要进历史(self):
        """历史里丢掉卡片，AI 就看不到"HR 在要简历"这件事，只会答非所问"""
        _, history = ReplyEngine._split_messages([_hr(card_text=CARD), _mine("好的")])
        assert any(CARD in str(h.get("text") or h.get("card_text") or "") for h in history)


class RuleSeesCardTest:
    """卡片文本进了决策，默认规则表就该把动作判成发简历。"""

    def test_简历卡片命中发简历动作(self):
        from boss_bot.rules import RuleEngine
        rules = {"简历": "send_resume", "发简历": "send_resume"}
        got = RuleEngine(rules).match(CARD)
        assert got and got[0] == "resume", got


class MainLoopUsesCardTest:
    """主循环自己那份"对方最新消息"也不能只看 text，否则记录里收到的永远是空的。"""

    def test_主循环取最新消息时带上卡片正文(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        start = src.index("latest_other_msg = None")
        block = src[start:start + 700]
        assert "card_text" in block, "还在只认 text：卡片会被当成没说话"
