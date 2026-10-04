# -*- coding: utf-8 -*-
"""回复轮要能从存档补漏，并且会说"追到面试"那句。

用户口径（2026-10-04）：「为什么你现在的回复记录失效了啊，你只是打招呼了但是
没有回复别人啊，我要的是一直约到面试为止」

两处根因都在这一层：
1) 回复轮只点侧栏带红点的会话，而红点被启动全量同步自己清掉了，侧栏又是虚拟列表
   一屏只渲染 ~20 行 —— 289 轮"0 个未读"，同期存档里压着真提问没人接；
2) 我们说完、对方沉默之后没人再开口，漏斗就停在"已沟通"。
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


def _make_loop(tmp_path, monkeypatch):
    from boss_bot.unified_config import UnifiedConfig, AccountConfig, JobConfig
    cfg = UnifiedConfig()
    cfg.greet.accounts = [
        AccountConfig(name="主账号", enabled=True, cookie_file="a0.json",
                      jobs=[JobConfig(query="数据分析", city="长沙", enabled=True)]),
    ]
    cfg.browser.debug_port = 9222

    class FakeManager:
        def __init__(self, config=None, account_index=0, port=None, user_data_dir=None):
            self.instance = MagicMock()
            self.instance.url = "https://www.zhipin.com/web/geek/chat"
            self.search = MagicMock()
            self.search.url = "https://www.zhipin.com/web/geek/chat"

        def get_instance(self):
            return self.instance

        def get_search_page(self):
            return self.search

        def get_chat_page(self):
            return self.instance

        def get_reply_tab_id(self):
            return None

    with patch("boss_bot.main_loop.BrowserManager", FakeManager):
        from boss_bot.main_loop import MultiAccountManager
        mgr = MultiAccountManager(config=cfg)
    loop = mgr._loops[0]
    loop._log = lambda *a, **k: None
    loop._state_store = MagicMock()
    loop._state_store.is_paused.return_value = False
    loop._msg_store = MagicMock()
    loop._reply_engine = MagicMock()
    loop._chat_handler = MagicMock()
    loop._stats = MagicMock()
    loop._emit_reply_event = MagicMock()
    # 跟进次数落盘：用例绝不能写进真实 data/
    monkeypatch.setattr("boss_bot.main_loop.FOLLOWUP_STATE_FILE",
                        tmp_path / "followup_state.json")
    loop._followup_state = {}
    return loop


def _conv(name, messages, company="", job_name="数据分析", account_index=0, updated_at=None):
    return {"chat_name": name, "company": company, "job_name": job_name,
            "account_index": account_index,
            # 存档里的消息只带 "10:50" 这种相对时间，读不出绝对时间时靠 updated_at 兜底
            "updated_at": _ago(2) if updated_at is None else updated_at,
            "messages": messages}


def _hr(text, mid="100", time=""):
    return {"is_mine": False, "text": text, "time": time, "mid": mid, "kind": "bubble"}


def _me(text, mid="200", time=""):
    return {"is_mine": True, "text": text, "time": time, "mid": mid, "kind": "bubble"}


def _ago(hours):
    return (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


class OwedFromArchiveTest:
    """欠回复的会话要能自己进候选，不靠红点。"""

    def test_存档里没接的提问要补进候选(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_hr("你好"), _me("您好，我对岗位很有兴趣"),
                            _hr("大四还有课吗", mid="300")], company="珍岛集团"),
        ]
        got = loop._archive_owed_chats([])
        assert [g["name"] for g in got] == ["刘女士"], "红点没了就当没消息可回——这条必须补回来"
        assert got[0]["company"] == "珍岛集团"
        assert "大四还有课" in got[0]["preview"]

    def test_已在未读候选里的不重复补(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_hr("大四还有课吗")], company="珍岛集团"),
        ]
        unread = [{"name": "刘女士", "company": "珍岛集团", "unread_count": 1}]
        assert loop._archive_owed_chats(unread) == []

    def test_没有公司名的不补(self, tmp_path, monkeypatch):
        """侧栏实测有 4 组重名昵称，公司空着点开就是猜人。"""
        loop = _make_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("陈女士", [_hr("在吗")], company=""),
        ]
        assert loop._archive_owed_chats([]) == []

    def test_别的账号的存档不进来(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop.account_index = 1
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_hr("大四还有课吗")], company="珍岛集团", account_index=0),
        ]
        assert loop._archive_owed_chats([]) == []

    def test_同一条没到重试窗口不重复补(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_hr("大四还有课吗")], company="珍岛集团"),
        ]
        assert len(loop._archive_owed_chats([])) == 1
        loop._owed_last_scan = 0          # 绕开扫描节流，只看单条会话的重试窗口
        assert loop._archive_owed_chats([]) == [], "对不上行就每轮重滚一次侧栏是空转"

    def test_每轮补的数量受配置限制(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop.config.reply.owed_per_round = 2
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv(f"HR{i}", [_hr("在吗")], company=f"公司{i}") for i in range(5)
        ]
        assert len(loop._archive_owed_chats([])) == 2

    def test_太旧的账不翻(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop.config.reply.owed_max_age_hours = 1
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_hr("大四还有课吗")], company="珍岛集团", updated_at=_ago(72)),
        ]
        assert loop._archive_owed_chats([]) == []

    def test_存档扫描有节流(self, tmp_path, monkeypatch):
        """回复轮不到 10 秒一轮，每轮把 600 多个会话文件读一遍是白读；
        8 条回复挤在 9 秒里发出去也太像机器。"""
        loop = _make_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_hr("大四还有课吗")], company="珍岛集团"),
        ]
        assert len(loop._archive_owed_chats([])) == 1
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("陈女士", [_hr("在吗")], company="爱森电商"),
        ]
        assert loop._archive_owed_chats([]) == [], "60 秒内第二次扫就不该再读盘"
        assert loop._msg_store.get_all_chats_detail.call_count == 1


class ReplyRoundMergeTest:
    def test_本轮候选是未读加存档欠回复(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._running = True
        loop._reply_paused = False
        loop._chat_handler.get_unread_chats.return_value = [
            {"name": "徐女士", "company": "准雀教育", "unread_count": 1, "index": 0},
        ]
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_hr("大四还有课吗")], company="珍岛集团"),
        ]
        loop._process_single_chat = MagicMock()

        loop._run_reply_round()

        names = [c.args[0]["name"] for c in loop._process_single_chat.call_args_list]
        assert names == ["徐女士", "刘女士"], f"只处理带红点的会话就是这次失效的全部原因：{names}"

    def test_取对方最新消息要用过滤后的判据(self):
        """平台自己塞的"竞争者PK"卡片实测占 248/303，回它就是对着空气说话。"""
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._process_single_chat)
        assert "inbound_body(" in src
        assert 'msg.get("text") or msg.get("card_text")' not in src

    def test_首轮深扫到底之后每轮只看靠前那些(self, tmp_path, monkeypatch):
        """每轮都滚到底太贵；BOSS 按活跃时间排序，靠前的几十行足够日常用。"""
        loop = _make_loop(tmp_path, monkeypatch)
        loop._running = True
        loop._reply_paused = False
        loop._process_single_chat = MagicMock()

        loop._run_reply_round()
        assert loop._chat_handler.get_unread_chats.call_args.kwargs["max_rounds"] == 30
        loop._owed_last_scan = 0.0
        loop._run_reply_round()
        assert loop._chat_handler.get_unread_chats.call_args.kwargs["max_rounds"] == 12


class FollowupRoundTest:
    def _due_loop(self, tmp_path, monkeypatch, live=None, state=None):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._running = True
        loop._reply_paused = False
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("张女士", [_hr("你好"), _me("您好，方便约个面试吗？")],
                  company="伍爱创意", updated_at=_ago(3)),
        ]
        loop._followup_state = state if state is not None else {}
        # 深夜闸门（23:00–07:00 不主动追）有它自己的用例，这里测的是"该不该追"的判定。
        # 不钉住它，晚上 23 点以后跑这套就会 5 条全红——结果取决于几点跑的。
        loop._in_quiet_hours = lambda now=None: False
        loop._chat_handler.enter_chat.return_value = True
        loop._chat_handler.read_selected_row.return_value = {
            "name": "张女士", "company": "伍爱创意"}
        loop._chat_handler.read_all_messages.return_value = (
            live if live is not None
            else [_hr("你好"), _me("您好，方便约个面试吗？")])
        loop._chat_handler.get_job_name.return_value = "数据分析"
        loop._chat_handler.send_text.return_value = True
        loop.config.reply.followup_after_hours = 1
        return loop

    def test_静默够久要主动追一句(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 1
        sent = loop._chat_handler.send_text.call_args.args[0]
        assert "面试" in sent and "数据分析" in sent
        assert loop._stats_dict["reply_sent"] == 1

    def test_每小时回复额度用光就不追(self, tmp_path, monkeypatch):
        """面板上"每小时最多回复"管的是所有主动发送，跟进不能绕过。"""
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._reply_engine.can_reply.return_value = False
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_追成功要占掉一次额度(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._run_followup_round()
        loop._reply_engine.record_reply.assert_called_once()

    def test_到次数上限不再追(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch, state={
            "0|张女士|伍爱创意": {"times": 2, "last_at": _ago(48)}})
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0, "追第三句就是骚扰"

    def test_两次之间要隔够(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch, state={
            "0|张女士|伍爱创意": {"times": 1, "last_at": _ago(2)}})
        loop.config.reply.followup_gap_hours = 24
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_扫描频率没到就不扫(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._followup_last_scan = datetime.now()
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_关了就完全不追(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop.config.reply.followup_enabled = False
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_页面上轮到对方说话就不追(self, tmp_path, monkeypatch):
        """存档会滞后：HR 刚回了问题还约了时间，这时追"约面试"是答非所问。"""
        loop = self._due_loop(
            tmp_path, monkeypatch,
            live=[_hr("你好"), _me("方便约个面试吗？"), _hr("周三下午三点你有空吗", mid="400")])
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_HR已拒绝不再追(self, tmp_path, monkeypatch):
        loop = self._due_loop(
            tmp_path, monkeypatch,
            live=[_hr("不好意思，不太合适哦"), _me("好的，谢谢您")])
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_对不上是谁就不发(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._chat_handler.read_selected_row.return_value = {
            "name": "另一位女士", "company": "别家"}
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_演练模式只判不发(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop.config.dry_run = True
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0
        assert loop._followup_state == {}, "没发出去却把次数记掉，真发时就少追一次"

    def test_追过要记账并留回复记录(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._run_followup_round()
        rec = loop._followup_state["0|张女士|伍爱创意"]
        assert rec["times"] == 1 and rec["last_at"]
        kwargs = loop._reply_engine._add_record.call_args.kwargs
        assert kwargs["reply_source"] == "followup"
        loop._msg_store.append_bot_message.assert_called_once()

    def test_次数落盘重启不丢(self, tmp_path, monkeypatch):
        from boss_bot.main_loop import UnifiedBotLoop
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._run_followup_round()
        assert UnifiedBotLoop._load_followup_state().get(
            "0|张女士|伍爱创意", {}).get("times") == 1

    def test_第二次换一句说法(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch, state={
            "0|张女士|伍爱创意": {"times": 1, "last_at": _ago(30)}})
        loop.config.reply.followup_gap_hours = 0
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 1
        assert "还在招吗" in loop._chat_handler.send_text.call_args.args[0], "复读同一句最像机器人"

    def test_孤儿动作行不追(self, tmp_path, monkeypatch):
        """messages/ 里真有这种文件：整份对话只有一条 [简历已发送]，公司也空着。

        按姓名点开会错人，按公司点开没人理我们——两种都是发错对象。
        """
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("江女士", [{"sender": "bot", "text": "[简历已发送]", "is_mine": True,
                             "kind": "action", "mid": ""}], company="",
                  job_name="数据分析18_25K上海", updated_at=_ago(30)),
        ]
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_从没聊上过话的冷联系人不追(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("刘女士", [_me("您好，我对贵司的数据分析岗位很感兴趣")],
                  company="湖南百川智媒科技", updated_at=_ago(30)),
        ]
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0, "给没理过我们的人群发约面试是骚扰"

    def test_平台卡片不算聊上过话(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._msg_store.get_all_chats_detail.return_value = [
            _conv("王女士", [
                _me("您好，我对岗位很感兴趣"),
                {"is_mine": False, "kind": "card", "text": "", "mid": "300",
                 "card_text": "你与该职位竞争者PK情况 共人投递，你超过竞争者 建议你 查看详细分析"},
            ], company="陈克明食品", updated_at=_ago(30)),
        ]
        loop._run_followup_round()
        assert loop._chat_handler.send_text.call_count == 0

    def test_跟进轮挂在回复线程里(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._reply_loop)
        assert "_run_followup_round()" in src, "方法没人调用等于没做"

    def test_深夜不主动追(self):
        """半夜三点冒出一句"约面试"最像机器人，白天被 HR 翻到反而掉好感。"""
        from datetime import datetime as DT
        from boss_bot.main_loop import UnifiedBotLoop
        assert UnifiedBotLoop._in_quiet_hours(DT(2026, 10, 4, 23, 30)) is True
        assert UnifiedBotLoop._in_quiet_hours(DT(2026, 10, 4, 3, 0)) is True
        assert UnifiedBotLoop._in_quiet_hours(DT(2026, 10, 4, 6, 59)) is True
        assert UnifiedBotLoop._in_quiet_hours(DT(2026, 10, 4, 7, 0)) is False
        assert UnifiedBotLoop._in_quiet_hours(DT(2026, 10, 4, 22, 59)) is False

    def test_静默时段一轮扫不出去(self, tmp_path, monkeypatch):
        loop = self._due_loop(tmp_path, monkeypatch)
        loop._in_quiet_hours = lambda now=None: True
        loop._send_followup = MagicMock()
        loop._run_followup_round()
        assert loop._send_followup.call_count == 0


class ReplyBudgetTest:
    """面板上那个"每小时最多回复"必须真管着回复轮，而且只数真发出去的句子。"""

    def test_额度用完本轮收工并说明原因(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._running = True
        loop._reply_paused = False
        loop._chat_handler.get_unread_chats.return_value = [
            {"name": "徐女士", "company": "准雀教育", "unread_count": 1, "index": 0},
        ]
        loop._reply_engine = MagicMock()
        loop._reply_engine.can_reply.return_value = False
        loop._reply_engine._max_replies_per_hour = 60
        loop._process_single_chat = MagicMock()
        logs = []
        loop._log = lambda *a, **k: logs.append(a[-1])

        loop._run_reply_round()

        loop._process_single_chat.assert_not_called()
        assert any("每小时" in str(x) for x in logs), f"要留下为什么停手的话：{logs}"

    def test_额度还在就照常处理(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path, monkeypatch)
        loop._running = True
        loop._reply_paused = False
        loop._chat_handler.get_unread_chats.return_value = [
            {"name": "徐女士", "company": "准雀教育", "unread_count": 1, "index": 0},
        ]
        loop._reply_engine = MagicMock()
        loop._reply_engine.can_reply.return_value = True
        loop._process_single_chat = MagicMock()

        loop._run_reply_round()

        assert loop._process_single_chat.call_count == 1

    def test_每小时额度只数真发出去的句子(self):
        """跳过、人工接管、侧栏对不上行都不算发送：把这些也计数，
        一小时内点过 30 个没回成的会话就把真回复全挡在外面。"""
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._handle_reply_action)
        anchor = "if self._chat_handler.send_text(content):"
        assert anchor in src
        before, after = src.split(anchor, 1)
        assert "record_reply()" not in before, \
            "发送之前不占额度（函数结尾那种无条件计数就是老写法）"
        assert "self._reply_engine.record_reply()" in after.split("else:", 1)[0], \
            "发送成功那一路要占额度"
