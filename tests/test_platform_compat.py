# -*- coding: utf-8 -*-
"""平台差异层的回归。

这层存在的理由：macOS 适配不是一次性翻译，Windows 上每一处"只有我能跑"的写法
都会变成 mac 上的静默失效（端口守卫、窗口收起、破解版浏览器查找都撞过）。
所以每个原语都收一个 平台 参数，在这台 Windows 机器上就能把 mac 分支的结论测出来
——不然测试只能验"Windows 还是好的"，mac 那条永远没人看过。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from boss_bot.platform_compat import (
    写剪贴板, 归一化路径键, 构造苹果脚本, 解析ps进程表, 终止命令, 终止进程,
    破解版可执行名, 破解版路径, 浏览器进程名,
)


class Test浏览器进程名:
    def test_windows只认exe形态(self):
        assert 浏览器进程名("windows") == ("chrome.exe", "msedge.exe")

    def test_mac按裸二进制名筛不到就是筛不到(self):
        名字 = 浏览器进程名("macos")
        assert "chromium" in 名字 and "google chrome for testing" in 名字
        assert all(not n.endswith(".exe") for n in 名字)


class Test写剪贴板:
    def test_windows把文本落文件而不是塞命令行(self):
        # 老坑：文本直接进 powershell 命令行会被引号/换行吃掉
        记 = {}

        def 假跑(命令, **kw):
            记["命令"] = 命令
            记["kw"] = kw
        assert 写剪贴板("第一行\n带'引号'的第二行", 平台="windows", 跑=假跑) is True
        脚本 = 记["命令"][-1]
        assert "Set-Clipboard" in 脚本 and "Get-Content" in 脚本
        assert "带'引号'" not in 脚本, "文本不该出现在命令行里"

    def test_mac用pbcopy喂utf8字节(self):
        记 = {}

        def 假跑(命令, **kw):
            记["命令"] = 命令
            记["kw"] = kw
        assert 写剪贴板("汇报正文", 平台="macos", 跑=假跑) is True
        assert 记["命令"] == ["pbcopy"]
        assert 记["kw"]["input"] == "汇报正文".encode("utf-8")

    def test_剪贴板失败不许把汇报本身带崩(self):
        def 炸(命令, **kw):
            raise OSError("pbcopy 不在")
        with pytest.raises(OSError):
            写剪贴板("x", 平台="macos", 跑=炸)
        # 调用方（wechat_outbox --clip）自己 catch：这条路只是备用出口


def _假文件系统(文件表):
    """文件表 { posix 风格相对路径: 字节数 } → (isfile, getsize)。

    键一律写 posix 路径，比较时把生产代码拼出来的原生分隔符折回来 ——
    这台机器是 Windows，而 mac 分支要在 Windows 上测。
    """
    折 = lambda p: str(p).replace("\\", "/")
    return ((lambda p: 折(p) in 文件表), (lambda p: 文件表[折(p)]))


class Test破解版路径:
    def test_windows找chrome_exe(self):
        存在, 大小 = _假文件系统({"p/cloakbrowser/chrome.exe": 5_000_000})
        assert 破解版路径("p", 平台="windows", 存在=存在, 大小=大小).replace("\\", "/") == \
            "p/cloakbrowser/chrome.exe"

    def test_mac找app包里的真二进制(self):
        # 破解版 mac 分发一般是 .app：真正的可执行在 Contents/MacOS/ 下面，
        # 直接指到 .app 目录 Chrome 起不来（open 和裸 exec 两种玩法要的都不是目录）
        存在, 大小 = _假文件系统({
            "p/cloakbrowser/Chromium.app/Contents/MacOS/Chromium": 5_000_000})
        assert 破解版路径("p", 平台="macos", 存在=存在, 大小=大小).replace("\\", "/") == \
            "p/cloakbrowser/Chromium.app/Contents/MacOS/Chromium"

    def test_mac也认裸二进制(self):
        存在, 大小 = _假文件系统({"p/cloakbrowser/chrome-mac-arm64/Google Chrome for Testing": 7_000_000})
        assert 破解版路径("p", 平台="macos", 存在=存在, 大小=大小).replace("\\", "/") == \
            "p/cloakbrowser/chrome-mac-arm64/Google Chrome for Testing"

    def test_占位文件不算找到(self):
        # Windows 上就有条 ">1MB 才算真 Chrome" 的规矩，mac 一样要守：
        # 装一半的目录里留着几 KB 的 stub，认了它就是把"没装破解版"说成"装好了"
        存在, 大小 = _假文件系统({"p/cloakbrowser/Chromium.app/Contents/MacOS/Chromium": 1024})
        assert 破解版路径("p", 平台="macos", 存在=存在, 大小=大小) == ""

    def test_没有mac版就回空串而不是报错(self):
        # 回空串是调用方"降级用系统浏览器并告警"的前提；抛异常会把启动本身堵死
        存在, 大小 = _假文件系统({"p/cloakbrowser/chrome.exe": 5_000_000})
        assert 破解版路径("p", 平台="macos", 存在=存在, 大小=大小) == ""

    def test_可执行名按平台给(self):
        assert 破解版可执行名("windows") == ("chrome.exe",)
        assert "Chromium" in 破解版可执行名("macos")


class Test解析ps进程表:
    def test_读出端口和profile(self):
        文本 = ('  412  /Applications/X.app/Contents/MacOS/X --remote-debugging-port=9223 '
                '--user-data-dir=/tmp/pf/9223\n')
        记录 = 解析ps进程表(文本)
        assert len(记录) == 1
        assert 记录[0]["pid"] == 412 and 记录[0]["port"] == 9223
        assert 记录[0]["user_data_dir"] == "/tmp/pf/9223"

    def test_路径里有空格也要拆对(self):
        文本 = '  7  /Users/me/Google Chrome for Testing --remote-debugging-port=9222\n'
        assert 解析ps进程表(文本)[0]["port"] == 9222

    def test_没有调试端口的进程不收(self):
        # 守卫只关心"端口上挂的是谁"，把一万行系统进程收进来等于自找误判
        assert 解析ps进程表("  1 /sbin/launchd\n  2 /usr/sbin/syslogd\n") == []

    def test_空行和表头不算进程(self):
        assert 解析ps进程表("  PID  COMMAND\n\n") == []


class Test终止命令:
    def test_windows给taskkill(self):
        assert 终止命令(4321, "windows") == "taskkill /PID 4321 /T /F"

    def test_mac给kill(self):
        # 报错文案里那条命令是要人照着敲的：mac 上写 taskkill 就是让人白敲一次
        assert 终止命令(4321, "macos") == "kill -9 4321"


class Test终止进程:
    def test_windows用taskkill带子进程树(self):
        记 = {}
        assert 终止进程(4321, 平台="windows", 跑=lambda 命令, **kw: 记.update(命令=命令))
        assert 记["命令"][:2] == ["taskkill", "/PID"]
        assert "/T" in 记["命令"], "不连子进程一起收，浏览器会留在桌面上"

    def test_mac用SIGKILL不许喊taskkill(self, monkeypatch):
        收到 = {}

        def 假kill(pid, 信号):
            收到["pid"], 收到["信号"] = pid, 信号

        monkeypatch.setattr(os, "kill", 假kill)
        assert 终止进程(4321, 平台="macos") is True
        # 9 = POSIX SIGKILL。写死数字而不是 signal.SIGKILL：后者在 Windows 上不存在，
        # 这条"在 Windows 上验 mac 分支"的测试会撞 AttributeError 再被 except 吞掉
        assert 收到 == {"pid": 4321, "信号": 9}

    def test_没有pid就不许乱杀(self):
        assert 终止进程(0, 平台="windows", 跑=lambda *a, **k: None) is False
        assert 终止进程(None, 平台="macos") is False


class Test归一化路径键:
    def test_windows折叠大小写与分隔符(self):
        a = 归一化路径键('C:\\Users\\Me\\Bot\\browser_data\\account_0', "windows")
        b = 归一化路径键('c:/users/me/bot/browser_data/account_0/', "windows")
        assert a == b

    def test_mac区分大小写但不区分分隔符尾巴(self):
        # APFS 默认区分大小写，normcase 在 mac 上什么都不做，所以 mac 的比较要自己
        # 保证"尾斜杠/引号"这些写法差异不吃掉，同时不能把大小写也抹平
        assert 归一化路径键("/p/browser_data/account_0/", "macos") == \
            归一化路径键("/p/browser_data/account_0", "macos")
        assert 归一化路径键("/p/Account_0", "macos") != 归一化路径键("/p/account_0", "macos")


class Test构造苹果脚本:
    def test_前置脚本按pid挑窗口(self):
        脚本 = 构造苹果脚本("前置", (412,))
        assert "System Events" in 脚本 and "set frontmost of theProc to true" in 脚本
        assert "unix id is in {412}" in 脚本

    def test_收起脚本不许弹授权框(self):
        # 无人值守的循环里一旦缺辅助功能权限就弹系统对话框，面板会卡在那儿
        assert "always allows" in 构造苹果脚本("收起", (412,)) or \
            "-1719" in 构造苹果脚本("收起", (412,))
