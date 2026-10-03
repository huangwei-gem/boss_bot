# -*- coding: utf-8 -*-
"""HR 要过简历、我们却没发出去的那些会话，要能被挑出来补发。

2026-10-03 排查截图那一条（江女士 | 孤波）时发现：回复轮只点**未读**会话，
而这些会话早就被我们点开并回过一句文字，红点没了，之后再也不会被读到。
也就是说光修卡片取话链，欠出去的简历还是永远不会补——
那句"稍后把简历整理好发给您"会一直挂着。

判据取自盘上真实会话（messages/a1_江女士_孤波.json、a1_刘保罗_沁灵科技.json）：
发送成功的痕迹有两种，我们自己写的 [简历已发送] 动作行，和 BOSS 的系统卡
（"对方已同意，您的附件简历已发送给对方"），两种都算已发。
"""
import copy
import json
from pathlib import Path

import pytest

from boss_bot.pending_resume import pending_resume_asks, resume_already_sent

ROOT = Path(__file__).resolve().parent.parent


def _conv(name, messages, job_name="", company=""):
    return {"chat_name": name, "job_name": job_name, "company": company,
            "messages": messages}


class RealThreadTest:
    """直接拿盘上真实存档跑，不造理想化数据"""

    def _load(self, fname):
        data = json.loads((ROOT / "messages" / fname).read_text(encoding="utf-8"))
        return _conv(data["chat_name"], data["messages"],
                     data.get("job_name", ""), data.get("company", ""))

    def test_江女士那条卡片会话要被挑出来(self):
        conv = self._load("a1_江女士_孤波.json")
        got = pending_resume_asks([conv])
        assert len(got) == 1, "截图里那条就是欠简历的，挑不出来等于没修"
        assert got[0]["chat_name"] == "江女士"
        assert "附件简历" in got[0]["ask"]

    def test_已经发过的刘保罗不再挑(self):
        conv = self._load("a1_刘保罗_沁灵科技.json")
        assert resume_already_sent(conv["messages"])
        assert pending_resume_asks([conv]) == []

    def test_全盘挑出的必须是真索要(self):
        """105 个会话逐个过一遍：挑出来的每一条，其 ask 必须真是在要简历。

        逐个文件判而不是整体一次判——会话身份是"姓名+岗位"，
        盘上有 4 组重名昵称，按姓名回捞消息会捞到另一个人的对话。
        """
        from boss_bot.intent import is_resume_request
        picked = 0
        for p in sorted((ROOT / "messages").glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            conv = _conv(d.get("chat_name", p.stem), d.get("messages") or [],
                         d.get("job_name", ""), d.get("company", ""))
            got = pending_resume_asks([conv])
            if not got:
                continue
            picked += len(got)
            assert is_resume_request(got[0]["ask"]), f"误挑 {p.name}: {got[0]['ask']}"
            assert not resume_already_sent(conv["messages"]), f"已发过还挑 {p.name}"
        assert picked, "盘上确实有欠着的会话，一条都没挑出来说明判据失效"


class BackfillWiringTest:
    """主循环要把这份清单接到真实发送链上，而不是停在"知道欠着"。"""

    class _Store:
        def __init__(self, convs):
            self._convs = convs

        def get_all_chats_detail(self):
            return self._convs

    class _Handler:
        def __init__(self, rows):
            self._rows = rows
            self.listed = 0

        def get_all_chats(self):
            self.listed += 1
            return self._rows

    def _loop(self, convs, rows):
        from types import SimpleNamespace
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp.account_index = 1
        lp._msg_store = self._Store(convs)
        lp._chat_handler = self._Handler(rows)
        lp._reply_engine = SimpleNamespace(wait_human_delay=lambda: None)
        lp.entered = []
        lp._process_single_chat = lambda info: lp.entered.append(info)
        lp.logs = []
        lp._log = lambda level, msg: lp.logs.append(msg)
        return lp

    def _owed(self, account_index=1):
        conv = _conv("江女士", [
            {"is_mine": False, "card_text": "我想要一份您的附件简历，您是否同意 拒绝 同意",
             "text": ""}],
            job_name="数据分析18-25K上海查看职位", company="孤波")
        conv["account_index"] = account_index
        return conv

    def test_按侧栏那一行进会话走正常发送链(self):
        row = {"index": 7, "name": "江女士", "company": "孤波", "preview": "", "unread_count": 0}
        lp = self._loop([self._owed()], [row])
        lp._backfill_pending_resumes()
        assert lp.entered == [row], "要拿侧栏自己那行原样进会话，enter_chat 会再校验一次"

    def test_只补本账号的会话(self):
        """两个号聊过同一个 HR 时不能互相代付：存档按 account_index 分"""
        row = {"index": 7, "name": "江女士", "company": "孤波", "preview": ""}
        lp = self._loop([self._owed(account_index=0)], [row])
        lp._backfill_pending_resumes()
        assert lp.entered == []

    def test_侧栏找不到就跳过不报错(self):
        lp = self._loop([self._owed()], [])
        lp._backfill_pending_resumes()
        assert lp.entered == []
        assert any("侧栏" in m for m in lp.logs), "找不到要说一声，不能静默吞掉"

    def test_没有欠简历的不去翻侧栏(self):
        conv = self._owed()
        conv["messages"] = [{"is_mine": False, "text": "您好，方便聊聊吗"}]
        lp = self._loop([conv], [])
        lp._backfill_pending_resumes()
        assert lp._chat_handler.listed == 0, "没欠东西就别白走一遍侧栏"

    def test_接在启动全量同步之后(self):
        """补扫读的就是刚同步完的那份存档，挪到别处会拿旧数据判"""
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        at = src.index("self._full_sync_chats()")
        assert "self._backfill_pending_resumes()" in src[at:at + 400]


class SyntheticTest:


    def test_HR后来又有新消息就不是欠简历(self):
        conv = _conv("陈女士", [
            {"is_mine": False, "text": "方便发一份简历过来吗？"},
            {"is_mine": True, "text": "好的"},
            {"is_mine": False, "text": "那我等你消息哈"},
        ])
        assert pending_resume_asks([conv]) == []

    def test_我们自己回过文字不算发过(self):
        """"稍后把简历整理好发给您"这种话不能当发送凭证，否则永远补不回来"""
        conv = _conv("赵女士", [
            {"is_mine": False, "text": "我想要一份您的附件简历，您是否同意 拒绝 同意"},
            {"is_mine": True, "text": "好的，我稍后把简历整理好发给您，感谢您的关注。"},
        ])
        assert not resume_already_sent(conv["messages"])
        assert len(pending_resume_asks([conv])) == 1

    def test_没有对方消息的会话跳过(self):
        assert pending_resume_asks([_conv("孙女士", [
            {"is_mine": True, "text": "您好，我是黄维"}])]) == []

    def test_按最新一条对方消息判(self):
        conv = _conv("周女士", [
            {"is_mine": False, "text": "您好，看您简历不太合适"},
            {"is_mine": False, "text": "方便发一份简历过来吗？"},
        ])
        assert len(pending_resume_asks([conv])) == 1

    def test_不改传入的会话数据(self):
        conv = _conv("吴女士", [
            {"is_mine": False, "text": "方便发一份简历过来吗？"}])
        before = copy.deepcopy(conv)
        pending_resume_asks([conv])
        assert conv == before
