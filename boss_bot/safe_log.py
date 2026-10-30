# -*- coding: utf-8 -*-
"""到点轮转失败也别把往后的日志吞掉，更别把日志删掉。

Windows 上 `TimedRotatingFileHandler` 的午夜轮转要 rename 当前文件，只要有另一个进程
还握着它（面板、回复线程、巡检脚本都算），rename 就报 WinError 32；标准库打完
"--- Logging error ---" 之后把流关掉，此后每条 record 都落进黑洞。
实测 2026-10-07 23:59:50 之后 logs/boss_bot.log 一个字没再写，而那两个号照常投了一夜——
第二天查"这一单为什么没拒"只能去翻 messages/ 存档。

10-23 又踩到第二层：改名失败以后 `rolloverAt` 不往前推（标准库只在轮转**成功**时才重算），
于是每条 record 都再撞一次 rename；而"撞失败"时要写的那条自述又走 `super().emit()`，
`FileHandler.emit` 自己会先 `shouldRollover()`→`doRollover()` → **回到我们这个失败的分支**，
递归。主日志里因此留下 1947 行一模一样的轮转 WARNING（连着 637、655 两串，毫秒递减），
真正把 INFO 挤在后面很久才写得进去。这一版把三件事一起收口：
① 失败要退避，不许每条 record 重试；② 自述直接写流，不再走 emit；
③ 轮转成功也不许标准库按 `backupCount` 把老日志 `os.remove` 掉——那是删用户数据，
   留多久由 `log_retention`（只挪走进 logs/kept/）说了算。
"""
import logging
import time
from logging.handlers import TimedRotatingFileHandler

# 改名失败以后隔多久再试一次。填 0 等于回到"每条 record 撞一次"的老样子。
轮转失败重试秒 = 600


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """轮转只是"归档没成功"，不是"今天的日志不用记了"，更不是"老日志可以删了"。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 同一场失败只在文件里留一条自述；换了原因（换个文件名占用）再说一遍。
        self._最近说的 = ""
        # 递归闸门：自述那条记录绝对不许再触发一次轮转判定。
        self._正在轮转 = False

    def doRollover(self):
        if self._正在轮转:
            return
        self._正在轮转 = True
        try:
            try:
                super().doRollover()
            except OSError as e:
                self._重开原文件()
                self._失败后退避()
                self._说一句失败(e)
        finally:
            self._正在轮转 = False

    def _重开原文件(self):
        try:
            if self.stream:
                self.stream.close()
        except OSError:
            pass
        self.stream = self._open()

    def _失败后退避(self):
        """关键的一条：把下一次尝试推到 10 分钟以后。

        标准库只在 `doRollover()` 走完整条路才重算 `rolloverAt`，失败时它原封不动，
        `shouldRollover()` 于是对每条 record 都返回 True——这就是 1947 行刷屏的引擎。
        这里直接钉在"从现在起 10 分钟"，不拿旧值做加法：旧值可能是三天前的某个午夜，
        加完还在过去，等于没退避。轮转后来成功了标准库会自己算回下一个午夜。
        """
        self.rolloverAt = time.time() + 轮转失败重试秒

    def _说一句失败(self, e):
        签 = "%s:%s" % (type(e).__name__, str(e)[:120])
        if self._最近说的 == 签:
            return            # 同一场失败只说一遍，不许刷屏
        self._最近说的 = 签
        记 = logging.LogRecord("safe_log", logging.WARNING, __name__, 0,
                               "⚠️ 日志轮转失败（%s: %s），继续写原文件；10 分钟后再试一次，"
                               "期间同类失败不再重复说" % (type(e).__name__, str(e)[:160]),
                               None, None)
        try:
            行 = self.format(记)
        except Exception:
            return
        try:
            # 直写流，绕开 FileHandler.emit 里那句 shouldRollover——那里会再进一次
            # doRollover，失败分支又调到本方法，就是原来那个递归。
            if self.stream and not self.stream.closed:
                self.stream.write(行 + self.terminator)
                self.stream.flush()
        except Exception:
            pass

    def getFilesToDelete(self):
        """轮转成功也不许按 backupCount 删老日志——这里是用户唯一的追查痕迹。

        标准库这条会 `os.remove` 掉超出份数的 dated 文件；本项目的口径是"过期只挪走不删"
        （`flask-version/log_retention.py` 把老日志整盒搬进 logs/kept/），保留多久由那一边定。
        """
        return []

    def emit(self, record):
        """自己接管一次 emit：标准库在 shouldRollover 之后任何异常都会静默丢掉这条。"""
        try:
            if self.shouldRollover(record):
                self.doRollover()
            TimedRotatingFileHandler.emit(self, record)
        except OSError:
            try:
                self._重开原文件()
                TimedRotatingFileHandler.emit(self, record)
            except Exception:
                self.handleError(record)
        except Exception:
            self.handleError(record)


__all__ = ["SafeTimedRotatingFileHandler", "轮转失败重试秒"]
