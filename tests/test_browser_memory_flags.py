# -*- coding: utf-8 -*-
"""浏览器才是内存大头，启动参数要真的带上省内存的那几个。

实测（2026-10-05 22:5x，两个号都在跑）：cloakbrowser 16 个进程合计 3023 MB，
而面板自己只有 90 MB——所以"占用太高"要动的是浏览器，不是 Python。
标签页本身不涨（DOM 1362 个节点、侧栏 40 行、气泡 12 条），
说明省下来的是进程架构和"再也用不上的历史页面"。
"""
import inspect

from boss_bot.browser_launcher import _launch_windows


def _args():
    return inspect.getsource(_launch_windows)


def test_不留历史岗位页():
    """打招呼是同一个标签页在几十个 job_detail 之间跳，
    BackForwardCache 会把上一页整个留在内存里攒着，而这些页面永远不会再回去。"""
    src = _args()
    assert "BackForwardCache" in src.split("--disable-features=")[1][:80]


def test_同站标签页挤一个渲染进程():
    assert "--renderer-process-limit" in _args()


def test_关掉后台服务进程():
    """崩溃上报、组件更新、同步这些在自动化里毫无用处，各占一个进程。"""
    src = _args()
    for flag in ("--disable-breakpad", "--disable-component-update", "--disable-sync"):
        assert flag in src, f"{flag} 没带上，白留一个后台进程"


def test_反检测开关没被动过():
    """省内存不许拿指纹换：cloakbrowser 二进制 + AutomationControlled 是底线，
    也不许把窗口改成默认无头（BOSS 对无头更敏感，登录也得靠这个窗口扫码）。"""
    src = _args()
    assert "--disable-blink-features=AutomationControlled" in src
    assert "if headless:" in src, "无头必须只在用户显式配置时启用"
