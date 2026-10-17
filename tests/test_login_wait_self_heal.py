# -*- coding: utf-8 -*-
"""启动时等人工登录：等不到要接着等，不许把整条流水线退掉。

2026-10-16 05:18:21 面板把主账号的窗口摆到桌面上写"登进去后这一轮自己继续"，
05:23:22 就写「登录超时，主循环退出」把两条腿全撤了。他后来真登进去了（10:12
只读探 9222：两张页都停在 `chengshi/c101250800` 城市首页，不是登录页；
zhipin_cookies.json 的 mtime 是登录成功那一下存下的），可是没人在等了——
主账号 `running=false phase=stopped` 一直挂到 10:12，当天 0 单。

`login.wait_timeout` 是给"这一次看多久"定的，不是给"这个号还跑不跑"定的。
把人睡着的 5 分钟当成放弃整条腿的理由，就是把这句话变成了空头承诺。
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _造循环(monkeypatch, account_index=0):
    with patch("boss_bot.main_loop.BrowserManager"):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop(account_index=account_index)
    lp._log = lambda level, msg: lp.日志.append((level, msg))
    lp.日志 = []
    lp._stop_event = threading.Event()
    lp._running = True
    lp.browser_manager = MagicMock()
    lp.browser_manager.get_instance.return_value = MagicMock()
    monkeypatch.setattr(lp, "_cookie_file", lambda: "zhipin_cookies.json")
    monkeypatch.setattr(lp, "_ensure_login_window", lambda *a, **k: True)
    monkeypatch.setattr(lp, "_save_cookies_if_logged_in", lambda *a, **k: True)
    monkeypatch.setattr(lp, "_finish_login_window", lambda: None)
    # 走到等待之前那段"读 Cookie 文件 + 复核会话页"要跳过：这条测试只管等待
    monkeypatch.setattr(lp, "_login_state_now", lambda instance: "uncertain")
    monkeypatch.setattr(lp, "_has_live_auth_cookie", lambda instance: False)
    monkeypatch.setattr(lp, "_interruptible_sleep", lambda s: None)
    return lp


class 等不到要继续等Test:

    def test_第一轮超时第二轮等到就算成功(self, monkeypatch):
        """核心那条：wait_timeout 过一轮不等于这个号不干了"""
        lp = _造循环(monkeypatch)
        轮次 = []

        def 假等(instance, cookie_file):
            轮次.append(1)
            return len(轮次) >= 2        # 第一轮没登进来，第二轮他扫完了

        monkeypatch.setattr(lp, "_wait_for_login", 假等)

        assert lp._handle_login() is True, "等到第二轮就该继续，不许退出"
        assert len(轮次) == 2

    def test_每轮超时都要留一条还在等的自述(self, monkeypatch):
        """面板日志得说清"这个号在等人登录"，不能悄悄挂着 stopped 让人以为冻住"""
        lp = _造循环(monkeypatch)
        轮次 = []

        def 假等(instance, cookie_file):
            轮次.append(1)
            return len(轮次) >= 3

        monkeypatch.setattr(lp, "_wait_for_login", 假等)
        lp._handle_login()

        念 = [m for _, m in lp.日志 if "等人登录" in m]
        assert len(念) == 2, "超时的每一轮都要有一条，实际: %r" % (lp.日志,)
        assert "第 1 轮" in 念[0] and "第 2 轮" in 念[1], 念

    def test_登录成功后照旧换回配置形态(self, monkeypatch):
        """等待改成多轮以后，"登完把临时借的有头还回去"这一手不能被挤掉"""
        lp = _造循环(monkeypatch)
        还过 = []
        monkeypatch.setattr(lp, "_wait_for_login", lambda i, c: len(还过) == 0 or True)
        monkeypatch.setattr(lp, "_finish_login_window", lambda: 还过.append(1))
        lp._login_headed_temp = True

        assert lp._handle_login() is True
        assert 还过, "登录确认后必须调 _finish_login_window，否则桌面挂着有头"
        assert lp._needs_login is False
        assert lp._logged_in is True


class 退出等待的只有这两种Test:
    """等待只在"人叫停了"或"浏览器真没了"时结束，别的情况都得继续等。"""

    def test_收到停止信号立刻退出(self, monkeypatch):
        lp = _造循环(monkeypatch)
        lp._stop_event.set()
        轮次 = []
        monkeypatch.setattr(lp, "_wait_for_login",
                            lambda i, c: (轮次.append(1), False)[1])

        assert lp._handle_login() is False
        assert len(轮次) == 1, "叫停了还要再开一轮等待就是在空转"

    def test_运行标志被撤也要退出(self, monkeypatch):
        """面板点停止后 _running 变假，等待线程必须跟着收，不然停不干净"""
        lp = _造循环(monkeypatch)
        轮次 = []

        def 假等(instance, cookie_file):
            轮次.append(1)
            lp._running = False
            return False

        monkeypatch.setattr(lp, "_wait_for_login", 假等)

        assert lp._handle_login() is False
        assert len(轮次) == 1

    def test_浏览器没了要如实失败(self, monkeypatch):
        """人把窗口关了 / 进程被杀：句柄拿不到就别对着空气轮询"""
        lp = _造循环(monkeypatch)
        次 = []

        def 假等(instance, cookie_file):
            次.append(1)
            lp.browser_manager.get_instance.return_value = None
            return False

        monkeypatch.setattr(lp, "_wait_for_login", 假等)

        assert lp._handle_login() is False
        assert len(次) == 1
        assert any("浏览器" in m for _, m in lp.日志), lp.日志


class 面板那条一次性等待不动Test:
    """「我已登录」按钮起的后台线程是一次性的：等不到就标超时，别常驻。

    它不在跑流水线，长期挂着只会占一个线程和一个 _login_wait_active 顶位。
    """

    def test_超时仍然收口(self, monkeypatch):
        lp = _造循环(monkeypatch, account_index=1)
        lp._login_wait_active = False
        monkeypatch.setattr(lp, "_wait_for_login", lambda i, c: False)
        lp._needs_login = True

        lp._finish_manual_login(MagicMock())

        assert lp._login_reason == "login_timeout"
        assert lp._logged_in is not True


class 承诺和代码要对得上Test:
    """摆窗口那句话写的是"登进去后这一轮自己继续"，代码不许反过来退出。"""

    def test_启动路径里没有登录超时退流水线(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = src[src.index("def _handle_login"):src.index("def open_login_page")]
        assert "主循环退出" not in 块, "等登录这一路不许自己判死整条腿"
        assert "wait_timeout" in 块 or "_wait_for_login" in 块

    def test_面板那句话不许再写成会自动继续却又退(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert "登进去后这一轮自己继续" in src, "摆窗口的承诺要留着"
        assert "登录超时，主循环退出" not in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
