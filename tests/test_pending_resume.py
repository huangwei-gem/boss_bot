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
import re
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
            self.enter_ok = True
            self.messages = []
            self.job_name = "数据分析18-25K上海查看职位"

        def get_all_chats(self):
            self.listed += 1
            return self._rows

        def enter_chat(self, row):
            return self.enter_ok

        def read_all_messages(self):
            return self.messages

        def get_job_name(self):
            return self.job_name

    def _loop(self, convs, rows):
        from types import SimpleNamespace
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp.account_index = 1
        lp._msg_store = self._Store(convs)
        lp._chat_handler = self._Handler(rows)
        lp._reply_engine = SimpleNamespace(wait_human_delay=lambda: None)
        lp.entered = []
        lp.actions = []
        lp._process_single_chat = lambda info: lp.entered.append(info)
        lp._handle_reply_action = (
            lambda action, content, meta, name, job_name, latest, chat_company="":
            lp.actions.append((action, name, job_name, latest, meta.get("source"))) or True)
        lp._state_store = SimpleNamespace(resume_sent=lambda name: False)
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

    def test_直接发动作而不是再走一遍文字回复链(self):
        """补扫不能借用未读回复链的"处理过就没有"判据。

        2026-10-03 实测：江女士那条卡片在页面上读不出正文（.text-content 空、
        卡片探针也没给），于是"对方最新消息"退成卡片之前那句"方便发一份简历过来吗？"，
        而那句话我们 10-02 已经用文字回过 → was_handled 命中 → "已处理过，跳过"。
        索要明明没被满足，却永远不会再被处理。所以补扫要直接走发送动作。
        """
        row = {"index": 7, "name": "江女士", "company": "孤波", "preview": "", "unread_count": 0}
        lp = self._loop([self._owed()], [row])
        lp._backfill_pending_resumes()
        assert lp.actions and lp.actions[0][0] == "resume", \
            f"没走发送动作: {lp.actions}"
        assert lp.actions[0][1] == "江女士"
        assert lp.actions[0][3].startswith("我想要一份您的附件简历")
        assert lp.actions[0][4] == "backfill"
        assert lp.entered == [], "不该再进文字回复链，那会被'已处理过'挡掉"

    def test_进不去会话就不发(self):
        row = {"index": 7, "name": "江女士", "company": "孤波", "preview": ""}
        lp = self._loop([self._owed()], [row])
        lp._chat_handler.enter_ok = False
        lp._backfill_pending_resumes()
        assert lp.actions == []

    def test_页面上已经回过简历就不再发(self):
        """存档可能落后于线上：进去看到"您的附件简历已发送给对方"就别再发了"""
        row = {"index": 7, "name": "江女士", "company": "孤波", "preview": ""}
        lp = self._loop([self._owed()], [row])
        lp._chat_handler.messages = [
            {"is_mine": False, "text": "", "card_text": "对方已同意，您的附件简历已发送给对方"}]
        lp._backfill_pending_resumes()
        assert lp.actions == []
        assert any("不再重复发" in m for m in lp.logs)

    def test_页面上HR后来改了口就不发(self):
        """拒绝之后再把简历塞过去是骚扰"""
        row = {"index": 7, "name": "江女士", "company": "孤波", "preview": ""}
        lp = self._loop([self._owed()], [row])
        lp._chat_handler.messages = [
            {"is_mine": False, "text": "我想要一份您的附件简历，您是否同意 拒绝 同意"},
            {"is_mine": False, "text": "不好意思，不太合适哦"},
        ]
        lp._backfill_pending_resumes()
        assert lp.actions == []
        assert any("拒绝" in m for m in lp.logs)

    def test_只补本账号的会话(self):
        """两个号聊过同一个 HR 时不能互相代付：存档按 account_index 分"""
        row = {"index": 7, "name": "江女士", "company": "孤波", "preview": ""}
        lp = self._loop([self._owed(account_index=0)], [row])
        lp._backfill_pending_resumes()
        assert lp.actions == []

    def test_侧栏找不到就跳过不报错(self):
        lp = self._loop([self._owed()], [])
        lp._backfill_pending_resumes()
        assert lp.actions == []
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


class SidebarCoverageTest:
    """补扫要能点到列表靠后的会话：侧栏是虚拟列表，不滚动只能看到当前那几十行。

    2026-10-03 真机实测：账号2 本地存了 68 个会话，get_all_chats() 一次只回 40 行，
    欠简历的 8 个全在没渲染的那半截里，于是补扫逐个报"侧栏已找不到这个会话"。
    """

    class _Page:
        """假浏览器：侧栏只渲染可视窗口那几行，靠 scrollTop 往前挪。

        与生产代码的约定只有两处在字符串上：滚动 JS 里有 scrollTop，
        探针 JS 里有 HAS_ROW 和 `var want = {...}`（内容是 JSON）。
        """
        def __init__(self, rows, window=5):
            self.rows = rows
            self.window = window
            self.pos = 0
            self.url = "https://www.zhipin.com/web/geek/chat"
            self.scrolls = 0

        def rendered(self):
            start = self.pos
            return self.rows[start:start + self.window]

        def get(self, url, *a, **k):
            pass

        def run_js(self, js, as_expr=False):
            if "RESET" in js:
                self.pos = 0
                return "0"
            if "HAS_ROW" in js:
                want = json.loads(re.search(r"var want = (\{.*?\});", js).group(1))
                return "yes" if any(
                    n == want["n"] and (not want["c"] or c == want["c"])
                    for (n, c) in self.rendered()) else "no"
            if "scrollTop" in js:
                self.scrolls += 1
                self.pos = min(self.pos + self.window,
                               max(0, len(self.rows) - self.window))
                return f"{self.pos}/{len(self.rows)}"
            start = self.pos
            return json.dumps([
                {"index": i, "name": n, "company": c, "preview": "", "unread_count": 0}
                for i, (n, c) in enumerate(self.rows[start:start + self.window], start)
            ])

    def test_滚到底要把全部会话收齐(self, monkeypatch):
        from boss_bot import page_handler as ph

        rows = [(f"HR{i}", f"公司{i}") for i in range(68)]
        page = self._Page(rows)
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = ph.BossChatHandler.__new__(ph.BossChatHandler)
        h.page = page
        h.browser = page

        got = h.get_all_chats()
        names = [r["name"] for r in got]
        assert len(names) == 68, f"只收到 {len(names)}/68，靠后的会话进不了同步和补扫"
        assert len(set(names)) == 68, "滚动重叠处不能重复计数"

    def test_不滚到底就收工(self, monkeypatch):
        """列表本来就全渲染时，不能白滚十几轮"""
        from boss_bot import page_handler as ph

        rows = [("HR0", "公司0"), ("HR1", "公司1")]
        page = self._Page(rows, window=5)
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = ph.BossChatHandler.__new__(ph.BossChatHandler)
        h.page = page
        h.browser = page
        got = h.get_all_chats()
        assert [r["name"] for r in got] == ["HR0", "HR1"]
        assert page.scrolls <= 2, f"两行的列表滚了 {page.scrolls} 次"

    def test_点开会话也要滚到目标那一行(self, monkeypatch):
        """enter_chat 的点击 JS 只能在"渲染出来的行"里找人。

        2026-10-03 实测：补扫时侧栏已被收集流程滚到底，8 个目标全报
        "会话切换校验失败" —— 不是没有这个会话，是那几行根本没渲染。
        所以点之前得先按 姓名+公司 滚过去。
        """
        from boss_bot import page_handler as ph

        rows = [(f"HR{i}", f"公司{i}") for i in range(68)]
        page = self._Page(rows, window=5)
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = ph.BossChatHandler.__new__(ph.BossChatHandler)
        h.page = page
        h.browser = page

        assert h._scroll_to_chat_row("HR60", "公司60") is True
        assert any(n == "HR60" for (n, _) in page.rendered()), "目标行没被滚进可视区"
        assert h._scroll_to_chat_row("不存在的人", "") is False, "滚到底还没有就该如实说找不到"

    def test_点击前必须已经滚到目标行(self, monkeypatch):
        """索引在虚拟列表里是过期信息。

        实测：账号2 侧栏 171 行，采集后按 index=12 去点"孙先生|沐数科技"，
        那会儿第 12 行是"马女士|掌门教育"，三次重试全点在错的人身上。
        所以 enter_chat 不能拿旧 index 点，也不能"找不到就按索引点"。
        """
        from boss_bot import page_handler as ph

        rows = [(f"HR{i}", f"公司{i}") for i in range(171)]
        page = _OpPage(rows, window=20)
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = ph.BossChatHandler.__new__(ph.BossChatHandler)
        h.page = page
        h.browser = page

        ok = h.enter_chat({"index": 5, "name": "HR160", "company": "公司160"})
        assert ok is True, "目标在列表深处就该滚过去点开，而不是报校验失败"
        assert page.calls[0] in ("probe", "RESET"), f"第一步就得确认目标行渲染没有: {page.calls[:4]}"
        assert page.clicked == ("HR160", "公司160"), f"点到别人身上了: {page.clicked}"

    def test_目标行已经渲染时不用滚(self, monkeypatch):
        """未读会话都在列表顶上，白滚一遍会把每轮回复拖慢"""
        from boss_bot import page_handler as ph

        rows = [(f"HR{i}", f"公司{i}") for i in range(171)]
        page = _OpPage(rows, window=20)
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = ph.BossChatHandler.__new__(ph.BossChatHandler)
        h.page = page
        h.browser = page

        assert h.enter_chat({"index": 2, "name": "HR1", "company": "公司1"}) is True
        assert "scrollTop" not in page.calls, f"顶部会话不需要滚动: {page.calls}"


class _OpPageMixin:
    """带点击/选中态的假侧栏，只用于 enter_chat 这类要走完整个交互的测试"""

    class _OpPage:
        def __init__(self, rows, window=20):
            self.rows = rows
            self.window = window
            self.pos = 0
            self.calls = []
            self.clicked = None

        def rendered(self):
            return self.rows[self.pos:self.pos + self.window]

        def get(self, *a, **k):
            pass

        @property
        def url(self):
            return "https://www.zhipin.com/web/geek/chat"

        def run_js(self, js, as_expr=False):
            if "RESET" in js:
                self.calls.append("RESET")
                self.pos = 0
                return "0"
            if "HAS_ROW" in js:
                self.calls.append("probe")
                want = json.loads(re.search(r"var want = (\{.*?\});", js).group(1))
                return "yes" if any(
                    n == want["n"] and (not want["c"] or c == want["c"])
                    for (n, c) in self.rendered()) else "no"
            if "scrollTop" in js:
                self.calls.append("scrollTop")
                self.pos = min(self.pos + self.window, max(0, len(self.rows) - self.window))
                return f"{self.pos}/{len(self.rows)}"
            if "by_index" in js:                     # enter_chat 的点击 JS
                self.calls.append("click")
                want = re.search(r'\{n: "(.*?)",\s*c: "(.*?)"\}', js, re.S)
                n, c = want.groups()
                visible = self.rendered()
                for (rn, rc) in visible:
                    if rn == n and (not c or rc == c):
                        self.clicked = (rn, rc)
                        return "ok"
                idx = int(re.search(r"friends\[(\d+)\]", js).group(1))
                if idx < len(visible):               # 真实行为：按索引点到当前第 idx 行
                    self.clicked = visible[idx]
                    return "by_index"
                return "not_found"
            if '"selected"' in js or "selected" in js:
                if not self.clicked:
                    return ""
                return json.dumps({"index": self.pos, "name": self.clicked[0],
                                   "company": self.clicked[1], "title": ""})
            if "#chat-input" in js:
                return "ready"
            return self.clicked[0] if self.clicked else ""


_OpPage = _OpPageMixin._OpPage


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
