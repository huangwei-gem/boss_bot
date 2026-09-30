# -*- coding: utf-8 -*-
"""多账号**运行层**的并发测试。

以前多账号的测试全在接口层和前端层（记录归属、数据范围切换、配置覆盖），
"两个号真的同时跑起来"这条路一次都没被测过——于是 2026-09-29 出现两件事：
① 第二个浏览器开出来是空的（端口/profile 共用），② 一号被踢出登录后
它的失效处理差点动手改到另一个号的会话文件。这两类都必须锁在运行层。

全程离线：BrowserManager 被打桩，不发任何消息、不起任何真实浏览器。
"""
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.unified_config import UnifiedConfig


def _cfg():
    cfg = UnifiedConfig()
    cfg.greet.accounts = []
    from boss_bot.unified_config import AccountConfig, JobConfig
    cfg.greet.accounts = [
        AccountConfig(name="主账号", enabled=True, cookie_file="zhipin_cookies.json",
                      jobs=[JobConfig(query="数据分析", city="上海", enabled=True)]),
        AccountConfig(name="账号2", enabled=True, cookie_file="zhipin_cookies_1.json",
                      jobs=[JobConfig(query="景观施工图", city="长沙", enabled=True)]),
    ]
    cfg.browser.debug_port = 9222
    return cfg


def _manager(cfg=None):
    """构造 manager，浏览器全部打桩；返回 (manager, 传给 BrowserManager 的参数列表)"""
    seen = []

    class FakeBrowserManager:
        def __init__(self, config=None, account_index=0, port=None, user_data_dir=None):
            seen.append({"account_index": account_index, "port": port,
                         "user_data_dir": str(user_data_dir or "")})
            self._instance = MagicMock()

        def get_instance(self):
            return self._instance

        def get_reply_tab_id(self):
            return None

        def close(self):
            pass

    with patch("boss_bot.main_loop.BrowserManager", FakeBrowserManager):
        from boss_bot.main_loop import MultiAccountManager
        mgr = MultiAccountManager(config=cfg or _cfg())
    return mgr, seen


class BrowserIsolationTest:
    def test_两个号各自端口和用户目录(self):
        """这条锁的是"第二个浏览器打开没内容"的根因：共用 profile"""
        _, seen = _manager()
        ports = [s["port"] for s in seen]
        assert ports == [9222, 9223], f"端口没按号分开：{ports}"
        dirs = [s["user_data_dir"].replace("\\", "/") for s in seen]
        assert dirs[0] != dirs[1], f"两个号共用一个浏览器用户目录：{dirs}"
        assert dirs[0].endswith("account_0") and dirs[1].endswith("account_1"), dirs

    def test_浏览器实例不是同一个对象(self):
        mgr, _ = _manager()
        a = mgr._loops[0].browser_manager
        b = mgr._loops[1].browser_manager
        assert a is not b


class ConfigIsolationTest:
    def test_每个号拿到自己的配置副本(self):
        mgr, _ = _manager()
        assert mgr._loops[0].config is not mgr._loops[1].config, \
            "共用一份对象，一个号热重载会把另一个号的判分阈值一起改掉"

    def test_按号覆盖不串(self):
        cfg = _cfg()
        cfg.greet.accounts[1].settings = {"ai": {"match_threshold": 80}}
        mgr, _ = _manager(cfg)
        assert mgr._loops[0].config.ai.match_threshold != \
            mgr._loops[1].config.ai.match_threshold

    def test_招呼语各说各话(self):
        cfg = _cfg()
        cfg.greet.accounts[0].greeting_message = "主号这句"
        cfg.greet.accounts[1].greeting_message = "二号这句"
        mgr, _ = _manager(cfg)
        for idx, want in ((0, "主号这句"), (1, "二号这句")):
            eng = mgr._loops[idx]._greet_engine or MagicMock()
            got = mgr._loops[idx].config.greet.accounts[idx].greeting_message
            assert got == want, f"号{idx} 读到 {got!r}"


class LoginStateIsolationTest:
    def test_一号被踢登录不污染另一号(self):
        mgr, _ = _manager()
        a, b = mgr._loops[0], mgr._loops[1]
        a._needs_login = True
        a._logged_in = False
        st = mgr.get_status()
        by = {x["index"]: x for x in st.get("accounts") or []}
        assert by[0]["needs_login"] is True
        assert by[1].get("needs_login") in (False, None), \
            f"主号的登录墙漏到账号2：{by[1]}"

    def test_失效归档只动本账号的cookie文件(self, tmp_path, monkeypatch):
        """并发跑时最坏的一类串：A 号判定失效，把 B 号的会话文件也搬走了"""
        mgr, _ = _manager()
        a, b = mgr._loops[0], mgr._loops[1]
        fa, fb = tmp_path / "zhipin_cookies.json", tmp_path / "zhipin_cookies_1.json"
        fa.write_text("[]", encoding="utf-8")
        fb.write_text("[]", encoding="utf-8")
        a._cookie_file = lambda: str(fa)
        b._cookie_file = lambda: str(fb)
        arch = tmp_path / "stale"
        for loop in (a, b):
            monkeypatch.setattr(loop, "_stale_cookie_dir", lambda: str(arch))
        a._discard_stale_cookies("主账号登录墙")
        assert not fa.exists()
        assert fb.exists(), "主号的失效处理把账号2 的 Cookie 也动了"
        assert len(list(arch.iterdir())) == 1

    def test_登录等待只看本账号浏览器(self):
        mgr, _ = _manager()
        a, b = mgr._loops[0], mgr._loops[1]
        assert a._has_live_auth_cookie(a.browser_manager.get_instance()) == \
            b._has_live_auth_cookie(b.browser_manager.get_instance())
        inst = MagicMock()
        inst._get_all_cookies.return_value = [
            {"name": "wt2", "value": "x", "expires": time.time() + 60}]
        assert a._has_live_auth_cookie(inst) is True


class StartStopIsolationTest:
    def _quiet_loop(self, loop):
        """把真正的运行线程换成假线程，只验证管理器的编排"""
        started = []

        class FakeThread:
            def __init__(self, target=None, args=(), name="", daemon=None):
                self.name = name
                self._target = target

            def start(self):
                started.append(self.name)

            def is_alive(self):
                return True

            def join(self, timeout=None):
                pass

        loop._test_started = started
        return FakeThread

    def test_停一个号不影响另一个号(self):
        mgr, _ = _manager()
        for loop in mgr._loops.values():
            loop._running = True
        mgr.stop_account(0)
        assert mgr._loops[0]._running is False
        assert mgr._loops[1]._running is True, "停一个号把另一个也带停了"

    def test_状态汇总里两号各报各的阶段(self):
        mgr, _ = _manager()
        a, b = mgr._loops[0], mgr._loops[1]
        a._running = True
        a._needs_login = True                      # → waiting_login
        b._running = True
        b._needs_login = False
        b._logged_in = True
        b._current_mode = "greet"                  # → running
        by = {x["index"]: x for x in mgr.get_status().get("accounts") or []}
        assert by[0]["phase"] == "waiting_login"
        assert by[1]["phase"] == "running", f"主号在等登录，账号2 的阶段被带走了：{by[1]}"

    def test_一个号启动失败不影响另一个号启动(self):
        mgr, _ = _manager()
        boom = MagicMock(side_effect=RuntimeError("端口被占"))
        mgr._loops[0].start = boom
        mgr._loops[1]._running = False
        with patch.object(type(mgr._loops[1]), "start", lambda self: setattr(self, "_running", True)):
            mgr.start()
        assert mgr._loops[1]._running is True, "主号起不来，账号2 就不跑了"


class RecordOwnershipTest:
    def test_并发写记录各归各号(self, tmp_path):
        from boss_bot.reply_record import GreetRecordStore
        from boss_bot.greet_engine import GreetEngine
        store = GreetRecordStore(path=str(tmp_path / "greet.json"))
        mgr, _ = _manager()
        for idx, loop in mgr._loops.items():
            eng = MagicMock()
            loop._greet_engine = eng
            eng.account_index = idx
            eng._greet_store = store
            eng._last_ai_result = {}
            eng._last_ai_duration_ms = 0
            eng._last_ai_model = "m"
            eng._last_ai_system_prompt = None
            eng._last_ai_user_prompt = None
            eng._last_ai_raw_response = None
            eng._account_label = lambda idx=idx: f"号{idx}"
            eng._greeting_for = lambda job: ("这句", "账号自定义")
            GreetEngine._record_greet(eng, {"job_name": f"岗位{idx}", "url": f"u{idx}"},
                                      is_greeted=True, actual_greeting_sent="hi")
        recs = [r.to_dict() for r in store.get_all()]
        assert len(recs) == 2, recs
        assert sorted(r["account_index"] for r in recs) == [0, 1], recs
        assert {r["account_name"] for r in recs} == {"号0", "号1"}, recs

    def test_两号统计互不加到对方头上(self):
        mgr, _ = _manager()
        mgr._loops[0]._stats_dict["greet_applied"] = 7
        mgr._loops[1]._stats_dict["greet_applied"] = 3
        by = {x["index"]: x for x in mgr.get_status().get("accounts") or []}
        assert by[0]["stats"]["greet_applied"] == 7
        assert by[1]["stats"]["greet_applied"] == 3
