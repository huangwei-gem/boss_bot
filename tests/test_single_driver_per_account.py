# -*- coding: utf-8 -*-
"""一个号只能有一套线程在动它的浏览器。

线上证据（2026-10-08，logs/boss_bot.log）：
    19:25:13 [账号2] ━━━ 开始回复轮次 ━━━（深扫=是）
    19:25:13 [账号2] ━━━ 开始回复轮次 ━━━（深扫=否）
同一秒两套线程给同一个号开轮，19:20-20:00 这样并发开轮的时刻有 12 个；
面板自己也报了「打招呼侧报登录失效，但复核是 uncertain（另一个线程正在动这个浏览器）」。
代价是账号2 在 19:33:32 被判登录失效、Cookie 归档进 data/stale_cookies/，
页面从此停在手机号+短信验证码框——用户 20:4x 截图里那个"需要登录 Boss 直聘"就是它。

为什么会两套：`stop()` 只把 `_running` 置 False 并 join 10 秒，而那一刻旧线程正卡在
逐个点开 254 个会话的全量同步里；`start()` 又把同一个对象的 `_running` 翻回 True，
于是旧线程在下一个检查点看见"还在跑"就继续活着，新线程又起一套。

修法：每次 start 换一个运行号（run id），线程只认自己那一份——
运行号不是自己的，就到点收手，不再碰浏览器。
"""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.main_loop import UnifiedBotLoop  # noqa: E402


def bare_loop():
    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp.logs = []
    lp._log = lambda level, msg: lp.logs.append(f"[{level}] {msg}")
    return lp


class 运行号Test:
    def test_每次启动换一个新的运行号(self):
        lp = bare_loop()
        lp._run_id = 0
        lp._run_tls = threading.local()
        lp._running = False
        lp._stop_event = threading.Event()
        lp._init_thread = lp._greet_thread = lp._reply_thread = None
        起过的 = []
        lp._init_and_run = lambda: 起过的.append(threading.current_thread().name)
        lp.start()
        第一号 = lp._run_id
        lp._running = False
        lp.start()
        assert lp._run_id != 第一号, "运行号没换，旧线程无从知道自己该收手"

    def test_旧线程到点自己收手(self):
        """模拟旧回复线程：它手上那份运行号已经不是当前的了"""
        lp = bare_loop()
        lp._run_id = 7
        lp._running = True                      # 新 start 把它翻回 True 了
        我的 = 6                                # 旧线程开轮时拿到的是 6
        assert lp._run_stale(我的) is True
        assert lp._run_stale(lp._run_id) is False

    def test_全量同步逐个会话之间要检查运行号(self):
        """19:23 那套僵尸线程就是卡在这一趟里：254 个会话要点 25~50 分钟"""
        import inspect
        源 = UnifiedBotLoop._full_sync_chats
        text = inspect.getsource(源)
        assert "_run_stale" in text, "逐个点开的长循环里必须每轮看一眼运行号"
        at = text.index("for chat_info in chats")
        assert "_run_stale" in text[at:], "检查要在循环体内，放循环外等于没检查"

    def test_两条主循环都要检查(self):
        import inspect
        for 名 in ("_reply_loop", "_greet_loop"):
            text = inspect.getsource(getattr(UnifiedBotLoop, 名))
            assert "_run_stale" in text, f"{名} 没有运行号检查，僵尸线程会一直驱动浏览器"
