# -*- coding: utf-8 -*-
"""面板自己的标准输出落盘：无声死掉的时候得留下现场。

实测 2026-10-27：面板最后一条日志是 10-25 11:08:59，之后到下一次开机为止
整个项目没有再被写过任何一个文件。Windows 系统日志给的是
Kernel-Power 41「The system has rebooted without cleanly shutting down first」+
EventLog 6008「The previous system shutdown at 10:58:37 on 2026-10-25 was unexpected」，
10-25 11:09:16 重新起来一次、20:48 又起来一次。也就是说那两个号是被整台机器
断电/硬重启带走的，不是代码崩的。

可这件事过去只能去翻事件日志倒推，面板自己一点痕迹都没有：tools/start_accounts.py
让人去看 `data/panel_stderr.log`，那个文件是 10-23 某一次用
PowerShell -RedirectStandardError 拉起来时留下的，0 KB；双击 start.bat 那条路
根本不经过它，面板的 print 和线程 traceback 全打在控制台窗口上，窗口一关就没了。
空文件看着像"没报错"，其实等于没装表。

这里只补三件事：
1) 起与停各留一行（带 PID），"面板什么时候在跑"不用再猜；
2) 没接住的异常落到同一个文件——logging 只收 logger 的话，print 和 excepthook
   走的是 stderr，boss_bot.log 里天然没有这一族；
3) 屏幕上照旧看得见。他是双击窗口跑的，不能为了留档把窗口变成哑的。

只 append，涨过阈值整份挪进 logs/kept/ 而不是删掉（跟 cookie 备份、归档盒、
logs/ 那几条留档口径一致）。stdout 和 stderr 共用一个落盘口：两份各开一个句柄
的话，轮换那一下必然被另一个句柄锁住，rename 就失败。
"""

import atexit
import os
import sys
import threading
import time
from pathlib import Path

日志相对路径 = "logs/panel_io.log"
留档目录名 = "kept"
上限字节 = 5 * 1024 * 1024
_接管标记 = "_boss_bot_panel_io"


def 默认根() -> Path:
    return Path(__file__).resolve().parent.parent


def _挪进留档(路径: Path) -> Path:
    """整份挪走不删：跟 Cookie/归档那几条一个口径。"""
    留档 = 路径.parent / 留档目录名
    留档.mkdir(parents=True, exist_ok=True)
    去 = 留档 / ("%s_%s%s" % (路径.stem, time.strftime("%Y%m%d-%H%M%S"), 路径.suffix))
    try:
        路径.replace(去)
        return 去
    except OSError:
        # 挪不动（被别的句柄占着）就原地留着，下一趟再挪。为了挪不动就不写日志
        # 是本末倒置——这条出口存在的意义就是"下一次还能查到东西"。
        return 路径


class _落盘:
    """一个文件、一把锁、超阈值自己挪走再开。写坏了不许把打印本身弄坏。"""

    def __init__(self, 路径, 上限=上限字节):
        self._路径 = Path(str(路径))
        self._上限 = int(上限)
        self._锁 = threading.Lock()
        self._文件 = None
        self._字节 = 0
        self._开()

    def _开(self):
        self._路径.parent.mkdir(parents=True, exist_ok=True)
        self._文件 = open(str(self._路径), "a", encoding="utf-8", errors="replace")
        self._字节 = self._文件.tell()

    def 写(self, 文):
        try:
            with self._锁:
                长 = len(str(文).encode("utf-8", "replace"))
                if self._字节 + 长 > self._上限:
                    self._文件.close()
                    _挪进留档(self._路径)
                    self._开()
                self._文件.write(文)
                self._文件.flush()
                self._字节 += 长
        except Exception:
            pass

    def 收场(self):
        self.写("│面板退出│ pid=%s 时间=%s\n"
                % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))


class _复写:
    """往控制台写一份、往文件写一份。"""

    def __init__(self, 原, 盘):
        self._原 = 原
        self._盘 = 盘

    def write(self, 文):
        try:
            self._原.write(文)
        finally:
            self._盘.写(文)
        return len(文)

    def flush(self):
        try:
            self._原.flush()
        except Exception:
            pass

    def writelines(self, 行们):
        # 不自己接住的话，这条路会经 __getattr__ 直通控制台，文件里就少了那几行。
        for 一 in 行们:
            self.write(一)

    def __getattr__(self, 名):
        # fileno/isatty/encoding 之类一律转给真正的那个流：werkzeug、socketio
        # 和一些第三方库会问，转不出去就是启动时一句 AttributeError。
        return getattr(self._原, 名)


def 接管(根: str = None, 路径: str = None) -> Path:
    """把 sys.stdout/stderr 各接一路进 logs/panel_io.log，返回那个文件的路径。

    重复调用只接一层（面板内多次入口、测试导入都会撞到这儿）：叠两层复写就等于
    一行字在文件里写两遍。
    """
    if getattr(sys.stdout, _接管标记, False):
        return Path(sys.stdout._panel_io_去处)
    目标 = Path(路径) if 路径 else Path(根 or 默认根()) / 日志相对路径
    盘 = _落盘(目标)
    盘.写("│面板启动│ pid=%s 时间=%s\n"
          % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
    sys.stdout = _复写(sys.stdout, 盘)
    sys.stderr = _复写(sys.stderr, 盘)
    for 流 in (sys.stdout, sys.stderr):
        setattr(流, _接管标记, True)
        setattr(流, "_panel_io_去处", str(目标))
    atexit.register(盘.收场)
    return 目标
