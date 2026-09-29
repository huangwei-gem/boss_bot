"""多账号隔离与数据范围测试

覆盖这一轮修的四个问题：
- 打招呼记录以前恒为 account_index=0、account_name 写成 cookie 文件名，
  切账号时记录根本分不开
- 去重库 chatted_jobs.json 两号共用却各写整档，后写的抹掉先写的 → 重复打招呼
- 按天归档被两个账号各跑一次，第二次归档的是已被清空的空文件 → 丢一天数据
- 记录/聊天/导出/清空接口和前端按钮都不分账号，"暂停账号2"其实停了全部
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from boss_bot.unified_config import (
    UnifiedConfig, AccountConfig, JobConfig,
)


def _two_account_cfg():
    cfg = UnifiedConfig()
    cfg.greet.accounts = [
        AccountConfig(name="主账号", cookie_file="zhipin_cookies.json",
                      jobs=[JobConfig(greeting_message="主号话术")]),
        AccountConfig(name="账号2", cookie_file="zhipin_cookies_1.json",
                      jobs=[JobConfig(greeting_message="二号话术")]),
    ]
    return cfg


def _make_loop(account_index=0, **kwargs):
    from boss_bot.main_loop import UnifiedBotLoop
    with patch("boss_bot.main_loop.BrowserManager"):
        return UnifiedBotLoop(config=_two_account_cfg(),
                              account_index=account_index, **kwargs)


# ─────────────────────────────────────────────
# 记录存储：按账号筛 / 按账号删 / 按账号导出
# ─────────────────────────────────────────────

class RecordStoreAccountScopeTest:

    def _store(self, tmp_path):
        from boss_bot.reply_record import ReplyRecordStore, ReplyRecord
        st = ReplyRecordStore(path=str(tmp_path / "reply_records.json"))
        st.add(ReplyRecord(chat_name="A", account_name="主账号", account_index=0))
        st.add(ReplyRecord(chat_name="B", account_name="账号2", account_index=1))
        st.add(ReplyRecord(chat_name="C", account_name="账号2", account_index=1))
        return st

    def test_按账号索引筛(self, tmp_path):
        st = self._store(tmp_path)
        assert [r.chat_name for r in st.filter(account_index=1)] == ["B", "C"]
        assert [r.chat_name for r in st.filter(account_index=0)] == ["A"]
        assert len(st.filter()) == 3

    def test_只删选定账号(self, tmp_path):
        st = self._store(tmp_path)
        assert st.delete_account(1) == 2
        assert [r.chat_name for r in st.get_all()] == ["A"]

    def test_删除已落盘(self, tmp_path):
        from boss_bot.reply_record import ReplyRecordStore
        st = self._store(tmp_path)
        st.delete_account(1)
        again = ReplyRecordStore(path=str(tmp_path / "reply_records.json"))
        assert [r.chat_name for r in again.get_all()] == ["A"]

    def test_导出按账号过滤(self, tmp_path):
        from boss_bot.reply_record import export_reply_records
        st = self._store(tmp_path)
        out = export_reply_records(format="json", account_index=1, store=st,
                                   output_path=str(tmp_path / "e.json"))
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["total"] == 2
        assert {r["account_index"] for r in data["records"]} == {1}

    def test_打招呼存储同样支持账号维度(self, tmp_path):
        from boss_bot.reply_record import GreetRecordStore, GreetRecord
        st = GreetRecordStore(path=str(tmp_path / "greet_records.json"))
        st.add(GreetRecord(job_name="x", account_index=0))
        st.add(GreetRecord(job_name="y", account_index=1))
        assert [r.job_name for r in st.filter(account_index=1)] == ["y"]
        assert st.delete_account(0) == 1
        assert st.count() == 1


# ─────────────────────────────────────────────
# GreetEngine：记录与实时事件都要带真实账号
# ─────────────────────────────────────────────

class GreetRecordAccountTagTest:

    def _engine(self, idx):
        from boss_bot.greet_engine import GreetEngine
        ge = GreetEngine(MagicMock(), _two_account_cfg(), account_index=idx)
        captured = []
        ge._greet_store = MagicMock()
        ge._greet_store.add.side_effect = captured.append
        return ge, captured

    def test_记录带账号2的索引和名字(self):
        ge, captured = self._engine(1)
        ge._record_greet({"job_name": "数据分析师", "url": "u1"},
                         is_skipped=True, skip_reason="AI判定不匹配")
        rec = captured[0]
        assert rec.account_index == 1
        assert rec.account_name == "账号2"

    def test_不再把cookie文件名当账号名(self):
        ge, captured = self._engine(1)
        ge._record_greet({"job_name": "x", "url": "u"}, is_greeted=True)
        assert not captured[0].account_name.endswith(".json")

    def test_主账号索引为0(self):
        ge, captured = self._engine(0)
        ge._record_greet({"job_name": "x", "url": "u"}, is_greeted=True)
        assert captured[0].account_index == 0
        assert captured[0].account_name == "主账号"

    def test_实时事件也带账号(self):
        ge, _ = self._engine(1)
        cb = []
        ge._greet_event_cb = lambda d: cb.append(d)
        ge._emit_greet_event({"job_name": "x", "url": "u"}, "skip", skip_reason="r")
        assert cb[0]["account_index"] == 1
        assert cb[0]["account_name"] == "账号2"

    def test_回复实时事件也带账号(self):
        loop = _make_loop(1)
        got = []
        loop._reply_event_cb = lambda d: got.append(d)
        loop._emit_reply_event("杨女士", "数据分析师", "在吗", "在的")
        assert got[0]["account_index"] == 1
        assert got[0]["account_name"] == "账号2"


# ─────────────────────────────────────────────
# 去重库：两号共用一份文件，写入必须合并
# ─────────────────────────────────────────────

class ChattedJobsMergeWriteTest:

    def _engines(self, tmp_path, monkeypatch):
        from boss_bot import greet_engine as ge_mod
        db = tmp_path / "chatted_jobs.json"
        monkeypatch.setattr(ge_mod, "CHATTED_DB_FILE", db)
        cfg = _two_account_cfg()
        return (ge_mod.GreetEngine(MagicMock(), cfg, account_index=0),
                ge_mod.GreetEngine(MagicMock(), cfg, account_index=1))

    def _urls(self, tmp_path):
        return set(json.loads(
            (tmp_path / "chatted_jobs.json").read_text(encoding="utf-8")))

    def test_两号各标一个岗位都在库里(self, tmp_path, monkeypatch):
        e0, e1 = self._engines(tmp_path, monkeypatch)
        e0._mark_chatted({"url": "u_main"})
        e1._mark_chatted({"url": "u_two"})
        assert self._urls(tmp_path) == {"u_main", "u_two"}

    def test_我后写不会抹掉对方先写的(self, tmp_path, monkeypatch):
        e0, e1 = self._engines(tmp_path, monkeypatch)
        e0._load_chatted()      # 两个引擎在同一时刻各取一份快照
        e1._mark_chatted({"url": "u_two"})
        e0._mark_chatted({"url": "u_main"})
        urls = self._urls(tmp_path)
        assert "u_two" in urls, "整档覆盖把对方的去重记录冲掉了"
        assert "u_main" in urls

    def test_对方标过的岗位我重读就能看到(self, tmp_path, monkeypatch):
        e0, e1 = self._engines(tmp_path, monkeypatch)
        e0._load_chatted()                       # 先取一份快照
        e1._mark_chatted({"url": "shared"})
        assert e0._is_already_chatted({"url": "shared"}) is False  # 快照还没过期
        e0._chatted_loaded_at = 0.0              # 过了刷新间隔
        assert e0._is_already_chatted({"url": "shared"}) is True

    def test_快照会过期不会永久用(self, tmp_path, monkeypatch):
        e0, e1 = self._engines(tmp_path, monkeypatch)
        e1._mark_chatted({"url": "u_two"})
        e0._chatted_cache = set()
        e0._chatted_loaded_at = 0.0
        assert "u_two" in e0._load_chatted()


# ─────────────────────────────────────────────
# 按天归档：一天只跑一次
# ─────────────────────────────────────────────

class ArchiveRunsOncePerDayTest:

    def _loop(self, tmp_path):
        loop = _make_loop(0)
        loop._archive_dir = tmp_path / "archive"
        loop._archive_dir.mkdir(parents=True, exist_ok=True)
        return loop

    def test_第二个账号看到标记就不再归档(self, tmp_path, monkeypatch):
        from boss_bot import main_loop as ml
        monkeypatch.setattr(ml, "_ARCHIVE_LOCK", threading.Lock())
        monkeypatch.setattr(ml.UnifiedBotLoop, "_reset_record_stores",
                            lambda self_: None)
        calls = []
        real = ml.UnifiedBotLoop._do_archive

        def fake_do(self_, today, prev, marker):
            calls.append(prev)
            real(self_, today, prev, marker)

        monkeypatch.setattr(ml.UnifiedBotLoop, "_do_archive", fake_do)
        a, b = self._loop(tmp_path), self._loop(tmp_path)
        a._last_archived_date = b._last_archived_date = "2020-01-01"
        a._check_and_archive_daily_data()
        b._check_and_archive_daily_data()
        assert len(calls) == 1, "同一天归了两次，第二次归档的是空文件"

    def test_标记文件写的就是今天(self, tmp_path, monkeypatch):
        from boss_bot import main_loop as ml
        monkeypatch.setattr(ml, "_ARCHIVE_LOCK", threading.Lock())
        monkeypatch.setattr(ml.UnifiedBotLoop, "_reset_record_stores",
                            lambda self_: None)
        loop = self._loop(tmp_path)
        loop._last_archived_date = "2020-01-01"
        loop._check_and_archive_daily_data()
        marker = loop._archive_dir / ".last_archived"
        assert marker.read_text(encoding="utf-8").strip(
            ) == datetime.now().strftime("%Y-%m-%d")

    def test_首次启动不清空当天数据(self, tmp_path, monkeypatch):
        from boss_bot import main_loop as ml
        monkeypatch.setattr(ml, "_ARCHIVE_LOCK", threading.Lock())
        cleared = []
        monkeypatch.setattr(ml.UnifiedBotLoop, "_reset_record_stores",
                            lambda self_: cleared.append(1))
        loop = self._loop(tmp_path)
        loop._last_archived_date = None
        loop._check_and_archive_daily_data()
        assert cleared == []

    def test_清空走存储单例不只改文件(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._reset_record_stores)
        assert "_get_reply_store" in src and "_get_greet_store" in src
        assert ".clear()" in src


# ─────────────────────────────────────────────
# 风控事件归属
# ─────────────────────────────────────────────

class WindControlAccountAttributionTest:

    def test_上报带账号索引和名字(self):
        got = []
        loop = _make_loop(1, wind_control_cb=lambda *a, **k: got.append((a, k)))
        loop._emit_wind("检测到验证码", "captcha")
        args, kwargs = got[0]
        assert args == ("检测到验证码", "captcha")
        assert kwargs["account_index"] == 1
        assert kwargs["account_name"] == "账号2"

    def test_没有回调时不炸(self):
        _make_loop(0)._emit_wind("x", "limit")

    def test_三处风控上报都走emit包装(self):
        """绕过 _emit_wind 直接调回调的话，账号信息就又丢了"""
        import pathlib
        import re
        src = pathlib.Path("boss_bot/main_loop.py").read_text(encoding="utf-8")
        direct = re.findall(r"self\._wind_control_cb\(", src)
        assert len(direct) == 1, f"应只有 _emit_wind 里一处直接调用，实际 {len(direct)}"


# ─────────────────────────────────────────────
# 账号阶段与登录原因
# ─────────────────────────────────────────────

class AccountPhaseStatusTest:

    def test_未启动(self):
        assert _make_loop(0).get_status()["phase"] == "stopped"

    def test_刚启动还在初始化(self):
        loop = _make_loop(0)
        loop._running = True
        assert loop.get_status()["phase"] == "starting"

    def test_登录好了才开始跑算运行中(self):
        loop = _make_loop(0)
        loop._running = True
        loop._logged_in = True
        loop._current_mode = "greet"
        assert loop.get_status()["phase"] == "running"

    def test_等登录优先于其他(self):
        loop = _make_loop(0)
        loop._running = True
        loop._logged_in = True
        loop._current_mode = "greet"
        loop._needs_login = True
        assert loop.get_status()["phase"] == "waiting_login"

    def test_登录原因随状态出来(self):
        loop = _make_loop(0)
        loop._login_reason = "cookie_expired"
        assert loop.get_status()["login_reason"] == "cookie_expired"

    def test_确认登录后清掉登录原因(self):
        loop = _make_loop(0)
        loop._login_reason = "no_cookie"
        loop.confirm_login()
        assert loop.get_status()["login_reason"] == ""


# ─────────────────────────────────────────────
# 后端接口按账号过滤
# ─────────────────────────────────────────────

class FlaskAccountScopeTest:

    def _src(self):
        import pathlib
        return pathlib.Path("flask-version/app.py").read_text(encoding="utf-8")

    def test_账号cookie批量接口存在(self):
        assert '/api/accounts/cookies' in self._src()

    def test_记录接口都读账号参数(self):
        import re
        src = self._src()
        for route in ("/api/reply_records", "/api/greet_records",
                      "/api/reply_records/grouped", "/api/chats",
                      "/api/export/reply_records", "/api/export/greet_records",
                      "/api/greet_records/clear", "/api/reply_records/clear"):
            m = re.search(r'@app\.route\("' + re.escape(route) + r'"', src)
            assert m, f"找不到路由 {route}"
            body = src[m.end():m.end() + 2200]
            assert "_account_arg()" in body, f"{route} 没有按账号过滤"

    def test_聊天详情按账号定位(self):
        src = self._src()
        assert "_msg_store(0 if account is None else account)" in src

    def test_状态里带cookie(self):
        assert "_enrich_status" in self._src()

    def test_账号状态缓存有TTL(self):
        src = self._src()
        assert "_COOKIE_STATE_TTL" in src
        assert src.count("_account_cookie_state(") >= 3

    def test_单独启动账号也会拉起状态推送(self):
        src = self._src()
        assert "manager.start_account(idx)" in src
        seg = src[src.index("manager.start_account(idx)"):]
        assert "_ensure_status_pusher()" in seg[:200]

    def test_账号状态落盘通知带账号(self):
        src = self._src()
        assert "def _add_notification(ntype: str, message: str," in src
        assert 'notif["account_index"]' in src


# ─────────────────────────────────────────────
# 前端只有一个数据范围
# ─────────────────────────────────────────────

class FrontendAccountScopeTest:

    def _html(self):
        import pathlib
        return pathlib.Path(
            "flask-version/templates/index.html").read_text(encoding="utf-8")

    def test_没有残留的独立统计范围变量(self):
        assert "metricsScope ===" not in self._html()
        assert "var metricsScope" not in self._html()

    def test_记录与聊天请求都带范围(self):
        html = self._html()
        for call in ("'/api/greet_records' + scopeQs()",
                     "'/api/reply_records/grouped' + scopeQs()",
                     "'/api/export/reply_records' + scopeQs(",
                     "'/api/export/greet_records' + scopeQs(",
                     "'/api/greet_records/clear' + scopeQs()",
                     "'/api/reply_records/clear' + scopeQs()"):
            assert call in html, f"前端少了账号范围参数: {call}"

    def test_控制按钮按范围走per账号路由(self):
        html = self._html()
        for base in ("start", "stop", "pause_greet", "resume_greet",
                     "pause_reply", "resume_reply", "confirm_login"):
            assert f"controlUrl('{base}')" in html, f"{base} 还在打全局接口"
        for legacy in ("fetch('/api/pause_greet'", "fetch('/api/resume_reply'",
                       "fetch('/api/confirm_login'", "fetch('/api/pause_reply'",
                       "fetch('/api/resume_greet'"):
            assert legacy not in html, f"仍有写死的全局控制调用: {legacy}"

    def test_切账号即切范围(self):
        assert "function switchAccount(i) { setDataScope(i); }" in self._html()

    def test_全局控制路由是绝对路径(self):
        """controlUrl 的 all 分支少了 /api/ 前缀，会变成相对路径 404"""
        html = self._html()
        assert "? '/api/' + base" in html
        assert "? base " not in html and "? base\n" not in html

    def test_实时行按范围过滤(self):
        html = self._html()
        assert "if(data && inScope(data)) addGreetRecord(data)" in html
        assert "function inScope(data)" in html

    def test_登录确认只针对要登录的那个号(self):
        html = self._html()
        assert "loginPendingIdx" in html
        assert "'/api/accounts/' + idx + '/confirm_login'" in html

    def test_cookie点会自己刷新(self):
        html = self._html()
        assert "refreshCookieStates(true)" in html
        assert "refreshCookieStates(false)" in html

    def test_账号点区分阶段(self):
        html = self._html()
        assert ".account-tab .acc-status-dot.starting" in html
        assert ".account-tab .acc-status-dot.paused" in html
        assert "waiting_login" in html

    def test_清空会说明会不会波及其他账号(self):
        html = self._html()
        assert "function clearScopeWarn(kind)" in html
        assert "另一个号的记录不受影响" in html

    def test_不再用浏览器级cookie检测点按钮(self):
        """点击红点去启动一个浏览器会和正在跑的会话抢用户数据目录"""
        html = self._html()
        assert "/check_cookie'" not in html


class ProfileDirIsolationTest:
    """两个账号必须各用各的 Chrome 用户目录。

    browser_launcher._launch_windows 里是 `if user_data_dir:` —— 空值时干脆不传
    --user-data-dir，Chrome 就退回默认用户目录。两个账号共用一份 profile 的后果：
    cookie 互相顶掉、第二个浏览器开出来是空的、还会和第一个抢调试端口
    （2026-09-29 主账号就是 "浏览器连接失败 127.0.0.1:9222" 直接退出）。
    """

    def _mgr(self, idx):
        from boss_bot.browser_launcher import BrowserManager
        return BrowserManager(config=None, account_index=idx)

    def test_没传profile也要按账号落到独立目录(self):
        m0, m1 = self._mgr(0), self._mgr(1)
        assert m0._user_data_dir, "空 profile 会让 Chrome 用默认用户目录，两号共用"
        assert m1._user_data_dir
        assert "account_0" in m0._user_data_dir.replace("\\", "/")
        assert "account_1" in m1._user_data_dir.replace("\\", "/")
        assert m0._user_data_dir != m1._user_data_dir

    def test_端口按账号分开(self):
        assert self._mgr(0)._debug_port != self._mgr(1)._debug_port

    def test_全局profile也不能被两个账号共用(self):
        """browser.user_data_dir 一旦填了，两号会指向同一目录——必须按账号再分一层"""
        from boss_bot.browser_launcher import BrowserManager

        class _Cfg:
            user_data_dir = "browser_data/shared"
            headless = False

        dirs = {BrowserManager(config=_Cfg(), account_index=i)._user_data_dir
                for i in (0, 1)}
        assert len(dirs) == 2, f"两号共用同一 profile: {dirs}"


class AccountIdentityFingerprintTest:
    """隔离自检脚本的身份指纹：端口/profile/文件路径都分开，也可能其实是同一个号。

    2026-09-29 实测就是这样：两个槽位的 wt2 一模一样，界面上看着"两个账号"，
    实际是同一个 BOSS 账号登录了两次。所以必须拿登录字段算指纹来比。
    """

    def _fp(self, tool, wt2):
        return tool.identity_fingerprint([
            {"name": "wt2", "value": wt2},
            {"name": "wbg", "value": "wbg-1"},
            {"name": "zp_at", "value": "at-1"},
        ])

    def _load_tool(self):
        import importlib.util, os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "check_account_isolation.py")
        spec = importlib.util.spec_from_file_location("iso_mod", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_同一个wt2算出同一个指纹(self):
        tool = self._load_tool()
        assert self._fp(tool, "WT2-SAME") == self._fp(tool, "WT2-SAME")

    def test_换账号必须指纹不同(self):
        tool = self._load_tool()
        assert self._fp(tool, "WT2-A") != self._fp(tool, "WT2-B")

    def test_风控令牌不同不能冒充换过账号(self):
        """bst/__zp_stoken__ 每次都变，把它们算进指纹会让同一个号看起来像两个号"""
        tool = self._load_tool()
        base = [{"name": "wt2", "value": "SAME"}, {"name": "wbg", "value": "w"}]
        a = tool.identity_fingerprint(base + [{"name": "__zp_stoken__", "value": "X"}])
        b = tool.identity_fingerprint(base + [{"name": "__zp_stoken__", "value": "Y"}])
        assert a == b

    def test_没有登录字段时返回空(self):
        tool = self._load_tool()
        assert tool.identity_fingerprint([{"name": "HMACCOUNT", "value": "z"}]) == ""
