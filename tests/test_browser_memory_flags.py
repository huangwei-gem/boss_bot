# -*- coding: utf-8 -*-
"""浏览器才是内存大头，启动参数要真的带上省内存的那几个——而且不许拿指纹换。

2026-10-05 实测：cloakbrowser 16 个进程合计 3023 MB，面板自己只有 90 MB，
所以"占用太高"要动的是浏览器，不是 Python。标签页本身不涨（DOM 1362 节点、
侧栏 40 行、气泡 12 条），大头在进程架构和"再也用不上的历史页面"。

2026-10-06 再量（tools/measure_browser_memory.py，两轮、同页面、独立临时 profile）：
    旧参数                        2643 MB/实例（每号一个 346 MB 的 gpu-process）
    + --in-process-gpu            2383 MB/实例   ← 现在用的，两个号合计省 ~520 MB
    + 渲染进程上限压到 1           2440 MB/实例   ← 没再省，多一个崩溃连坐，弃
    + --js-flags 堆上限 512        2436 MB/实例   ← 只多省 4 MB，冒 OOM 险，弃
    + 关软件光栅化 → WebGL 变 no-webgl，那是 BOSS 风控要读的家底，弃
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import _launch_windows  # noqa: E402


def _args():
    return inspect.getsource(_launch_windows)


def test_不留历史岗位页():
    """打招呼是同一个标签页在几十个 job_detail 之间跳，
    BackForwardCache 会把上一页整个留在内存里攒着，而这些页面永远不会再回去。"""
    assert "BackForwardCache" in _args().split("--disable-features=")[1][:80]


def test_同站标签页挤一个渲染进程():
    assert "--renderer-process-limit=2" in _args(), \
        "上限收到 1 实测省不到内存（1251→1300），还多一个崩溃连坐"


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


def test_GPU_子进程折进主进程():
    """--disable-gpu 在 --headless=new 下并不真的去掉 GPU 子进程，
    实测每号还挂一个 346 MB 的 gpu-process；--in-process-gpu 才折得掉。"""
    assert "--in-process-gpu" in _args()


def test_不许把_webgl_关掉():
    src = _args()
    assert "software-rasterizer" not in src, \
        "关掉软件光栅化和 --disable-gpu 叠一起会把 WebGL 打死，实测报 no-webgl"
    assert "--js-flags" not in src, "堆上限实测只多省 4 MB，不值得冒页面 OOM"


def test_启动参数能临时叠加():
    # A/B 实测靠这个口子，没有它就只能改代码再重启
    assert "extra_args" in inspect.signature(_launch_windows).parameters
