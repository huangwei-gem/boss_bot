# -*- coding: utf-8 -*-
"""值守用的"现在每个线程卡在哪一行"出口。

为什么要它：2026-10-16 两条投递腿各停了一个钟头，日志最后一个字是
「已点击沟通按钮，等待输入框…」，之后连一条 DEBUG 都没有。DrissionPage 的
调用带 30 秒超时、超时会抛、抛了我们就落一句 DEBUG——一句都没有，说明线程不是
在"等超时"，是压根没回到 Python 层。光看日志我连着猜错三轮（页面冻住 / 内存
压力 / 判据漏扫），这一份把栈原样抄出来，一次定死。

只读：不点、不发、不动任何引擎对象，就是 `sys._current_frames()` 加格式化。
"""
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLASK_DIR = str(ROOT / "flask-version")


@pytest.fixture
def 面板():
    if FLASK_DIR not in sys.path:
        sys.path.insert(0, FLASK_DIR)
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    return FLASK_APP


class 卡住的哨兵:
    """起一个明确停在某一行上的线程，看抄出来的栈里有没有它。"""

    def __init__(self):
        self.进去 = threading.Event()
        self.放行 = threading.Event()

    def 这一行别再往下(self):
        self.进去.set()
        self.放行.wait(30)


class Test抄栈出口:

    def test_管理器没建起来也要答(self, 面板):
        """卡住的时候恰恰是没人建管理器那种时候，这条路不能依赖它。"""
        面板._multi_manager = None
        回 = 面板.app.test_client().get("/api/debug/stacks")
        assert 回.status_code == 200
        assert "text/plain" in 回.headers.get("Content-Type", "")

    def test_停在某一行上的线程要带函数名被抄出来(self, 面板):
        哨 = 卡住的哨兵()
        线 = threading.Thread(target=哨.这一行别再往下, name="greet_loop-抄栈验证")
        线.daemon = True
        线.start()
        assert 哨.进去.wait(5), "哨兵线程没跑起来"
        try:
            正文 = 面板.app.test_client().get("/api/debug/stacks").get_data(as_text=True)
        finally:
            哨.放行.set()
            线.join(5)
        assert "greet_loop-抄栈验证" in 正文, "线程名没带上，值守对不上是哪条腿"
        assert "这一行别再往下" in 正文, "栈里连函数名都没有，等于没抄"
        assert 'File "' in 正文, "没有源码位置就定不了卡在哪个调用"

    def test_抄的是当前栈而不是历史(self, 面板):
        """用 sys._current_frames 才有"此刻"；format_stack() 抄的是调用方自己。"""
        源 = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
        起 = 源.index("def api_debug_stacks")
        段 = 源[起:起 + 1600]
        assert "sys._current_frames()" in 段
        assert "format_stack" in 段, "光拿到帧不格式化，返回的是空话"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
