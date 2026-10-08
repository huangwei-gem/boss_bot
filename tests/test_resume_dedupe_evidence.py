# -*- coding: utf-8 -*-
"""「这单简历发过了」只认 BOSS 那张卡，不认我们自己写的那行。

2026-10-08 现数（同一份存档、同一份口径）：
- 存档里带附件卡片字样的消息 **157 条**，落在 **88 个会话**；
- `bot_state*.json` 里标了 resume_sent 的会话 **81 个**，其中 2 个（每号 1 个）
  存档里压根没有卡片；
- 台账在同一口径下判「HR 要过简历但没送达」**57 单**（号0 32 / 号1 25）。
旧送达判据是在消息列表里找"简历"两个字，所以每次"成功"都
① 写一行自记的 `[简历已发送]`、② `mark_resume_sent()` 落下状态位、
③ `resume_send_once` 从此不再给这一单发。于是用户看到的现象是
「好多面试官要简历，你没给」，而台账还把它们统计成"已发"。

标记不能一刀切全不信：新标记是"点确认前数一遍、点完卡片多出一条"验过的，
存档还没同步到时也得认，否则下一轮就对同一个 HR 再发一遍。所以标记要落时间，
盘上那些没时间戳的旧标记才按存档重判。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.contact_ledger import contact_rows  # noqa: E402
from boss_bot.message_store import MessageStore  # noqa: E402
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
    def _loop(self, tmp_path, marked=True, messages=None, stamped=True):
        """stamped=False 复刻今天盘上那 81 条旧标记：只写 True，不写时间。"""
        from types import SimpleNamespace
        from boss_bot.main_loop import UnifiedBotLoop
        path = tmp_path / "bot_state.json"
        loop = UnifiedBotLoop.__new__(UnifiedBotLoop)
        loop.account_index = 0
        loop._log = lambda *a, **k: None
        loop._state_store = StateStore(path)
        if marked:
            loop._state_store.mark_resume_sent("刘女士")
            if not stamped:
                data = json.loads(path.read_text(encoding="utf-8"))
                for chat in data.get("chats", {}).values():
                    chat.pop("resume_sent_at", None)
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                loop._state_store = StateStore(path)
        loop._msg_store = SimpleNamespace(
            get_messages=lambda name, job_name="", company="": messages)
        return loop

    def test_旧标记没落时间才按证据撤回(self, tmp_path):
        loop = self._loop(tmp_path, marked=True, stamped=False,
                          messages=[HR_ASK, OUR_CLAIM])
        assert loop._resume_delivered("刘女士") is False
        assert loop._state_store.resume_sent("刘女士") is False, "标记没清，下一轮还是不发"

    def test_验过的标记存档还没同步到也要认(self, tmp_path):
        """刚发成功、卡片没进存档时清掉标记＝对着同一个 HR 发两遍"""
        loop = self._loop(tmp_path, marked=True, stamped=True,
                          messages=[HR_ASK, OUR_CLAIM])
        assert loop._resume_delivered("刘女士") is True
        assert loop._state_store.resume_sent("刘女士") is True

    def test_发过一次就落下时间戳(self, tmp_path):
        store = StateStore(tmp_path / "bot_state.json")
        store.mark_resume_sent("刘女士")
        assert store.resume_sent_at("刘女士"), "没时间戳就分不清新验过的和旧假判据"

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

    def test_回复轮走的是这一条判据(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        源 = inspect.getsource(UnifiedBotLoop._handle_reply_action)
        assert "_resume_delivered" in 源, "还在只看那个会自己骗自己的状态位"

    def test_补扫的门槛也是这一条(self):
        """补扫原来只看状态位：旧假标记挂着，欠的那 57 单连会话都进不去"""
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        源 = inspect.getsource(UnifiedBotLoop._backfill_pending_resumes)
        assert "_resume_delivered" in 源
        assert "self._state_store.resume_sent(" not in 源


class 发完要把卡片落进存档Test:
    """送达判据在页面上数到卡片多出一条，可存档里只留下我们自记的那行 ——
    下一次去重按"存档有没有卡片"判，就会把这一单当成没发过再发一遍。

    线上实例：2026-10-08 15:56 账号2 发给楚仪可的那单，日志
    「简历送达验证通过（卡片 0 → 1 条）」，可 16:3x 再看存档只有
    [简历已发送] 那一行 action，卡片根本没进来。
    """

    def _loop(self, tmp_path):
        from types import SimpleNamespace
        from boss_bot.main_loop import UnifiedBotLoop
        loop = UnifiedBotLoop.__new__(UnifiedBotLoop)
        loop.account_index = 1
        loop.logs = []
        loop._log = lambda level, msg: loop.logs.append(msg)
        loop._msg_store = MessageStore(base_dir=tmp_path, account_index=1)
        loop._state_store = StateStore(tmp_path / "bot_state.json")
        return loop

    def test_发成功后页面那张卡片要并进存档(self, tmp_path):
        from types import SimpleNamespace
        loop = self._loop(tmp_path)
        loop._chat_handler = SimpleNamespace(
            read_all_messages=lambda **kw: [HR_ASK, BOSS_CARD])
        loop._persist_resume_card("楚仪可", "兼职·平面设计300-500元/时北京查看职位",
                                  "北京智能知识数据科技")
        存档 = loop._msg_store.get_messages(
            "楚仪可", job_name="兼职·平面设计300-500元/时北京查看职位",
            company="北京智能知识数据科技")
        assert resume_already_sent(存档) is True, "卡片没落进存档，下一轮就去重不掉"

    def test_没有mid的两张卡片不许撞成一条(self, tmp_path):
        """去重键退回 content+time 时，卡片正文在 card_text 里，content 是空的——
        两张不同的卡片键一样，后一张直接被丢掉，证据就这么没了"""
        store = MessageStore(base_dir=tmp_path, account_index=1)
        store.merge_messages(chat_name="楚仪可", new_messages=[HR_ASK, BOSS_CARD],
                             job_name="平面设计", company="北京智能知识数据科技")
        存档 = store.get_messages("楚仪可", job_name="平面设计",
                                  company="北京智能知识数据科技")
        assert len(存档) == 2, f"两张卡片被并成一张: {存档}"
        assert resume_already_sent(存档) is True

    def test_落了证据之后这一单就判成已发(self, tmp_path):
        from types import SimpleNamespace
        loop = self._loop(tmp_path)
        loop._chat_handler = SimpleNamespace(
            read_all_messages=lambda **kw: [HR_ASK, BOSS_CARD])
        loop._state_store.mark_resume_sent("楚仪可")
        loop._persist_resume_card("楚仪可", "平面设计", "北京智能知识数据科技")
        assert loop._resume_delivered("楚仪可", "平面设计",
                                      "北京智能知识数据科技") is True

    def test_回读失败只留痕不许把发送流程搞停(self, tmp_path):
        from types import SimpleNamespace
        loop = self._loop(tmp_path)

        def 炸(**kw):
            raise RuntimeError("页面读不到")
        loop._chat_handler = SimpleNamespace(read_all_messages=炸)
        loop._persist_resume_card("楚仪可", "平面设计", "北京智能知识数据科技")
        assert any("证据" in m or "读" in m for m in loop.logs), loop.logs

    def test_发送成功那条路真的调了它(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        源 = inspect.getsource(UnifiedBotLoop._handle_reply_action)
        assert "_persist_resume_card" in 源
        assert 源.index("send_resume()") < 源.index("_persist_resume_card"), \
            "要在发送成功之后落证据，放前面读到的还是旧页面"



class 页面回读那一遍也认得卡片Test:
    """_persist_resume_card 发成功当场回读页面，18:32、18:48 两单明明
    「卡片 0 → 1 条」验证通过，却被"发送后回读没再看到卡片"跳过没落档。
    根因：read_all_messages 出来的原始消息卡片正文在 block（text 是空的），
    而 resume_already_sent 只看 text/card_text——和台账那边 _message_text
    查三个字段的老口径不一致。"""

    def test_页面原始形状里的卡片也算证据(self):
        原始 = {"text": "", "time": "18:32", "isFriend": False, "is_mine": False,
                "is_system": True, "mid": "392667908764162",
                "block": "数据分析简历-黄维.docx 点击预览附件简历"}
        assert resume_already_sent([原始]) is True

    def test_普通气泡的block不许冒充卡片(self):
        """补上 block 这一路是把双刃剑：HR 一句"简历我看过了"的 block 里带"简历"
        不能算送达——用的还是那四个卡片特有字样。"""
        气泡 = {"text": "简历我这边看过了", "block": "14:02 简历我这边看过了",
                "isFriend": True, "is_mine": False}
        assert resume_already_sent([气泡]) is False

    def test_发送成功那条路读的是页面原始形状(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        源 = inspect.getsource(UnifiedBotLoop._persist_resume_card)
        assert "read_all_messages" in 源 and "merge_messages" in 源
