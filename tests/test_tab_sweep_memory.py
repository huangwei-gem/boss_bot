# -*- coding: utf-8 -*-
"""闲置的岗位详情页要能扫干净：一张约 400 MB，漏一张就是一个号的内存。

现场（2026-10-16 10:12 只读探 `http://127.0.0.1:9223/json/list`）：账号2 在跑，
除了 `geek/chat` 和搜索页，还挂着 **3 张 job_detail**（数据分析师/高级数据分析师/
泊车数据分析师）。现有的 `close_stray_tabs` 只关"这一次点击甩出来的那几张"
（按 pre/post 的 tab_ids 差集），新页开得比那次快照晚就永远不在差集里——这 3 张
就是漏下来的。隔离实例量过：开一张详情页进程树 2932 MB、关掉 2533 MB。

清扫的红线只有一条：**正在用的那张不能碰**。10-16 那次整条投递腿死了 16 分钟
就是因为把搜索页当成待关的临时页关掉了；而"点了立即沟通但抽屉没出来"这一种，
搜索页自己就停在 job_detail 上——所以保护集必须按标签页 id 来，不能按 URL 猜。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import (BrowserManager, BrowserInstance,  # noqa: E402
                                       close_idle_job_tabs)


class 假页:
    def __init__(self, tid, url, 读不到=False):
        self.tab_id = tid
        self._url = url
        self.读不到 = 读不到
        self.关过 = False

    @property
    def url(self):
        if self.读不到:
            raise RuntimeError("页面已经不在")
        return self._url

    def close(self):
        self.关过 = True


class 假浏览器:
    def __init__(self, 页列表):
        self._页 = {p.tab_id: p for p in 页列表}
        self.tab_ids = [p.tab_id for p in 页列表]

    def get_tab(self, tid):
        return self._页[tid]


def 造管理器(页列表, 搜索="T-JOBS", 回复=None, 临时=None, 没有浏览器=False):
    """只装 sweep 用到的那几样：三张在用页的归属 + 一个能遍历的浏览器。"""
    m = BrowserManager.__new__(BrowserManager)
    浏览器 = None if 没有浏览器 else 假浏览器(页列表)
    钉 = None
    if not 没有浏览器:
        钉 = next(p for p in 页列表 if p.tab_id == 搜索)
        钉.browser = 浏览器
    m._instance = None if 没有浏览器 else BrowserInstance(chrome_page=钉)
    m._search_tab = m._instance
    m._chat_tab = None if 回复 is None else BrowserInstance(
        chrome_page=_带浏览器(页列表, 回复, 浏览器))
    m._greet_chat_tab = None if 临时 is None else BrowserInstance(
        chrome_page=_带浏览器(页列表, 临时, 浏览器))
    return m, 浏览器


def _带浏览器(页列表, tid, 浏览器):
    页 = next(p for p in 页列表 if p.tab_id == tid)
    页.browser = 浏览器
    return 页


def _岗位页(tid, n=1):
    return 假页(tid, "https://www.zhipin.com/job_detail/%d.html" % n)


class 按id保护的那一张绝不关Test:

    def test_搜索页停在岗位详情页也绝不关(self):
        """"抽屉没出来"那一种：搜索页自己就是 job_detail，按 URL 猜就会自杀"""
        搜索 = 假页("T-JOBS", "https://www.zhipin.com/job_detail/keep.html")
        管理, _ = 造管理器([搜索], 搜索="T-JOBS")

        assert 管理.sweep_idle_tabs() == 0
        assert 搜索.关过 is False, "关掉钉住的那张 = 整条投递腿死掉"


class 清扫范围Test:

    def test_挂着的岗位详情页全关掉(self):
        搜索 = 假页("T-JOBS", "https://www.zhipin.com/web/geek/jobs")
        聊天 = 假页("T-CHAT", "https://www.zhipin.com/web/geek/chat")
        闲1, 闲2, 闲3 = _岗位页("T-D1"), _岗位页("T-D2", 2), _岗位页("T-D3", 3)
        管理, _ = 造管理器([搜索, 聊天, 闲1, 闲2, 闲3], 搜索="T-JOBS", 回复="T-CHAT")

        assert 管理.sweep_idle_tabs() == 3
        assert (闲1.关过, 闲2.关过, 闲3.关过) == (True, True, True)
        assert 搜索.关过 is False and 聊天.关过 is False

    def test_登记过的临时会话页不关(self):
        """正在发消息那张是打招呼自己登记的临时页，收尾由 close_greet_chat_tab 办"""
        搜索 = 假页("T-JOBS", "https://www.zhipin.com/web/geek/jobs")
        临时 = 假页("T-CHAT2", "https://www.zhipin.com/web/geek/chat")
        管理, _ = 造管理器([搜索, 临时], 搜索="T-JOBS", 临时="T-CHAT2")

        assert 管理.sweep_idle_tabs() == 0
        assert 临时.关过 is False

    def test_非岗位详情页一律不动(self):
        搜索 = 假页("T-JOBS", "https://www.zhipin.com/web/geek/jobs")
        登录 = 假页("T-LOGIN", "https://www.zhipin.com/web/user/?ka=header-login")
        管理, _ = 造管理器([搜索, 登录], 搜索="T-JOBS")

        assert 管理.sweep_idle_tabs() == 0
        assert 登录.关过 is False, "等人扫码那张关了就把登录弄没了"

    def test_读不到url的那张宁可留着(self):
        搜索 = 假页("T-JOBS", "https://www.zhipin.com/web/geek/jobs")
        可疑 = 假页("T-??", "", 读不到=True)
        管理, _ = 造管理器([搜索, 可疑], 搜索="T-JOBS")

        assert 管理.sweep_idle_tabs() == 0
        assert 可疑.关过 is False

    def test_浏览器没起来时如实回零(self):
        管理, _ = 造管理器([], 没有浏览器=True)
        assert 管理.sweep_idle_tabs() == 0


class 判据只留一份Test:
    """哪些页算"没人认领"只写一处：补一类要收的页，不至于只补一半。"""

    def test_纯函数只认岗位详情页(self):
        浏览器 = 假浏览器([_岗位页("T-D1"), 假页("T-B", "https://www.zhipin.com/web/geek/chat")])
        assert close_idle_job_tabs(浏览器, ("T-B",)) == 1
        assert close_idle_job_tabs(浏览器, ("T-D1", "T-B")) == 0

    def test_关页判据在浏览器那一层只有一份(self):
        src = (ROOT / "boss_bot" / "browser_launcher.py").read_text(encoding="utf-8")
        assert src.count('"job_detail" in') == 1, "判据抄了两份，将来补一类只补一半"
        起 = src.index("def 闲置岗位页")
        止 = src.index("def 挑闲置岗位页")
        assert '"job_detail" in' in src[起:止], "判据不住在 `闲置岗位页` 里，两条清扫路就找不到它"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
