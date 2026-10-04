# -*- coding: utf-8 -*-
"""红点要边滚边收：侧栏是虚拟列表，只读当前 DOM 会漏掉渲染窗口外的新消息。

用户口径（2026-10-04）：「你再把回复做完善吧，现在好多的回复没有回」

实测：账号2 侧栏 272 行，不滚只读到 40 行。以前 get_unread_chats 一次都不滚，
红点排在渲染窗口外的会话每轮都是"0 个未读"——这部分欠着的回复连存档那条腿也
补不到（没点开过，存档里就没有 HR 的新消息）。
"""
import json
import time

import pytest


def _handler(windows, total=9999, monkeypatch=None):
    """假页面：每次读行返回 windows 里下一页的内容，滚动回报"位置/总高"。"""
    from boss_bot.page_handler import BossChatHandler

    monkeypatch.setattr(time, "sleep", lambda s: None)
    state = {"rows": 0, "scroll": 0, "reset": 0}

    class _Page:
        def run_js(self, js, as_expr=True):
            if "scrollTop = 0" in js:
                state["reset"] += 1
                return "0"
            if "friend-content" in js:
                i = min(state["rows"], len(windows) - 1)
                state["rows"] += 1
                return json.dumps(windows[i], ensure_ascii=False)
            if "scrollTop" in js:
                state["scroll"] += 1
                # 真实容器滚到底之后 scrollTop 就停在最大值，位置不再变化
                return "%d/%d" % (min(state["scroll"] * 100, total), total)
            return ""

    h = BossChatHandler.__new__(BossChatHandler)
    h.page = _Page()
    h.go_to_chat = lambda: None
    return h, state


def _row(i, name, company, unread=0):
    return {"index": i, "name": name, "company": company, "preview": "在吗",
            "unread_count": unread}


class ScrollCollectTest:
    def test_渲染窗口外的红点也要收到(self, monkeypatch):
        windows = [
            [_row(i, f"甲{i}", "公司A") for i in range(4)],
            [_row(i, f"乙{i}", "公司B") for i in range(4)],
            [_row(8, "孙先生", "沐数科技", unread=2)],      # 只在第三屏渲染出来
        ]
        h, state = _handler(windows, total=4000, monkeypatch=monkeypatch)
        got = h.get_unread_chats(max_rounds=30)
        assert [g["name"] for g in got] == ["孙先生"], \
            f"只读第一屏的话这条永远看不见（滚了 {state['rows']} 屏）"
        assert got[0]["unread_count"] == 2
        assert state["rows"] >= 3

    def test_同一行滚过两次不重复计(self, monkeypatch):
        row = _row(0, "林女士", "圣元文化", unread=1)
        h, _ = _handler([[row], [row], [row]], total=300, monkeypatch=monkeypatch)
        got = h.get_unread_chats(max_rounds=3)
        assert len(got) == 1, "重复进候选会点两次同一个会话、回两遍"

    def test_到底就停(self, monkeypatch):
        h, state = _handler([[_row(0, "甲", "A")]], total=200, monkeypatch=monkeypatch)
        h.get_unread_chats(max_rounds=30)
        assert state["scroll"] <= 3, f"到底了还傻滚 {state['scroll']} 次"

    def test_每轮默认只滚靠前那些行(self, monkeypatch):
        """BOSS 按活跃时间排序，新消息基本都在靠前几十行；每轮全滚到底太贵。"""
        h, state = _handler([[_row(0, "甲", "A")]], total=99999, monkeypatch=monkeypatch)
        h.get_unread_chats()
        assert state["rows"] == 12

    def test_红点判据仍取自notice_badge(self):
        """全量同步读所有行，未读列表只认带红点的：两条腿不能混用判据。"""
        import inspect
        from boss_bot.page_handler import BossChatHandler
        src = inspect.getsource(BossChatHandler._sidebar_rows)
        assert "notice-badge" in src and "offsetParent" in src
        got = inspect.getsource(BossChatHandler.get_unread_chats)
        assert "unread_count" in got and "_SIDEBAR_SCROLL_JS" in got


class StartupOrderTest:
    """重启后先把已知欠着的回复接上，再慢慢做全量同步。"""

    def _loop(self):
        from types import SimpleNamespace
        from boss_bot.unified_config import UnifiedConfig
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp.account_index = 0
        lp.config = UnifiedConfig()
        lp._running = True
        lp._reply_enabled = True
        lp._reply_paused = False
        lp._full_sync_done = False
        flips = {"n": 0}
        lp._stop_event = SimpleNamespace(
            is_set=lambda: flips["n"] > 0,
            wait=lambda timeout=None: flips.__setitem__("n", flips["n"] + 1))
        lp._sync_chat_tab = lambda: None
        lp._check_health = lambda side=None: "ok"
        lp._reply_login_ok = lambda: None
        lp._check_and_archive_daily_data = lambda: None
        lp._hot_reload_config = lambda: None
        lp._log = lambda *a, **k: None
        lp._current_mode = "idle"
        lp._current_chat = None
        lp._state_store = SimpleNamespace(is_paused=lambda: False)
        lp._msg_store = SimpleNamespace()
        lp._reply_engine = SimpleNamespace(wait_human_delay=lambda: None)
        lp.calls = []
        lp._chat_handler = SimpleNamespace(
            go_to_chat=lambda: None,
            get_unread_chats=lambda max_rounds=12: [],
            get_all_chats=lambda: lp.calls.append("full_sync") or [])
        lp._run_reply_round = lambda: lp.calls.append("reply_round")
        lp._run_followup_round = lambda: lp.calls.append("followup")
        lp._full_sync_chats = lambda: lp.calls.append("full_sync")
        lp._backfill_pending_resumes = lambda: lp.calls.append("backfill")
        return lp

    def test_回复轮排在全量同步前面(self):
        """全量同步要逐个点开 270 多个会话（实测 25~50 分钟）。
        回复排在它后面，就等于每次重启后的头半小时里没人回话。"""
        from boss_bot.main_loop import UnifiedBotLoop
        lp = self._loop()
        UnifiedBotLoop._reply_loop(lp)
        assert "reply_round" in lp.calls and "full_sync" in lp.calls
        assert lp.calls.index("reply_round") < lp.calls.index("full_sync"), \
            f"启动顺序还是先同步后回复: {lp.calls}"
        assert lp.calls.index("followup") < lp.calls.index("full_sync"), \
            f"跟进也被全量同步挡住: {lp.calls}"
        assert lp._full_sync_done is True, "同步还得真做，只是不能挡在回复前面"


class SyncInterleaveTest:
    """同步中途也要插回复轮：一轮同步要 25~50 分钟，不能一路挡住回话。"""

    def _loop(self):
        from types import SimpleNamespace
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp._running = True
        lp._stop_event = SimpleNamespace(is_set=lambda: False)
        lp.chats_entered = []
        lp.calls = []
        lp._log = lambda *a, **k: None
        lp._reply_engine = SimpleNamespace(wait_human_delay=lambda: None)
        lp._chat_handler = SimpleNamespace(
            check_health=lambda: "ok",
            get_all_chats=lambda: [{"name": f"HR{i}", "company": f"公司{i}", "index": i}
                                   for i in range(45)],
            enter_chat=lambda info: lp.chats_entered.append(info["name"]) or True,
            read_all_messages=lambda: [{"is_mine": False, "text": "在吗", "mid": "9"}],
            read_selected_row=lambda: {"name": lp.chats_entered[-1], "company": "公司"},
            get_job_name=lambda: "数据分析")
        lp._msg_store = SimpleNamespace(merge_messages=lambda **kw: 1)
        lp._run_reply_round = lambda: lp.calls.append("reply_round")
        lp._run_followup_round = lambda: lp.calls.append("followup")
        return lp

    def test_每二十个会话插一轮回复(self):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = self._loop()
        UnifiedBotLoop._full_sync_chats(lp)
        assert len(lp.chats_entered) == 45, "同步本身还是要读完全部会话"
        assert lp.calls == ["reply_round", "followup", "reply_round", "followup"], \
            f"45 个会话应该插两轮: {lp.calls}"
