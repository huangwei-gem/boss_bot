# -*- coding: utf-8 -*-
"""打招呼那条腿的"页面断开"要能自己好起来，而且不能是自己把搜索页关掉的。

盘上取证（data/greet_records.json + logs/boss_bot.log，2026-10-16 真实运行）：
  03:35:21 [账号2] 当前页面未找到输入框，遍历 3 个标签页（排除回复引擎标签页）
  03:35:21 [账号2] 在标签页 https://www.zhipin.com/web/geek/chat 中找到输入框: #chat-input
  03:35:52 已关闭打招呼引擎临时聊天标签页
  03:36:02 页面已断开 → 两个落点都没起来 → 跳过: 高级数据分析师
  ……同样的话每 20 秒一条，到 03:51:21 一共 39 条，全是账号2，一条没投进去。
同一时刻用 CDP 数 9223：chat 页 + job_detail 页两张都活着。浏览器没死，
死的只是打招呼那个句柄钉住的那张标签页。

真机量出来的语义（tools/probe_tab_rebind.py，隔离实例 + 本地 data: 页，不碰 BOSS）：
  A `ChromiumPage.new_tab()` 之后 `page.tab_id` 纹丝不动——浏览器级句柄一直钉在
    它自己那张页上；关掉**别的**标签页，`page.url` 照样读得到。
  B 关掉**句柄自己那张** → 之后每一次 `page.url`/`page.get()` 都抛
    `PageDisconnectedError: 与页面的连接已断开`（str() 以换行开头，
    所以线上日志里冒号后面看着是空的）。
  C 这台 DrissionPage 4.1.1.4 的 ChromiumPage **没有 set_tab**，也没有 tab_recover：
    指针挪不回去。能用的救法是拿一张活 Tab（new_tab/get_tab）换掉包装里的页对象；
    Tab 撑得起 url/get/ele/eles/run_js/scroll/set/listen/cookies/browser/tab_id，
    但**没有 quit**——所以 `BrowserInstance.quit()` 必须走浏览器对象，不然一重绑
    就没法关浏览器了。

所以这一族有两件事要做：①别把搜索页当成临时会话页关掉（④遍历只排除了回复引擎
那张，自己那张也在候选里，命中输入框后注册成 `_greet_chat_tab` 就被收尾关掉）；
②真的断了要能换一张活页接着跑，而不是抱着死句柄一单烧一条记录。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import (BrowserInstance, BrowserManager,
                                       PageDisconnectedError)
from boss_bot.greet_engine import GreetEngine


class 假页:
    """一张标签页，照实测的 ChromiumTab 建模：钉住的那张被关 → 读什么都抛。"""

    def __init__(self, tid, url, 浏览器):
        self.tab_id = tid
        self._url = url
        self.browser = 浏览器
        self.已关 = False
        self.访问过 = []

    @property
    def url(self):
        if self.已关:
            raise PageDisconnectedError("\n与页面的连接已断开。\n版本: 4.1.1.4")
        return self._url

    def get(self, 目标):
        self.访问过.append(目标)
        if self.已关:
            raise PageDisconnectedError("\n与页面的连接已断开。")
        self._url = 目标

    def run_js(self, script, *a, **kw):
        if self.已关:
            raise PageDisconnectedError("\n与页面的连接已断开。")

    def ele(self, sel, timeout=None):
        if self.已关:
            raise PageDisconnectedError("\n与页面的连接已断开。")
        return object()

    def close(self):
        self.已关 = True


class 假浏览器:
    """Chromium：管标签页，被 Tab.browser 和包装共用。"""

    def __init__(self):
        self.页们 = []
        self.关过 = []
        self._序号 = 0

    def _新id(self):
        self._序号 += 1
        return "T%02d" % self._序号

    def 建(self, url):
        页 = 假页(self._新id(), url, self)
        self.页们.append(页)
        return 页

    @property
    def tab_ids(self):
        return [p.tab_id for p in self.页们 if not p.已关]

    def get_tab(self, tid):
        for p in self.页们:
            if p.tab_id == tid:
                return p
        return None

    def new_tab(self, url=""):
        return self.建(url or "about:blank")

    def quit(self):
        self.关过.append("quit")


class 假浏览器级页:
    """ChromiumPage：所有动作都落在**它自己钉住的那张**标签页上（实测 A/B）。"""

    def __init__(self, 浏览器):
        self.browser = 浏览器
        self.钉 = 浏览器.建("https://www.zhipin.com/web/geek/jobs?query=%E6%95%B0%E6%8D%AE")

    @property
    def tab_id(self):
        return self.钉.tab_id

    @property
    def url(self):
        return self.钉.url

    def get(self, 目标):
        return self.钉.get(目标)

    def run_js(self, script, *a, **kw):
        return self.钉.run_js(script)

    def ele(self, sel, timeout=None):
        return self.钉.ele(sel, timeout=timeout)

    def new_tab(self, url=""):
        return self.browser.new_tab(url)      # 实测：不会把 钉 挪走

    @property
    def tab_ids(self):
        return self.browser.tab_ids

    def get_tab(self, tid):
        return self.browser.get_tab(tid)


def 造管理器():
    浏览器 = 假浏览器()
    页 = 假浏览器级页(浏览器)
    实例 = BrowserInstance(chrome_page=页)
    管理 = BrowserManager.__new__(BrowserManager)
    管理._instance = 实例
    管理._search_tab = 实例
    管理._chat_tab = None
    管理._greet_chat_tab = None
    return 管理, 浏览器, 页


def 造发信引擎(成因):
    """只测 send_greeting 的失败分支：把 _apply_job 钉成"永远返回这个成因"。"""
    引擎 = GreetEngine.__new__(GreetEngine)
    引擎.running = True
    引擎._log = lambda *a, **k: None
    引擎._apply_job = lambda job: (False, 成因)
    引擎._record_greet = lambda *a, **k: None
    引擎._emit_greet_event = lambda *a, **k: None
    引擎._report_progress = lambda: None
    引擎.applied_count = 0
    引擎.skipped_count = 0
    引擎._nav_fail_recent = []
    引擎._applied_recent = []
    引擎._greet_cooldown_until = 0.0
    引擎._greet_cooldown_reason = ""
    return 引擎


def 造恢复引擎(管理, 日志=None):
    引擎 = GreetEngine.__new__(GreetEngine)
    引擎.browser_manager = 管理
    引擎._query, 引擎._city, 引擎._job_type = "数据分析", "长沙", ""
    引擎._build_search_url = lambda q, c, j: ("https://www.zhipin.com/web/geek/jobs"
                                             "?query=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90")
    引擎._log = lambda *a: (日志 or []).append(a)
    引擎._random_delay = lambda *a: None
    引擎._nav_fail_recent = []
    引擎._applied_recent = []
    引擎._greet_cooldown_until = 0.0
    引擎._greet_cooldown_reason = ""
    return 引擎


class 搜索页不许自己关掉Test:
    """根因那一半：④遍历把命中输入框的标签页注册成 _greet_chat_tab，收尾就把它关了；
    自己那张搜索页也在候选里（以前只排除回复引擎那张）。关掉自己的钉住页 =
    这个号剩下的每一单都必挂——实测 B。"""

    def test_登记临时会话页时拒绝搜索页自己(self):
        管理, 浏览器, 页 = 造管理器()
        搜索 = 页.钉
        assert 管理.claim_greet_chat_tab(BrowserInstance(chrome_page=搜索)) is False, \
            "搜索页被当成临时会话页登记了——收尾一关整条腿就死"
        assert 管理._greet_chat_tab is None

    def test_别的标签页可以登记(self):
        管理, 浏览器, 页 = 造管理器()
        另一张 = 浏览器.new_tab("https://www.zhipin.com/web/geek/chat")
        assert 管理.claim_greet_chat_tab(BrowserInstance(chrome_page=另一张)) is True
        assert 管理._greet_chat_tab is not None

    def test_收尾关页时再挡一次搜索页(self):
        """两道门：登记那一关可能被绕过（历史代码就是直接赋值 `_greet_chat_tab=`）。"""
        管理, 浏览器, 页 = 造管理器()
        管理._greet_chat_tab = BrowserInstance(chrome_page=页.钉)   # 模拟旧代码写坏
        管理.close_greet_chat_tab()
        assert 页.钉.已关 is False, "搜索页被 close_greet_chat_tab 关掉了"
        assert 管理._greet_chat_tab is None


class 断开恢复那一路Test:
    def test_自己那张被关后要换一张活页接着跑(self):
        管理, 浏览器, 页 = 造管理器()
        引擎 = 造恢复引擎(管理)
        页.钉.close()                        # 线上那一步：搜索页被当成临时页关了

        assert 引擎._handle_disconnect() is True, "浏览器活着，就该换张页继续"
        assert 管理._instance._page is not 页.钉, "还抱着那张死页"
        assert 管理._instance.url.startswith("https://www.zhipin.com"), 管理._instance.url

    def test_重绑之后关浏览器走的还是浏览器对象(self):
        """实测 C：Tab 没有 quit。`BrowserInstance.quit()` 若还打 `self._page.quit()`，
        一重绑就关不掉浏览器（下一次启动撞自己的空壳，两个号起不来那种）。"""
        管理, 浏览器, 页 = 造管理器()
        引擎 = 造恢复引擎(管理)
        页.钉.close()
        assert 引擎._handle_disconnect() is True
        assert not hasattr(管理._instance._page, "quit"), "前提：重绑后拿到的是 Tab"
        管理._instance.quit()
        assert 浏览器.关过 == ["quit"], "quit 必须打到 Chromium 上"

    def test_没坏的时候不许新开标签页(self):
        管理, 浏览器, 页 = 造管理器()
        引擎 = 造恢复引擎(管理)
        张数 = len(浏览器.tab_ids)
        assert 引擎._handle_disconnect() is True
        assert len(浏览器.tab_ids) == 张数, "原地导航就好了还新开一张，等于每轮漏一张页"

    def test_新开的那张也起不来时才说没起来(self):
        """只要还能开出一张读得到的页就不算死——真死是浏览器进程没了、new_tab 也抛。"""
        管理, 浏览器, 页 = 造管理器()
        引擎 = 造恢复引擎(管理)
        页.钉.close()
        原建 = 浏览器.建

        def 开出来就是死的(url=""):
            新 = 原建(url)
            新.close()
            return 新

        浏览器.new_tab = 开出来就是死的
        assert 引擎._handle_disconnect() is False


class 熔断那一路Test:
    """断开这一族以前不进熔断：03:36 起每 20 秒烧一条记录，烧了 39 条、16 分钟。

    10-16 04:15 又添一种：主账号 9222 的 CDP 已经不应答（浏览器没了），循环还在
    一个个岗位往下走，04:15:16→04:17:49 连着 7 条「浏览器未启动」。
    这一族的共同点是"坏在腿上，不是坏在这个岗位"——只数「页面加载失败」就漏。
    """

    整条腿坏了的成因 = ("页面加载失败", "页面断开且恢复失败", "导航前页面断开且恢复失败",
                        "浏览器未启动")

    def test_这四种坏法都要记进熔断窗口(self):
        """记账只放在 send_greeting 的失败分支这一处，不放在各个 return 现场。

        放在现场就有"第三条 return 忘了记"——那 39 条和 7 条正是各自 return、
        各自没人记造成的。
        """
        for 成因 in self.整条腿坏了的成因:
            引擎 = 造发信引擎(成因)
            for 第几次 in range(1, 6):
                assert 引擎.send_greeting({"job_name": "数据分析师", "url": "https://x"}) is False
                assert len(引擎._nav_fail_recent) == 第几次, "%s 每次恰好记一笔" % 成因
            assert 成因 in 引擎.greet_cooldown_reason(), "%s 五连败就该冷下来" % 成因

    def test_岗位自己的问题不记进腿的账(self):
        """抽屉没出来、没登录这些各有各的计数；把岗位级失败算进腿级会把好腿冻掉。"""
        for 成因 in ("点了「立即沟通」但聊天抽屉没在这个标签页里出现（URL 仍停在岗位详情页）",
                     "登录超时", "JD 命中否决词「驻场」｜数据库运维", ""):
            引擎 = 造发信引擎(成因)
            for _ in range(6):
                引擎.send_greeting({"job_name": "数据分析师", "url": "https://x"})
            assert 引擎._nav_fail_recent == [], 成因
            assert 引擎.greet_cooldown_reason() == "", 成因

    def test_两种坏法混在一格里一起算(self):
        引擎 = 造发信引擎("页面加载失败")
        now = 1_000_000.0
        引擎._note_nav_fail("页面加载失败", now=now)
        for i in range(4):
            引擎._note_nav_fail("页面断开且恢复失败", now=now + i)
        assert 引擎.greet_cooldown_left(now + 6) > 0
        话 = 引擎.greet_cooldown_reason(now + 6)
        assert "页面加载失败" in 话 and "页面断开" in 话, 话

    def test_只有一种坏时报的那句话还是原来的词(self):
        """10-09 那条「页面加载失败」的判据不能被改散——线上按这个词看过几百条。"""
        引擎 = 造发信引擎("页面加载失败")
        now = 1_000_000.0
        for i in range(5):
            引擎._note_nav_fail("页面加载失败", now=now + i)
        assert "页面加载失败" in 引擎.greet_cooldown_reason(now + 6)
