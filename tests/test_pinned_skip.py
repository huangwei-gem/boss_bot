# -*- coding: utf-8 -*-
"""他亲手置顶的那几路会话，机器一律不动。

用户口径（2026-10-08）：「我置顶的岗位你就不要动了，因为那我打算单独聊。」

置顶是他在 BOSS 里标的"这一路我要自己谈"，所以每一条会动手的腿都要绕开：
回复轮的红点候选、存档欠回复、主动跟进、补发简历，以及两个批量清剿/补点工具。
判据取自侧栏那一行的 friend-top 类（实测置顶行 class="friend-content friend-top"，
普通行只有 friend-content）。这个标记必须落进存档——存档驱动的那几条腿
（欠回复、跟进、工具名单）本来就不看侧栏，不落盘它们根本不知道谁被置顶了。
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.reply_queue import followup_due, owed_replies, worth_following_up  # noqa: E402
from tests.test_reply_backfill_wiring import _make_loop  # noqa: E402


def _ago(hours):
    return (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _handler(windows, monkeypatch, total=9999):
    """假页面：每次读行返回 windows 里下一屏的内容（沿用 test_unread_scroll 那套约定）。"""
    from boss_bot.page_handler import BossChatHandler

    monkeypatch.setattr(time, "sleep", lambda s: None)
    state = {"rows": 0, "scroll": 0}

    class _Page:
        def run_js(self, js, as_expr=True):
            if "scrollTop = 0" in js:
                return "0"
            if "friend-content" in js:
                i = min(state["rows"], len(windows) - 1)
                state["rows"] += 1
                return json.dumps(windows[i], ensure_ascii=False)
            if "scrollTop" in js:
                state["scroll"] += 1
                return "%d/%d" % (min(state["scroll"] * 100, total), total)
            return ""

    h = BossChatHandler.__new__(BossChatHandler)
    h.page = _Page()
    h.go_to_chat = lambda: None
    return h


def _row(i, name, company, unread=0, pinned=False):
    return {"index": i, "name": name, "company": company, "preview": "在吗",
            "unread_count": unread, "pinned": pinned}


def _conv(name, messages, company="公司", pinned=None, job_name="数据分析",
          updated_at=None):
    c = {"chat_name": name, "company": company, "job_name": job_name,
         "account_index": 0, "updated_at": _ago(3) if updated_at is None else updated_at,
         "messages": messages}
    if pinned:
        c["pinned"] = True
    return c


def _hr(text, mid="100"):
    # 存档里 HR 的消息带 isFriend（台账那层认这个字段）
    return {"is_mine": False, "isFriend": True, "text": text, "time": "", "mid": mid}


def _me(text, mid="200"):
    return {"is_mine": True, "isFriend": False, "text": text, "time": "", "mid": mid}


class 侧栏读得到置顶Test:
    def test_置顶判据取自friend_top类(self):
        """实测：置顶行的 class 是 "friend-content friend-top"，普通行只有 friend-content。"""
        import inspect
        from boss_bot.page_handler import BossChatHandler
        src = inspect.getsource(BossChatHandler._sidebar_rows)
        assert "friend-top" in src, "没读置顶类，机器就不知道哪几路是他自己聊的"
        assert "pinned" in src

    def test_滚动时置顶行即使没红点也带出标记(self, monkeypatch):
        """全量同步那一趟要能看见"置顶但没红点"的行——落盘靠它。"""
        h = _handler([[_row(0, "陈女士", "湖南云幻科技", pinned=True),
                       _row(1, "王女士", "长沙数据标注", unread=2)]], monkeypatch)
        got = h.get_all_chats(max_rounds=2)
        by_name = {g["name"]: g for g in got}
        assert by_name["陈女士"]["pinned"] is True
        assert by_name["王女士"]["pinned"] is False

    def test_红点候选也带着置顶标记(self, monkeypatch):
        h = _handler([[_row(0, "陈女士", "湖南云幻科技", unread=1, pinned=True),
                       _row(1, "王女士", "长沙数据标注", unread=1)]], monkeypatch)
        got = h.get_unread_chats(max_rounds=2)
        assert [g["name"] for g in got] == ["陈女士", "王女士"]
        assert got[0]["pinned"] is True, "回复轮要靠这个字段当场放行"
        assert got[1]["pinned"] is False


class 存档驱动的腿绕开置顶Test:
    def test_欠回复的置顶会话不补进候选(self):
        convs = [_conv("陈女士", [_hr("你什么时候到岗")], company="湖南云幻科技",
                       pinned=True),
                 _conv("王女士", [_hr("大四还有课吗")], company="长沙数据标注")]
        got = owed_replies(convs)
        assert [c["name"] for c in got] == ["王女士"], got

    def test_置顶会话不被主动跟进(self):
        # 静默 12 小时：够到 followup_after_hours=8 那道门，才测得出是不是被置顶挡住的
        convs = [_conv("陈女士", [_hr("好的"), _me("有经验的，方便发份简历吗")],
                       company="湖南云幻科技", pinned=True, updated_at=_ago(12)),
                 _conv("王女士", [_hr("好的"), _me("我住长沙，随时可以线上面试")],
                       company="长沙数据标注", updated_at=_ago(12))]
        assert worth_following_up(convs[0]) is False, "他自己聊的那路不该被机器追"
        due = followup_due(convs, {}, now=datetime.now())
        assert [d["name"] for d in due] == ["王女士"], due


class 回复轮放行Test:
    def _loop(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._running = True
        loop._reply_paused = False
        loop._msg_store.get_all_chats_detail.return_value = []
        loop._reply_engine.can_reply.return_value = True
        loop._process_single_chat = MagicMock()
        logs = []
        loop._log = lambda *a, **k: logs.append(a[-1])
        loop._logs = logs
        return loop

    def test_置顶的红点会话不点开并说明是谁的主意(self, tmp_path, monkeypatch):
        loop = self._loop(tmp_path, monkeypatch)
        loop._chat_handler.get_unread_chats.return_value = [
            _row(0, "陈女士", "湖南云幻科技", unread=1, pinned=True),
            _row(1, "王女士", "长沙数据标注", unread=1),
        ]
        loop._run_reply_round()
        handled = [c.args[0]["name"] for c in loop._process_single_chat.call_args_list]
        assert handled == ["王女士"], f"置顶的那路他自己聊，机器不能点开：{handled}"
        assert any("置顶" in str(x) for x in loop._logs), \
            f"要让他看见是这条口径挡下来的：{loop._logs}"

    def test_置顶标记落进存档(self, tmp_path, monkeypatch):
        """不落盘的话，欠回复/跟进/工具那几条只看存档的腿根本不知道谁被置顶了。"""
        loop = self._loop(tmp_path, monkeypatch)
        loop._chat_handler.get_unread_chats.return_value = [
            _row(0, "陈女士", "湖南云幻科技", unread=1, pinned=True)]
        loop._run_reply_round()
        loop._msg_store.set_pinned.assert_called_once_with(
            "陈女士", True, company="湖南云幻科技")

    def test_取消置顶后机器重新接手(self, tmp_path, monkeypatch):
        loop = self._loop(tmp_path, monkeypatch)
        loop._chat_handler.get_unread_chats.return_value = [
            _row(0, "陈女士", "湖南云幻科技", unread=1, pinned=True)]
        loop._run_reply_round()
        loop._msg_store.set_pinned.reset_mock()
        loop._process_single_chat.reset_mock()
        loop._chat_handler.get_unread_chats.return_value = [
            _row(0, "陈女士", "湖南云幻科技", unread=1, pinned=False)]
        loop._run_reply_round()
        loop._msg_store.set_pinned.assert_called_once_with(
            "陈女士", False, company="湖南云幻科技")
        assert loop._process_single_chat.call_count == 1, "不置顶了就该照常回"

    def test_全量同步把没有红点的置顶会话也登记上(self, tmp_path, monkeypatch):
        """置顶行常常一条未读都没有（他一直自己聊），回复轮看不见它，只有同步那一趟能。"""
        from boss_bot.main_loop import UnifiedBotLoop
        loop = _make_loop(tmp_path, monkeypatch)
        loop._running = True
        loop._chat_handler.check_health.return_value = "ok"
        loop._chat_handler.get_all_chats.return_value = [
            _row(0, "陈女士", "湖南云幻科技", pinned=True),
            _row(1, "王女士", "长沙数据标注"),
        ]
        loop._chat_handler.enter_chat.return_value = True
        loop._chat_handler.read_all_messages.return_value = [_hr("在吗")]
        loop._chat_handler.read_selected_row.return_value = {"name": "陈女士",
                                                            "company": "湖南云幻科技"}
        loop._chat_handler.get_job_name.return_value = "数据标注"
        UnifiedBotLoop._full_sync_chats(loop)
        written = [c.args[:2] for c in loop._msg_store.set_pinned.call_args_list]
        assert written == [("陈女士", True), ("王女士", False)], written


    def test_跟进轮要说清置顶的那路没追(self, tmp_path, monkeypatch):
        from boss_bot.main_loop import UnifiedBotLoop
        loop = _make_loop(tmp_path, monkeypatch)
        loop.config.reply.followup_enabled = True
        loop._in_quiet_hours = lambda now=None: False
        loop._followup_last_scan = None
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("陈女士", [_hr("好的"), _me("有经验的，方便发份简历吗")],
                  company="湖南云幻科技", pinned=True, updated_at=_ago(12)),
        ]
        loop._send_followup = MagicMock()
        logs = []
        loop._log = lambda *a, **k: logs.append(a[-1])
        UnifiedBotLoop._run_followup_round(loop)
        loop._send_followup.assert_not_called()
        assert any("置顶" in str(x) for x in logs), f"静默跳过会让他以为这几路也有人追：{logs}"


class 存档里存着置顶Test:
    def test_置顶标记写进存档也能撤销(self, tmp_path):
        from boss_bot.message_store import MessageStore
        st = MessageStore(base_dir=tmp_path, account_index=0)
        st.merge_messages(chat_name="陈女士", new_messages=[_hr("你什么时候到岗")],
                          job_name="数据标注", company="湖南云幻科技")
        st.set_pinned("陈女士", True, company="湖南云幻科技")
        got = [c for c in st.get_all_chats_detail() if c["chat_name"] == "陈女士"][0]
        assert got["pinned"] is True, "工具名单读的是存档，标记不落盘它们看不见"
        st.set_pinned("陈女士", False, company="湖南云幻科技")
        got = [c for c in st.get_all_chats_detail() if c["chat_name"] == "陈女士"][0]
        assert not got.get("pinned"), "取消置顶后机器要重新接手"


class 补发简历与工具绕开置顶Test:
    def test_补发简历跳过置顶会话(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("江女士", [_hr("方便发份简历给我吗")], company="孤波", pinned=True),
            _conv("刘女士", [_hr("方便发份简历给我吗")], company="珍岛集团"),
        ]
        rows = [_row(0, "江女士", "孤波", pinned=True),
                _row(1, "刘女士", "珍岛集团")]
        loop._chat_handler.get_all_chats.return_value = rows
        entered = []
        loop._chat_handler.enter_chat.side_effect = lambda info: (
            entered.append(info["name"]) or True)
        loop._chat_handler.read_all_messages.return_value = [_hr("方便发份简历给我吗")]
        loop._chat_handler.get_job_name.return_value = "数据标注"
        loop._reply_engine.can_reply.return_value = True
        loop._state_store.resume_sent.return_value = False
        logs = []
        loop._log = lambda *a, **k: logs.append(a[-1])
        loop._backfill_pending_resumes()
        assert "江女士" not in entered, f"置顶的那路是他自己聊的：{entered}"
        assert "刘女士" in entered, f"没置顶的照常补：{entered}"
        assert any("置顶" in str(x) for x in logs), logs

    def test_批量补拒名单里没有置顶会话(self, monkeypatch):
        import tools.decline_unwanted_inbound as T
        convs = [_conv("陈女士", [_hr("普工包吃住，加个微信")],
                       company="湖南云幻科技", job_name="长白班普工包吃住18元一小时",
                       pinned=True),
                 _conv("王女士", [_hr("普工包吃住，加个微信")],
                       company="长沙数据标注", job_name="长白班普工包吃住18元一小时")]

        class _Store:
            def __init__(self, *a, **k):
                pass

            def get_all_chats_detail(self):
                return convs

        monkeypatch.setattr(T, "MessageStore", _Store)
        rows, total = T.owed(limit=20)
        assert [r["name"] for r in rows] == ["王女士"], rows
        assert total == 1, f"置顶那条要从待拒名单里摘干净：{total}"

    def test_补点联系方式工具不动置顶会话(self, monkeypatch):
        import tools.accept_pending_contacts as T
        ask = "我想要一份您的电话号码，您是否同意？"
        convs = [_conv("陈女士", [_hr(ask)], company="湖南云幻科技", pinned=True),
                 _conv("王女士", [_hr(ask)], company="长沙数据标注")]

        class _Store:
            def __init__(self, *a, **k):
                pass

            def get_all_chats_detail(self):
                return convs

        monkeypatch.setattr(T, "MessageStore", _Store)
        todo, total = T.pending_rows(limit=20)
        assert [r["chat_name"] for r in todo] == ["王女士"], \
            f"置顶那路的卡片由他自己决定：{todo}"
        assert total == 1, f"总数也要摘干净，不然界面写着 2 单待办：{total}"

