# -*- coding: utf-8 -*-
"""欠着的简历不能只在启动时补那一次。

2026-10-08 19:23:26 线上（logs/boss_bot.log）：
    [主账号] 补扫欠简历的会话：11 个 —— 沈女士、喻江南、王女士、邹女士、宋女士、张墨晗…
    [主账号] 启动全量同步失败（不影响后续未读轮次）:
两行挨着出现——补扫和全量同步套在同一个 try 里，那一刻浏览器正在重建（19:23:49 才
"统一主循环已启动"），补扫第一句 get_all_chats() 就把整段带崩，11 单一条没发出去。
更糟的是这一趟只在启动后跑一次：到 19:34 用户来催"简历啥的都没发成功"时，
它一次都没再试过。

修两点：
1. 补扫自己吞异常——同步炸了不该连累它，它炸了也不该连累同步；
2. 按时间窗再跑：跑成过的隔 30 分钟，上一趟是抛异常结束的隔 5 分钟就来。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boss_bot.main_loop as ml  # noqa: E402
from boss_bot.main_loop import UnifiedBotLoop  # noqa: E402


def loop():
    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp.logs = []
    lp._log = lambda level, msg: lp.logs.append(f"[{level}] {msg}")
    lp._resume_backfill_at = None
    lp._resume_backfill_failed = False
    lp.calls = []
    lp._backfill_pending_resumes = lambda: lp.calls.append("补扫")
    return lp


class 时间窗Test:
    def test_启动那一趟立刻跑(self):
        lp = loop()
        assert lp._resume_backfill_due() is True

    def test_跑成过就要等满这么久(self, monkeypatch):
        lp = loop()
        monkeypatch.setattr(ml, "datetime", _Fake(now=datetime(2026, 10, 8, 19, 23)))
        lp._run_resume_backfill()
        assert lp.calls == ["补扫"]
        monkeypatch.setattr(ml, "datetime", _Fake(now=datetime(2026, 10, 8, 19, 40)))
        lp._run_resume_backfill()
        assert lp.calls == ["补扫"], "17 分钟就重来会把回复轮挤满"

    def test_跑满间隔就再来一趟(self, monkeypatch):
        lp = loop()
        monkeypatch.setattr(ml, "datetime", _Fake(now=datetime(2026, 10, 8, 19, 0)))
        lp._run_resume_backfill()
        monkeypatch.setattr(ml, "datetime",
                            _Fake(now=datetime(2026, 10, 8, 19, 0) +
                                  timedelta(minutes=ml.RESUME_BACKFILL_MINUTES)))
        lp._run_resume_backfill()
        assert lp.calls == ["补扫", "补扫"]

    def test_上一趟崩了隔几分钟就得重试(self, monkeypatch):
        """崩掉那趟一单都没发，还要等满 30 分钟才补，用户看到的就还是「简历没发成功」"""
        lp = loop()
        monkeypatch.setattr(ml, "datetime", _Fake(now=datetime(2026, 10, 8, 19, 23)))

        def 炸():
            raise RuntimeError("与页面的连接已断开")
        lp._backfill_pending_resumes = 炸
        lp._run_resume_backfill()
        assert lp._resume_backfill_failed is True
        monkeypatch.setattr(ml, "datetime",
                            _Fake(now=datetime(2026, 10, 8, 19, 23) +
                                  timedelta(minutes=ml.RESUME_BACKFILL_RETRY_MINUTES)))
        lp._backfill_pending_resumes = lambda: lp.calls.append("补扫")
        lp._run_resume_backfill()
        assert lp.calls == ["补扫"]


class 不许连累别的链Test:
    def test_补扫抛异常只留一行日志(self):
        lp = loop()
        lp._backfill_pending_resumes = lambda: (_ for _ in ()).throw(
            RuntimeError("与页面的连接已断开"))
        lp._run_resume_backfill()          # 不该把这一下抛出去
        assert any("补扫" in m for m in lp.logs), lp.logs

    def test_补扫不再挂在启动同步那个try里(self):
        src = Path(ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        at = src.index("self._full_sync_chats()")
        段 = src[at:src.index("check_interval", at)]
        assert "self._backfill_pending_resumes()" not in 段, \
            "还在同一个 try 里：同步一炸补扫就整趟没了"
        assert "self._run_resume_backfill(force=True)" in 段, "启动时要单独跑一趟补扫"

    def test_每轮回复之后都会照时间窗补(self):
        src = Path(ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        循环 = src[src.index("def _reply_loop"):src.index("def _run_reply_round")]
        跟进 = 循环.index("self._run_followup_round()")
        assert "self._run_resume_backfill()" in 循环[跟进:], \
            "补扫只有启动那一次，欠着的简历等不到下一趟"


class _Fake:
    """只替住 datetime.now()，别的照原样"""
    real = datetime

    def __init__(self, now):
        self._now = now

    def now(self, *a, **k):
        return self._now

    def __getattr__(self, name):
        return getattr(self.real, name)
