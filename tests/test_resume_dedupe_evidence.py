# -*- coding: utf-8 -*-
"""「这单简历发过了」只认 BOSS 那张卡，不认我们自己写的那行。

2026-10-08 数出来的：`bot_state*.json` 里标了 resume_sent 的会话 **81 个**，
可 `messages/*.json` 里真有附件简历卡片的只有 **39 个会话**。差的那四十多个
就是旧送达判据（在消息列表里找"简历"两个字）判成功的——每次成功都
① 写一行自记的 `[简历已发送]`、② `mark_resume_sent()` 落下状态位、
③ `resume_send_once` 从此不再给这一单发。于是用户看到的现象是
「好多面试官要简历，你没给」，而台账还把它们统计成"已发"。

改三处，都要有证据：
- `resume_already_sent()` 只认 BOSS 侧的卡片文案；自记那行不算证据
- 台账的"已发"同一口径，欠的就是欠的
- 主循环去重读状态位前先对一次存档：没证据就把那位假标记清掉重发；
  存档读不到时才退回信状态位（宁可少发一次，也不要对着同一个 HR 发两遍）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.contact_ledger import contact_rows  # noqa: E402
from boss_bot.pending_resume import resume_already_sent  # noqa: E402
from boss_bot.state_store import StateStore  # noqa: E402

BOSS_CARD = {"is_mine": False, "isFriend": False, "text": "",
             "card_text": "您的附件简历 数据分析简历-黄维.docx 已发送给Boss，请查看"}
OUR_CLAIM = {"is_mine": True, "isFriend": False, "text": "[简历已发送]"}
HR_ASK = {"is_mine": False, "isFriend": True, "text": "",
          "card_text": "方便把附件简历发我看看吗"}


class 证据口径Test:
    def test_BOSS的卡片才算送达(self):
        assert resume_already_sent([HR_ASK, BOSS_CARD]) is True

    def test_我们自记的那行不算证据(self):
        # 它是旧假判据写下的，拿它自证就等于"我说发了就算发了"
        assert resume_already_sent([HR_ASK, OUR_CLAIM]) is False

    def test_台账不再把自记行统计成已发(self):
        rows = contact_rows([{"chat_name": "刘女士", "company": "某公司",
                              "messages": [HR_ASK, OUR_CLAIM]}])
        assert rows[0]["resume_sent"] is False
        assert rows[0]["resume_asked"] is True
        rows2 = contact_rows([{"chat_name": "刘女士", "company": "某公司",
                               "messages": [HR_ASK, BOSS_CARD, OUR_CLAIM]}])
        assert rows2[0]["resume_sent"] is True


class 去重要回头看存档Test:
    def _loop(self, tmp_path, marked=True, messages=None):
        from types import SimpleNamespace
        from boss_bot.main_loop import UnifiedBotLoop
        loop = UnifiedBotLoop.__new__(UnifiedBotLoop)
        loop.account_index = 0
        loop._log = lambda *a, **k: None
        loop._state_store = StateStore(tmp_path / "bot_state.json")
        if marked:
            loop._state_store.mark_resume_sent("刘女士")
        loop._msg_store = SimpleNamespace(
            get_messages=lambda name, job_name="", company="": messages)
        return loop

    def test_标记有但存档没证据要清掉标记(self, tmp_path):
        loop = self._loop(tmp_path, marked=True, messages=[HR_ASK, OUR_CLAIM])
        assert loop._resume_delivered("刘女士") is False
        assert loop._state_store.resume_sent("刘女士") is False, "标记没清，下一轮还是不发"

    def test_存档有证据就不重发(self, tmp_path):
        loop = self._loop(tmp_path, marked=True, messages=[HR_ASK, BOSS_CARD])
        assert loop._resume_delivered("刘女士") is True

    def test_存档读不到时信状态位(self, tmp_path):
        """宁可少发一次，也不要对着同一个 HR 发两遍"""
        loop = self._loop(tmp_path, marked=True, messages=None)
        assert loop._resume_delivered("刘女士") is True

    def test_没标记也没证据就是没发(self, tmp_path):
        loop = self._loop(tmp_path, marked=False, messages=[HR_ASK])
        assert loop._resume_delivered("刘女士") is False

    def test_索要过的会话走的是这条判据(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        源 = inspect.getsource(UnifiedBotLoop._handle_reply_action)
        assert "_resume_delivered" in 源, "还在只看那个会自己骗自己的状态位"
