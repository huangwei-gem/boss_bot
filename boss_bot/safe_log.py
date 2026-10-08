# -*- coding: utf-8 -*-
"""到点轮转失败也别把往后的日志吞掉。

Windows 上 `TimedRotatingFileHandler` 的午夜轮转要 rename 当前文件，只要有另一个进程
还握着它（面板、回复线程、巡检脚本都算），rename 就报 WinError 32；标准库打完
"--- Logging error ---" 之后把流关掉，此后每条 record 都落进黑洞。
实测 2026-10-07 23:59:50 之后 logs/boss_bot.log 一个字没再写，而那两个号照常投了一夜——
第二天查"这一单为什么没拒"只能去翻 messages/ 存档。
"""
import logging
from logging.handlers import TimedRotatingFileHandler


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """轮转只是"归档没成功"，不是"今天的日志不用记了"。

    改名失败就继续写原文件，并在文件里留一行自证——
    宁可让一个文件长两天，也不能让当天没有任何痕迹。
    """

    def doRollover(self):
        try:
            super().doRollover()
        except OSError as e:
            self._reopen_after_failed_rollover()
            self._note_failure(e)

    def _reopen_after_failed_rollover(self):
        try:
            if self.stream:
                self.stream.close()
        except OSError:
            pass
        self.stream = self._open()

    def _note_failure(self, e):
        try:
            记 = logging.LogRecord("safe_log", logging.WARNING, __name__, 0,
                                   f"⚠️ 日志轮转失败（{type(e).__name__}: {e}），"
                                   f"继续写原文件，当天日志不会中断", None, None)
            super().emit(记)
        except Exception:
            pass

    def emit(self, record):
        """自己接管一次 emit：标准库在 shouldRollover 之后任何异常都会静默丢掉这条。"""
        try:
            if self.shouldRollover(record):
                self.doRollover()
            TimedRotatingFileHandler.emit(self, record)
        except OSError:
            try:
                self._reopen_after_failed_rollover()
                TimedRotatingFileHandler.emit(self, record)
            except Exception:
                self.handleError(record)
        except Exception:
            self.handleError(record)


__all__ = ["SafeTimedRotatingFileHandler"]
