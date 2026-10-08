# -*- coding: utf-8 -*-
"""主日志跨午夜后不能再写一个字。

监控那条线（每 20 分钟一轮）读的就是 logs/boss_bot.log 里当天的 🚫/面试策略那些行。
2026-10-07 23:59:50 之后文件停住，10-08 凌晨查"这一单为什么没拒"只能去翻 messages/ 存档——
不是当天没干活，是日志没落下来。

根因在 Windows：TimedRotatingFileHandler 到点要 rename 当前文件，
只要有任何进程还握着这个文件（面板、回复线程、另一个脚本），rename 就报 WinError 32，
logging 打完 "--- Logging error --- … doRollover" 之后把手里的流关掉，
此后所有 record 都进了黑洞。data/panel_stderr.log 里那段 traceback 就是它的遗言。
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.safe_log import SafeTimedRotatingFileHandler


def _emit(handler, n, 起=0):
    for i in range(起, 起 + n):
        handler.emit(logging.LogRecord("t", logging.INFO, "p", 1, f"第 {i} 条", None, None))
    handler.flush()


class 轮转失败不吞日志Test:
    def test_rename失败后面还照样写(self, tmp_path, monkeypatch):
        import os
        log = tmp_path / "boss_bot.log"
        h = SafeTimedRotatingFileHandler(str(log), when="midnight", backupCount=7,
                                         encoding="utf-8")
        try:
            _emit(h, 3)
            monkeypatch.setattr(os, "rename",
                                lambda *a: (_ for _ in ()).throw(OSError(32, "used")))
            h.doRollover()                      # 直接撞那一下
            _emit(h, 3, 起=3)                   # 之后的三条必须还在
            文 = log.read_text(encoding="utf-8")
            assert "第 5 条" in 文, 文[-300:]
            assert "第 0 条" in 文
        finally:
            h.close()

    def test_轮转失败要留一行自证(self, tmp_path, monkeypatch):
        """静默失败是最贵的那种失败：下一次还得靠人猜日志为什么没了。"""
        import os
        log = tmp_path / "boss_bot.log"
        h = SafeTimedRotatingFileHandler(str(log), when="midnight", encoding="utf-8")
        try:
            _emit(h, 1)
            monkeypatch.setattr(os, "rename",
                                lambda *a: (_ for _ in ()).throw(OSError(32, "used")))
            h.doRollover()
            assert "轮转失败" in log.read_text(encoding="utf-8")
        finally:
            h.close()

    def test_面板用的是这个处理器(self):
        src = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
        assert "SafeTimedRotatingFileHandler" in src, "还在用会吞日志的那个"
        assert "boss_bot.log" in src

    def test_import顺序不能压过sys_path那行(self):
        """app.py 是直接在 flask-version/ 下跑的：提前 import boss_bot.* 会 ModuleNotFoundError，
        面板起都起不来——这行 import 必须在 sys.path.insert 之后。"""
        src = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
        assert src.index("from boss_bot.safe_log import") > src.index("sys.path.insert(0, str(PROJECT_ROOT))")
