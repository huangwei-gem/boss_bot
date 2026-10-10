# -*- coding: utf-8 -*-
"""macOS 启动路径的回归。

这组测试存在的理由是"假绿"：以前 windows 分支改了参数、mac 分支手抄一份没跟上，
而回归只读 _launch_windows 的源码 —— mac 上跑的是缺反检测参数的那一份，测试却全绿。
所以这里既锁"两条路径共用一套参数"，也在 Windows 上把 mac 分支的行为真跑一遍。
"""
import inspect
from pathlib import Path

import pytest

from boss_bot import browser_launcher as BL
from boss_bot import platform_compat


class Test两条启动路径不许各自手抄:
    def test_windows吃的是共用参数表(self):
        assert "CHROME运行参数" in inspect.getsource(BL._launch_windows)

    def test_mac吃的也是同一张表(self):
        assert "CHROME运行参数" in inspect.getsource(BL._launch_macos)

    def test_反检测那条在表里(self):
        # BOSS 风控读 navigator.webdriver；mac 分支以前抄漏的就是这条
        assert "--disable-blink-features=AutomationControlled" in BL.CHROME运行参数
        assert set(BL.BACKGROUND_FLAGS) <= set(BL.CHROME运行参数)


class Testmac窗口操作:
    def test_收起窗口喊osascript不碰user32(self, monkeypatch):
        记录 = {}

        class _假subprocess:
            @staticmethod
            def run(命令, **kw):
                记录["argv"] = 命令
                return type("R", (), {"returncode": 0, "stderr": b""})()

        monkeypatch.setattr(BL, "_IS_MACOS", True)
        monkeypatch.setattr(BL, "_IS_WINDOWS", False)
        monkeypatch.setattr(BL, "subprocess", _假subprocess)
        assert BL.minimize_browser_windows(4321) == 1
        assert 记录["argv"][0] == "osascript"
        assert "4321" in 记录["argv"][2]

    def test_前置窗口同样走osascript(self, monkeypatch):
        记录 = {}

        class _假subprocess:
            @staticmethod
            def run(命令, **kw):
                记录["argv"] = 命令
                return type("R", (), {"returncode": 0, "stderr": b""})()

        monkeypatch.setattr(BL, "_IS_MACOS", True)
        monkeypatch.setattr(BL, "_IS_WINDOWS", False)
        monkeypatch.setattr(BL, "subprocess", _假subprocess)
        assert BL.restore_browser_windows(4321) == 1
        assert "frontmost" in 记录["argv"][2]

    def test_没给辅助功能授权不许把启动堵死(self, monkeypatch):
        class _假subprocess:
            @staticmethod
            def run(命令, **kw):
                return type("R", (), {"returncode": 1, "stderr": b"-1719"})()

        monkeypatch.setattr(BL, "_IS_MACOS", True)
        monkeypatch.setattr(BL, "subprocess", _假subprocess)
        assert BL.minimize_browser_windows(4321) == 0


class Testmac端口守卫:
    def test_端口上挂着别人的浏览器时mac分支必须拒绝(self, monkeypatch, tmp_path):
        # DrissionPage 在 mac 上是"自己 Popen + 连端口"，所以这道守卫得在这儿，
        # 而且必须在建 profile 目录之前 —— 事故形态就是连着没登录的空壳跑一整天
        def 假守卫(端口, profile, owners=None):
            raise RuntimeError(f"端口 {端口} 上不是本账号的浏览器")

        monkeypatch.setattr(BL, "_is_port_open", lambda h, p: True)
        monkeypatch.setattr(BL, "assert_port_is_free_for", 假守卫)
        with pytest.raises(RuntimeError, match="不是本账号"):
            BL._launch_macos(chrome_path="/no/such/chrome", headless=False,
                             user_agent="", proxy="", viewport_width=1280,
                             viewport_height=800, port=9222,
                             user_data_dir=str(tmp_path / "account_0"))
        assert not (tmp_path / "account_0").exists(), "被拦下了却还把 profile 目录建了出来"


class Test缺破解版时的说法:
    def test_mac要说清放哪儿认哪些形状(self):
        说明 = platform_compat.破解版缺失说明("macos")
        assert "cloakbrowser/" in 说明
        assert "browser_path" in 说明 and "Contents/MacOS" in 说明

    def test_windows不许多塞一句没用的(self):
        assert platform_compat.破解版缺失说明("windows") == ""
