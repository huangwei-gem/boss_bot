# -*- coding: utf-8 -*-
"""点"沟通"顺带开出来、当前用不上的标签页要当场关掉（限定在这一批 id 里）。

2026-10-09 实测：主账号（端口 9222）挂着 5 个页面，其中 3 个是 job_detail——
引擎只登记聊天那一张（_greet_chat_tab），BOSS 偶尔把岗位详情也开成新标签页，
这几张没人管就一直攒着。隔离实例里量过一张的价钱：新开一张详情页进程树
2533 → 2932 MB，关掉又掉回 2533 MB，一张就是 400 MB，三张一点几个 G。

全量清扫（不传 only_ids、由 BrowserManager.sweep_idle_tabs 算保护集）那条路
在 tests/test_tab_sweep_memory.py 里盯；这里盯的是"只在这批 id 里挑"那一档：
调用方明确知道是哪几张时，差集之外的一张都不能碰。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import close_idle_job_tabs  # noqa: E402


class FakeTab:
    def __init__(self, url):
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, tabs):
        self.tabs = tabs
        self.asked = []

    def get_tab(self, tid):
        self.asked.append(tid)
        return self.tabs[tid]


def test_关掉点沟通甩出来的岗位详情页():
    t1 = FakeTab("https://www.zhipin.com/job_detail/aaa.html")
    b = FakeBrowser({"1": t1})
    assert close_idle_job_tabs(b, only_ids={"1"}) == 1
    assert t1.closed


def test_聊天页不动():
    """聊天页是这一轮要用的那张，关掉等于把招呼发不出去"""
    t = FakeTab("https://www.zhipin.com/web/geek/chat")
    b = FakeBrowser({"1": t})
    assert close_idle_job_tabs(b, only_ids={"1"}) == 0
    assert not t.closed


def test_留着这一轮登记下来的那张():
    keep = FakeTab("https://www.zhipin.com/job_detail/keep.html")
    stray = FakeTab("https://www.zhipin.com/job_detail/stray.html")
    b = FakeBrowser({"k": keep, "s": stray})
    assert close_idle_job_tabs(b, keep_ids={"k"}, only_ids={"k", "s"}) == 1
    assert not keep.closed
    assert stray.closed


def test_只清点名那几张不碰旧标签页():
    """搜索页/聊天页是长期存在的，名单之外的一张都不能碰"""
    b = FakeBrowser({})
    assert close_idle_job_tabs(b, only_ids=set()) == 0
    assert b.asked == []


def test_取不到url就跳过不许连带关掉():
    class Broken:
        closed = False

        def close(self):
            self.closed = True

        @property
        def url(self):
            raise RuntimeError("tab 已经断连")

    b = FakeBrowser({"1": Broken()})
    assert close_idle_job_tabs(b, only_ids={"1"}) == 0


def test_关不动也不许把这一轮炸掉():
    class CantClose(FakeTab):
        def close(self):
            raise RuntimeError("浏览器已经在关这个标签页了")

    b = FakeBrowser({"1": CantClose("https://www.zhipin.com/job_detail/x.html")})
    assert close_idle_job_tabs(b, only_ids={"1"}) == 0


def test_打招呼收尾走的是管理器那一份保护集():
    """调用方自己想起来要保护谁 = 迟早漏一张。

    10-16 那次自杀就是把搜索页登记成待关的临时页：句柄钉着它，关掉之后
    每一次 url/get() 都抛 PageDisconnectedError。所以这里只许调
    BrowserManager.sweep_idle_tabs()，不许再拿 browser 自己遍历。
    """
    src = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
    起 = src.index("new_tab_ids = post_tab_ids - pre_tab_ids")
    止 = src.index("tab_ids 差集未找到新聊天标签页")
    段 = src[起:止]
    assert "sweep_idle_tabs()" in 段, "收尾没走那道按 id 保护的门"
    assert "close_stray_tabs" not in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
