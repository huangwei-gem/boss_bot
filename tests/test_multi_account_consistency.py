# -*- coding: utf-8 -*-
"""多账号重做：账号切换、岗位按号、记录与 BOSS 对齐。

用户现场（2026-10-02 双号同跑）：点左侧「账号2」界面不动；两号岗位集不同却都写着
89 条；日志一片「未配置招呼语，跳过」而招呼语输入框里明明有字；打招呼记录里同
一个岗位被记了几十遍，还带着别的岗位的 AI 分数和一句 AI 建议的招呼语，看着像
真发过。这里锁的都是能跑出行为的那部分。
"""
import os
import time
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from boss_bot.greet_engine import (GREETING_MISSING_REASON, DEFAULT_GREETING,
                                   GreetEngine, classify_greet_skip, pick_greeting)
from boss_bot.reply_record import GreetRecord, GreetRecordStore
from boss_bot.unified_config import UnifiedConfig


def _engine(account_index=0, store=None):
    eng = GreetEngine(MagicMock(), UnifiedConfig(), account_index=account_index)
    eng.running = True
    eng._log = lambda *a, **k: None
    if store is not None:
        eng._greet_store = store
    return eng


def _store(tmp_path, name="greet.json"):
    return GreetRecordStore(path=str(tmp_path / name))


def _row(url, status="skipped", reason="AI判定不匹配: 不要你", account=0,
         when=None, is_skipped=True, is_greeted=False):
    return GreetRecord(job_name="岗位A", job_url=url, status=status,
                       skip_reason=reason, is_skipped=is_skipped,
                       is_greeted=is_greeted, account_index=account,
                       timestamp=when or f"{date.today().isoformat()} 10:00:00")


class TestGreetingHonesty:
    """招呼语输入框里有的字，引擎就不能当没配"""

    def test_历史默认文案在加载时清空(self):
        """旧配置里每个岗位都被塞过同一段默认文案：界面显示"已填"、引擎判"没填"，
        用户只能从日志里看到一片跳过。加载时就把它清掉，让输入框如实为空。"""
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"accounts": [{
            "name": "主账号",
            "jobs": [{"query": "数据分析", "city": "长沙",
                     "greeting_message": DEFAULT_GREETING}],
        }]})
        assert cfg.greet.accounts[0].jobs[0].greeting_message == ""

    def test_岗位自己写的招呼语要留下(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"accounts": [{
            "name": "主账号",
            "jobs": [{"query": "数据分析", "city": "长沙",
                     "greeting_message": "这是我给这个岗位写的话"}],
        }]})
        assert cfg.greet.accounts[0].jobs[0].greeting_message == "这是我给这个岗位写的话"

    def test_清空后账号级招呼语顶上(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"accounts": [{
            "name": "主账号", "greeting_message": "账号通用的那句话",
            "jobs": [{"greeting_message": DEFAULT_GREETING}],
        }]})
        text, source = pick_greeting(cfg.greet.accounts[0].jobs[0].greeting_message,
                                     cfg.greet.accounts[0].greeting_message,
                                     DEFAULT_GREETING)
        assert (text, source) == ("账号通用的那句话", "账号自定义")

    def test_记录里的招呼语不许拿AI建议顶(self):
        """410 条 skipped 记录的 greeting_message 是 AI 现编的话，
        导出的 Excel 与界面看着像"这句发出去了"，实际一条都没发。"""
        eng = _engine()
        eng._last_ai_result = {"score": 82, "is_match": True,
                               "suggested_greeting": "AI 现编的招呼语",
                               "reason": "挺匹配"}
        eng._record_greet({"job_name": "岗位A", "url": "u1", "_ai_result": dict(eng._last_ai_result)},
                          is_skipped=True, skip_reason=GREETING_MISSING_REASON)
        rec = eng._greet_store.get_all()[-1]
        assert rec.greeting_message == ""
        assert rec.ai_suggested_greeting == "AI 现编的招呼语"

    def test_真发出去的记录写实际文案(self):
        eng = _engine()
        eng._last_ai_result = {"score": 82, "is_match": True, "suggested_greeting": "AI 建议"}
        eng._record_greet({"job_name": "岗位A", "url": "u2"},
                          is_greeted=True, actual_greeting_sent="真发出去的那句话")
        rec = eng._greet_store.get_all()[-1]
        assert rec.greeting_message == "真发出去的那句话"


class TestAiResultNotLeakedAcrossJobs:
    """AI 字段必须来自这一条岗位自己那次判分"""

    def test_没判过分的记录不带上一条的分数(self):
        """「已沟通过」的岗位根本没跑 AI，以前却把上一条的 82 分抄了过来"""
        eng = _engine()
        eng._last_ai_result = {"score": 82, "is_match": True, "reason": "上一条岗位的理由",
                               "suggested_greeting": "上一条的建议"}
        eng._last_ai_model = "deepseek-v4-flash"
        eng._record_greet({"job_name": "岗位B", "url": "u3"},
                          is_skipped=True, skip_reason="已沟通过")
        rec = eng._greet_store.get_all()[-1]
        assert rec.ai_score == 0
        assert rec.ai_reason == ""
        assert rec.ai_model == ""

    def test_判过的岗位带自己那份结果(self):
        eng = _engine()
        eng._last_ai_result = {"score": 99, "is_match": True, "reason": "上一条"}
        job = {"job_name": "岗位C", "url": "u4",
               "_ai_result": {"score": 41, "is_match": False, "reason": "这一条的理由"}}
        eng._record_greet(job, is_skipped=True, skip_reason="AI判定不匹配: 这一条的理由")
        rec = eng._greet_store.get_all()[-1]
        assert rec.ai_score == 41
        assert rec.ai_reason == "这一条的理由"


class TestSameDaySameOutcomeRecordedOnce:
    """同一岗位同一天同一个结论只留一条：记录要跟 BOSS 对得上"""

    def test_结论分类(self):
        from boss_bot.greet_engine import (CHAT_REDIRECT_REASON, OFFLINE_JOB_REASON)
        assert classify_greet_skip("已沟通过") == "already"
        assert classify_greet_skip("AI判定不匹配: 岗位要实习") == "ai_reject"
        assert classify_greet_skip(GREETING_MISSING_REASON) == "no_greeting"
        assert classify_greet_skip(CHAT_REDIRECT_REASON) == "already"
        assert classify_greet_skip(OFFLINE_JOB_REASON) == "offline"
        # 验证码/页面断开这类要能重试，不能算"今天已经判过"
        assert classify_greet_skip("投递失败: 未找到输入框") == ""
        assert classify_greet_skip("") == ""

    def test_同日同结论已存在就算重复(self, tmp_path):
        st = _store(tmp_path)
        st.add(_row("u5", reason="AI判定不匹配: 不要你"))
        assert st.has_today("u5", "ai_reject", 0) is True
        assert st.has_today("u5", "no_greeting", 0) is False
        assert st.has_today("u5", "ai_reject", 1) is False   # 另一个号各算一份
        assert st.has_today("u6", "ai_reject", 0) is False

    def test_跨天不再算重复(self, tmp_path):
        st = _store(tmp_path)
        st.add(_row("u7", when="2026-01-01 10:00:00"))
        assert st.has_today("u7", "ai_reject", 0) is False

    def test_引擎按跳过类别去重(self, tmp_path):
        eng = _engine(store=_store(tmp_path))
        eng._greet_store.add(_row("u8", reason="AI判定不匹配: 不要你"))
        assert eng._already_recorded_today({"url": "u8"}, "AI判定不匹配: 不要你") is True
        assert eng._already_recorded_today({"url": "u8"}, "已沟通过") is False
        assert eng._already_recorded_today({"url": "u9"}, "AI判定不匹配: 不要你") is False


class TestGreetRoundOrder:
    """招呼语没配就不该先烧 AI 预算；同一结论当天只落一条"""

    def _loop(self, greeting="", recorded_today=False):
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop()
        loop._running = True
        loop._greet_paused = False
        loop._log = MagicMock()
        loop._metrics = MagicMock()
        loop._dry_run = lambda *a, **k: False
        loop._hot_reload_config = MagicMock()
        loop._build_greet_tasks = lambda: [{
            "query": "数据分析", "city": "长沙", "scroll_pages": 1,
            "message_interval_min": 0, "message_interval_max": 0}]
        loop._stats_dict = {"greet_rounds": 0, "greet_total": 0,
                            "greet_skipped": 0, "greet_applied": 0}
        eng = loop._greet_engine = MagicMock()
        eng._rate_limit_enabled = False
        eng._greeting_for.return_value = (greeting, "岗位配置" if greeting else "未配置")
        eng.search_jobs.return_value = [{"job_name": "岗位A", "url": "u1"}]
        eng._is_already_chatted.return_value = False
        eng._ai_enabled = True
        eng._ai_providers = [{"name": "p"}]
        eng._already_recorded_today.return_value = recorded_today
        eng._analyze_job_with_ai.return_value = ({"score": 90, "is_match": True,
                                                  "reason": "匹配"}, 0.1)
        eng._init_ai.return_value = object()
        eng.send_greeting.return_value = True
        return loop, eng

    def test_没配招呼语不跑AI(self):
        loop, eng = self._loop(greeting="")
        loop._run_greet_round()
        eng._analyze_job_with_ai.assert_not_called()
        eng.send_greeting.assert_not_called()
        reason = eng._record_greet.call_args.kwargs["skip_reason"]
        assert reason == GREETING_MISSING_REASON

    def test_当天记过的同类结论不再重复落库(self):
        loop, eng = self._loop(greeting="", recorded_today=True)
        loop._run_greet_round()
        eng._record_greet.assert_not_called()
        eng._emit_greet_event.assert_not_called()

    def test_配了招呼语照常投递(self):
        loop, eng = self._loop(greeting="本号自己的招呼语")
        loop._run_greet_round()
        eng.send_greeting.assert_called_once()


class TestReplyRecordOnSend:
    """回复真发出去了就要落回复记录：以前只有 socket 事件，刷新一次界面那条就没了"""

    def _loop(self, send_ok):
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop()
        loop.account_index = 0
        loop._chat_handler = MagicMock()
        loop._chat_handler.send_text.return_value = send_ok
        loop._msg_store = MagicMock()
        loop._state_store = MagicMock()
        loop._state_store.was_handled.return_value = False
        loop._state_store.is_paused.return_value = False
        loop._notifier = MagicMock()
        loop._notifier.notify_if_important.return_value = False
        loop._stats = MagicMock()
        loop._stats_dict = {"reply_sent": 0, "reply_skipped": 0, "important_events": 0}
        loop._emit_reply_event = MagicMock()
        loop._log = MagicMock()
        loop._dry_run = lambda *a, **k: False
        loop._reply_engine = MagicMock()
        loop._reply_engine._last_ai_model = "m"
        loop._self_evolve = MagicMock()
        return loop

    def _run(self, loop):
        loop._handle_reply_action("text", "好的，我发您简历", {
            "source": "ai", "intent": "简历"}, "李女士", "数据分析",
            "HR 请求简历", "某某科技")

    def test_发送成功要落一条(self):
        loop = self._loop(True)
        self._run(loop)
        assert loop._reply_engine._add_record.call_count == 1
        kwargs = loop._reply_engine._add_record.call_args.kwargs
        assert kwargs["reply_content"] == "好的，我发您简历"
        assert kwargs.get("is_skipped", False) is False

    def test_发送失败也要落一条(self):
        loop = self._loop(False)
        self._run(loop)
        kwargs = loop._reply_engine._add_record.call_args.kwargs
        assert kwargs["is_skipped"] is True
        assert "发送文字失败" in kwargs["skip_reason"]

    def test_落库必须带会话与岗位(self):
        """801 条回复记录里 352 条 chat_name 为空，界面只能显示「(未知)」；
        账号由本号自己的 ReplyEngine 补，这两条从 loop 的会话身份传进去"""
        loop = self._loop(True)
        self._run(loop)
        kwargs = loop._reply_engine._add_record.call_args.kwargs
        assert kwargs["chat_name"] == "李女士"
        assert kwargs["job_name"] == "数据分析"


class TestAiFailAction:
    """AI 全军覆没时不再默认通过盲投"""

    def _chain_result(self, fail_action, monkeypatch):
        from boss_bot import ai_health
        from boss_bot.greet_engine import AIAnalyzerChain
        # 成败回写体检表会落盘，测试只验容灾链的判定，不碰文件
        monkeypatch.setattr(ai_health, "report_runtime_result", lambda *a, **k: None)
        provider = {"name": "P1", "api_key": "k", "api_base": "https://x/v1",
                    "model": "m"}
        chain = AIAnalyzerChain(providers=[provider], match_threshold=70,
                                log_callback=lambda *a, **k: None,
                                fail_action=fail_action)
        chain._call_provider_api = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("API 请求失败: HTTP Error 429"))
        return chain.analyze_job({"job_name": "岗位", "url": "u"})

    def test_配置skip时判不通过(self, monkeypatch):
        result = self._chain_result("skip", monkeypatch)
        assert result["ai_error"] is True
        assert result["is_match"] is False

    def test_配置default时保持默认通过(self, monkeypatch):
        result = self._chain_result("default", monkeypatch)
        assert result["ai_error"] is True
        assert result["is_match"] is True


class TestUnhealthyListRefreshesAcrossAccounts:
    """一个账号跑完体检，另一个账号不能还拿着 5 分钟前的旧清单跳接口"""

    def _chain(self, tmp_path, monkeypatch):
        from boss_bot import ai_health, greet_engine
        path = tmp_path / "ai_health.json"
        monkeypatch.setattr(ai_health, "HEALTH_FILE", path)
        provider = {"name": "P1", "api_key": "k", "api_base": "https://x/v1",
                    "model": "m"}
        return greet_engine.AIAnalyzerChain(providers=[provider], match_threshold=70,
                                            log_callback=lambda *a, **k: None,
                                            skip_unhealthy=True), path

    def test_体检文件变化后立刻重读(self, tmp_path, monkeypatch):
        import json
        from boss_bot.ai_health import provider_key
        chain, path = self._chain(tmp_path, monkeypatch)
        entry = {"api_base": "https://x/v1", "model": "m", "name": "P1"}
        key = provider_key(entry)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def write(status):
            path.write_text(json.dumps(
                {"updated_at": stamp, "results": {key: {"status": status}}},
                ensure_ascii=False), encoding="utf-8")
            # mtime 落在同一时间戳粒度内时缓存不会失效，测试要造出可区分的两次写
            os.utime(path, (time.time() + 1, time.time() + 1))

        write("unavailable")
        assert chain._unhealthy_names() == {"P1"}
        # 另一个账号（另一个 chain 实例）随后拿到的是刚写好的那份，而不是旧缓存
        write("available")
        assert chain._unhealthy_names() == set()


class TestEventTimestampParity:
    """实时推送的行和轮询回来的行必须是同一条：前端按 岗位+时间戳+状态 去重，
    两边各取一次 now() 就会差一秒，用户看到"刚打的那条闪一下又变成两条"。"""

    def _eng(self, tmp_path):
        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine.__new__(GreetEngine)
        e._greet_store = GreetRecordStore(path=str(tmp_path / "g.json"))
        e.account_index = 1
        e._account_label = lambda: "账号2"
        e._log = lambda *a: None
        e._event_ts = None
        events = []
        e._greet_event_cb = events.append
        return e, events

    def test_岗位推送与落库同一时间戳(self, tmp_path):
        e, events = self._eng(tmp_path)
        ts = e.begin_event_ts()
        job = {"job_name": "岗位A", "url": "u1", "company": "c"}
        e._emit_greet_event(job, "success")
        e._record_greet(job, is_greeted=True, actual_greeting_sent="你好")
        rec = e._greet_store.get_all()[-1]
        assert events[0]["timestamp"] == ts
        assert rec.timestamp == ts

    def test_落库在前推送在后也一样(self, tmp_path):
        e, events = self._eng(tmp_path)
        ts = e.begin_event_ts()
        job = {"job_name": "岗位B", "url": "u2", "company": "c"}
        e._record_greet(job, is_skipped=True, skip_reason=GREETING_MISSING_REASON)
        e._emit_greet_event(job, "skip", skip_reason=GREETING_MISSING_REASON)
        assert e._greet_store.get_all()[-1].timestamp == events[0]["timestamp"] == ts

    def test_换岗位要重开时间戳(self, tmp_path):
        e, _ = self._eng(tmp_path)
        first = e.begin_event_ts("2026-01-01 00:00:00")
        assert e.event_ts() == first
        assert e.begin_event_ts() != first

    def test_回复侧同样共用一个时间戳(self, tmp_path):
        from boss_bot.reply_engine import ReplyEngine
        from boss_bot.reply_record import ReplyRecordStore
        e = ReplyEngine.__new__(ReplyEngine)
        e._record_store = ReplyRecordStore(path=str(tmp_path / "r.json"))
        e._account_name = "账号2"
        e._account_index = 1
        e._event_ts = None
        ts = e.begin_event_ts()
        e._add_record(chat_name="李女士", job_name="数据分析",
                      received_message="发个简历", reply_content="好的",
                      reply_source="ai")
        assert e._record_store.get_all()[-1].timestamp == ts

    def test_没有回复引擎时推送照样要发出去(self):
        """_reply_engine 只在连上浏览器后才建，面板刚起时是 None。
        取时间戳这件事不能把它变成推送的前提，否则事件被 except 吞掉，
        前端回复记录一条都不显示。"""
        import re
        from unittest.mock import patch
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(account_index=1)
        assert loop._reply_engine is None
        got = []
        loop._reply_event_cb = got.append
        loop._emit_reply_event("陈女士", "数据分析师", "在吗", "在的")
        assert len(got) == 1
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                            got[0]["timestamp"])
        assert got[0]["time"] == got[0]["timestamp"][11:19]
