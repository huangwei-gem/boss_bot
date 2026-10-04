# -*- coding: utf-8 -*-
"""读一个会话的历史消息不该死等四秒。

实测（2026-10-05 日志时间线）：一个会话里 read_all_messages 要 2.5~7 秒，
而一轮回复里同一个会话往往要读两三次（点进去校验一次、决定回什么一次、发完再核一次）。
根因是"向上滚动加载历史"那段写成了两截：
  1) 同步 JS 里 while 循环滚到顶——同一次 JS 执行里等不到异步加载，等于只滚了一次；
  2) 回到 Python 后无条件 sleep(max_scroll_rounds × scroll_wait_ms) = 4 秒，
     而且这段时间里再也不滚，所以历史并没被多拽出来几条。
改成"滚一下、看条数长没长、不长就收"：没历史可读时约 0.6 秒返回，
真在加载时反而比原来滚得更勤（每轮都重新滚到顶）。
"""
import inspect

from boss_bot.page_handler import BossChatHandler as PageHandler


class FakePage:
    """只认脚本里的标记，按预设的条数序列回报。

    grows 里放每一步"滚动之后"的条数；序列走完就保持最后一个值。
    """

    def __init__(self, counts):
        self.counts = list(counts)
        self.scrolls = 0
        self.reads = 0

    def run_js(self, script, as_expr=False, timeout=None):
        if "/* SCROLL_TOP */" in script:
            self.scrolls += 1
            return "ok"
        if "/* MSG_COUNT */" in script:
            self.reads += 1
            idx = min(self.reads - 1, len(self.counts) - 1)
            return str(self.counts[idx])
        raise AssertionError("不该走到这里的 JS：" + script[:80])


def _handler(counts):
    ph = PageHandler.__new__(PageHandler)
    page = FakePage(counts)
    ph.page = page
    return ph, page


def _no_sleep(monkeypatch):
    """把等待换成"记账"，测试不真睡觉，但要能看出睡了多少。"""
    slept = []

    def fake_sleep(sec):
        slept.append(sec)

    monkeypatch.setattr("boss_bot.page_handler.time.sleep", fake_sleep)
    return slept


def test_没有更多历史就立刻收(monkeypatch):
    slept = _no_sleep(monkeypatch)
    ph, page = _handler([8, 8, 8, 8, 8])       # 条数一直不变
    ph._load_chat_history()
    total = sum(slept)
    assert total <= 1.0, f"没得加载还等了 {total:.2f} 秒，原来写死 4 秒"
    assert page.scrolls == 1, "不长了就不该再滚第二次"


def test_每轮都在加载就继续滚(monkeypatch):
    _no_sleep(monkeypatch)
    # 第一次读 8 条；滚一下长到 14；再滚长到 21；第三次滚完不再长 → 收
    ph, page = _handler([8, 14, 21])
    ph._load_chat_history(max_rounds=5)
    assert page.scrolls == 3, f"该滚到不再增长为止，实际滚了 {page.scrolls} 次"


def test_滚到上限就停不会越等越久(monkeypatch):
    slept = _no_sleep(monkeypatch)
    ph, page = _handler([i * 5 for i in range(1, 40)])   # 永远在长
    ph._load_chat_history(max_rounds=3)
    assert page.scrolls == 3
    assert sum(slept) < 3.0


def test_两条读取路径都用这一套等待():
    """read_all_messages 和 read_messages_rich 是同一个坑的两份拷贝，
    留一份写死 sleep 就还有一半在白等。"""
    src = inspect.getsource(PageHandler)
    assert src.count("self._load_chat_history(") >= 2
    for fn in (PageHandler.read_all_messages, PageHandler.read_messages_rich):
        body = inspect.getsource(fn)
        assert "for _ in range(max_scroll_rounds):\n                time.sleep(" not in body, \
            "还留着无条件 sleep 整个预算"


def test_读不出容器时不炸(monkeypatch):
    class DeadPage:
        def run_js(self, script, as_expr=False, timeout=None):
            raise RuntimeError("页面没了")

    ph = PageHandler.__new__(PageHandler)
    ph.page = DeadPage()
    ph._load_chat_history()          # 不许抛
