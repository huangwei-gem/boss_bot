# -*- coding: utf-8 -*-
"""会话页是"空壳"时，回复腿不许把它读成「0 个未读」，也不许拿同址重载去治。

2026-10-31 现场（两个号一模一样）：每一轮都是 `获取到未读会话数: 0`、跟进每条
`侧栏读不到滚动位置（no-box）`+`联系人搜索框没找到`，而职位页那个角标上挂着
39 条没看的消息。只读探针读回来的是：

    <div id="wrap"><!----><!----><!----></div>   侧栏 0 · 会话行 0 · 搜索框 0
    URL = https://www.zhipin.com/web/geek/chat   readyState = complete
    发过的请求只有 APM 埋点 + 一条 POST /wapi/zppassport/set/zpToken（没回来）

也就是说 Vue 的壳子加载完了、聊天应用没起来，而 `go_to_chat` 只看地址——地址对，
就永远不再加载这一页。判据要打在内容上（侧栏/会话行/搜索框），空壳要照实说
"没读到"，不许印成"0 个未读"。

真因当天下午查到并用 A/B 钉住（`.scratch/只读_ab_加载模式eager会不会生壳.py`，
同一浏览器、同一地址、两轮一致）：**eager 加载模式掐断了聊天应用首屏之后的分片**——
eager 17→19 个资源、34 个节点，再等 20 秒也不活；normal 138 个资源、1063 个节点、
侧栏 1 / 会话行 40 / 搜索框 1。所以"新开一张能不能活"取决于那张页用什么模式加载，
不取决于新旧；治法是会话页这一张单独走 normal（`_会话页导航到会话页`）。
另一条同日的实测：一张已经挂载的会话页被同址 `get()` 打回 34 个节点的壳，20 秒不回来
（`.scratch/只读探针_同址导航会不会把页打回空壳.py`）——所以也不许在这一页上原地重载。
"""
import time

import pytest

from boss_bot.page_handler import BossChatHandler


class 假页:
    """按脚本回答 run_js；`get` 记下来加载过哪些地址。`page` 指向自己，
    因为 BrowserInstance 的 `.page` 就是这层，测试里不必再造一个。"""

    def __init__(self, 答案=(), url="https://www.zhipin.com/web/geek/chat"):
        self.答案 = list(答案)
        self.url = url
        self.加载过 = []
        self.page = self

    def run_js(self, js, *a, **k):
        return self.答案.pop(0) if self.答案 else "shell"

    def get(self, url):
        self.加载过.append(url)


def 装配(答案):
    手 = BossChatHandler.__new__(BossChatHandler)
    手.page = 假页(答案)
    手.导航次数 = 0

    def _假导航(url):
        手.导航次数 += 1
    手._导航 = _假导航
    return 手


@pytest.fixture(autouse=True)
def 不让等待真的睡(_monkeypatch_sleep):
    yield


@pytest.fixture
def _monkeypatch_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_空壳页判成没挂载():
    手 = 装配(["shell"])
    assert 手.会话页挂载了吗() is False


def test_侧栏有任一样就算挂载():
    """判据必须打在侧栏/会话行/搜索框这三样上，不许拿别的字符串凑数。"""
    手 = 装配(["ok", "ok"])          # 一次判据 + 一次挂载检查
    assert 手.会话页挂载了吗() is True
    assert 手._确保会话页挂载() is True

    数过 = []

    class 只记脚本:
        url = "https://www.zhipin.com/web/geek/chat"

        def run_js(self, js, *a, **k):
            数过.append(js)
            return "ok"

        def get(self, url):
            raise AssertionError("挂载正常不许导航")
    手 = BossChatHandler.__new__(BossChatHandler)
    手.page = 只记脚本()
    assert 手._确保会话页挂载() is True
    assert ".user-list-content" in 数过[0] and "input.boss-search-input" in 数过[0], \
        "判据读的不是侧栏/会话行/搜索框这三样"


def test_空壳不许在这一页上重载():
    """判据留着，原地刷新删掉——10-31 实测 44 分钟里判据响 135 次、跟着发 121 次
    "重新加载"、地板记账的整页加载 32 发，0 次刷活。

    `go_to_chat` 一分钟被叫三四次（回复轮、跟进轮、面板体检走同一张页），
    在这里刷一次等于每次被叫都烧一发整页加载，而打开整页正是风控最集中那一步。
    """
    手 = 装配(["shell"] * 30)
    assert 手._确保会话页挂载() is False
    assert 手.导航次数 == 0, "又在这张页上重载了——那是拿整页加载去赌风控"
    assert 手.page.加载过 == [], "绕过 _导航 直接 page.get 也算动手"
    assert 手.会话页空壳 is True


def test_空壳连着一片判据只说一次():
    """这条一分钟被叫好几次：每次都 warn 就是 116 条重复日志把有用的淹没。"""
    手 = 装配(["shell"] * 30)
    记 = []
    from boss_bot import page_handler
    原logger = page_handler.logger
    page_handler.logger = type("记", (), {
        "warning": lambda self, m: 记.append(str(m)),
        "info": lambda self, m: 记.append(str(m)),
        "debug": lambda self, m: 记.append(str(m)),
        "error": lambda self, m: 记.append(str(m)),
    })()
    try:
        for _ in range(6):
            assert 手._确保会话页挂载() is False
    finally:
        page_handler.logger = 原logger
    提过 = [话 for 话 in 记 if "空壳" in 话]
    assert len(提过) == 1, "六次判据说了 %d 次空壳——复述窗口没生效" % len(提过)
    assert any("0 个未读" in 话 for 话 in 提过), "要说清「没读到」不等于「没有新消息」"


def test_判据读不出就不动手():
    """探不准就当页面还在：把一条读不出的探针当成"页面坏了"会天天白重载会话页。

    "读不出"有两种形状，都得单独成一档：run_js 抛异常，和 DrissionPage 把返回值
    丢成 None / 原样交回一个 Mock（页面正在挂载、句柄刚换的时候就是这副样子）。
    把这两种算成"空壳"，每一轮都会多发一发整页加载——而整页加载正是风控的来源。
    """
    手 = 装配([])

    class 报错页:
        url = "https://www.zhipin.com/web/geek/chat"

        def run_js(self, *a, **k):
            raise RuntimeError("页面连接已断开")
    手.page = 报错页()

    def _不许导航(url):
        raise AssertionError("读不出判据也不许重载这一页")
    手._导航 = _不许导航
    assert 手.会话页挂载了吗() is None
    assert 手._确保会话页挂载() is True

    class 没返回值的页:
        url = "https://www.zhipin.com/web/geek/chat"

        def run_js(self, *a, **k):
            return None
    手.page = 没返回值的页()
    assert 手.会话页挂载了吗() is None, "run_js 丢回 None 被判成空壳——重载会白烧整页加载"
    assert 手._确保会话页挂载() is True

    class 读回别的东西的页:
        url = "https://www.zhipin.com/web/geek/chat"

        def run_js(self, *a, **k):
            return {"ok": True}          # 形状不对：等于这条判据此刻什么都没说
    手.page = 读回别的东西的页()
    assert 手.会话页挂载了吗() is None


def test_go_to_chat末尾接的是挂载判据():
    import inspect
    源 = inspect.getsource(BossChatHandler.go_to_chat)
    assert "self._确保会话页挂载()" in 源, \
        "go_to_chat 又只看地址了——空壳页会被读成「0 个未读」"


def test_回复轮不许在页面不可用时读未读和补跟进():
    """判据打在动作上：读未读、落置顶、从存档补跟进三件事都必须在「可用」那一支里。"""
    import inspect

    from boss_bot.main_loop import UnifiedBotLoop
    源 = inspect.getsource(UnifiedBotLoop._run_reply_round)
    分岔 = 源.index("会话页可用 = self._chat_handler.go_to_chat()")
    for 动作 in ("get_unread_chats", "_登记置顶(", "_archive_owed_chats"):
        assert 源.index(动作) > 分岔, f"「{动作}」排在判据之前，页面不可用时照样会读"
    assert "unread_chats, candidates, owed_n = [], [], 0" in 源, \
        "不可用那一支没把候选清干净，会拿上一轮的旧候选继续发"
    assert "「没读到」不许印成「0 个未读」" in 源, "这句说明被删了——日志又会只剩一句 0"


class 换一张页Test:
    """空壳页刷不回来，只能换新的一张——这条是 10-31 现测出来的分岔。

    旧页上 `location.reload()` / `_导航(同一地址)` 之后仍是 34 个 DOM 节点的空壳，
    而同一个浏览器 `new_tab(会话页)` 4 秒就读满侧栏 1 / 会话行 40 / 搜索框 1。
    """

    def _环(self, 挂载=True, 上次=0.0):
        from boss_bot.main_loop import UnifiedBotLoop

        扔过 = []
        记 = []

        class 假手:
            def 会话页挂载了吗(self):
                return 挂载

        class 假管理:
            def 重开会话标签页(self, 等到挂载秒=12):
                扔过.append(等到挂载秒)
                return 假页(["ok"])

        环 = object.__new__(UnifiedBotLoop)
        环._chat_handler = 假手()
        环.browser_manager = 假管理()
        环._会话页重开于 = 上次
        环._log = lambda 级, 话: 记.append((级, 话))
        环.会话页重开冷却秒 = 300
        return 环, 扔过, 记

    def test_空壳就扔页并且把句柄换到新页上(self):
        环, 扔过, 记 = self._环(挂载=False)
        环._会话页空壳就换一张()
        assert len(扔过) == 1, "空壳却没扔页"
        assert isinstance(环._chat_handler.page, 假页), "扔完页没把 handler 绑到新页上"
        assert any("空壳" in 话 for 级, 话 in 记 if 级 == "WARN")

    def test_挂载正常一页都不许扔(self):
        环, 扔过, _ = self._环(挂载=True)
        环._会话页空壳就换一张()
        assert 扔过 == [], "页面好好的就重开，等于白加一发整页加载"

    def test_判据读不出也不许扔(self):
        """None = 「这一眼什么都没读到」，不是"页面是壳"。拿它当定罪会每轮扔一张页。"""
        环, 扔过, _ = self._环(挂载=None)
        环._会话页空壳就换一张()
        assert 扔过 == [], "读不出就被判成空壳，扔页等于拿整页加载赌风控"

    def test_冷却期内连着扔页要挡(self):
        """一直起不来时，每轮都扔一张页 = 拿整页加载去赌风控。"""
        import time
        环, 扔过, 记 = self._环(挂载=False, 上次=time.time() - 10)
        环._会话页空壳就换一张()
        assert 扔过 == [], "距上次只隔 10 秒又扔一张页"
        assert any("冷却" in 话 or "不再扔页" in 话 for _, 话 in 记), "挡下了得说一句"

    def test_重开时旧页是搜索页就不许关(self):
        """10-16 那次就是把钉住的搜索页当临时页关了，整条投递腿从此「页面断开」。"""
        from boss_bot.browser_launcher import BrowserManager

        管 = BrowserManager.__new__(BrowserManager)
        关过 = []

        class 假旧页:
            def close_current_tab(self):
                关过.append("关了")

        管._chat_tab = 假旧页()
        管._包的id = lambda 包装: "T1"
        管.search_tab_id = lambda: "T1"

        def 假取页(导航=True):
            管._chat_tab = 假页(["ok"])
            return 管._chat_tab
        管.get_chat_page = 假取页
        新 = 管.重开会话标签页(等到挂载秒=0)
        assert 关过 == [], "旧会话页就是搜索页，这一刀会把投递腿关掉"
        assert 管._chat_tab is 新, "没换新页就没重开成"

    def test_旧页不是搜索页时正常关掉(self):
        from boss_bot.browser_launcher import BrowserManager

        关过 = []

        class 假旧页:
            def close_current_tab(self):
                关过.append("关了")

        管 = BrowserManager.__new__(BrowserManager)
        管._chat_tab = 假旧页()
        管._包的id = lambda 包装: "T9"
        管.search_tab_id = lambda: "T1"
        管.get_chat_page = lambda 导航=True: 假页(["ok"])
        管.重开会话标签页(等到挂载秒=0)
        assert 关过 == ["关了"], "空壳页不关掉就是每轮漏一张废页"


def test_挂载探针三档_ok算挂载_shell算壳_别的都没读到():
    """这一句是「新页活过来了没有」的唯一证据，读不出不许算成"在"。

    browser_launcher 里原来读不出返回 True，日志会写「会话页已重开并挂载」
    而侧栏还是 0 行——把没证实的事记成成功，正是用户最烦的那一类。
    """
    from boss_bot.browser_launcher import BrowserManager

    for 答案, 期望 in (("ok", True), ("shell", False), (None, None), ({"ok": 1}, None)):
        class 页:
            def run_js(self, *a, **k):
                return 答案
        assert BrowserManager._会话应用挂载了(页()) is 期望, 答案

    class 会抛的页:
        def run_js(self, *a, **k):
            raise RuntimeError("断了")
    assert BrowserManager._会话应用挂载了(会抛的页()) is None


def test_重开的新页没确认挂载时日志不许记成成功():
    from boss_bot import browser_launcher
    from boss_bot.browser_launcher import BrowserManager

    记 = []
    原logger = browser_launcher.logger
    browser_launcher.logger = type("记日志", (), {
        "info": lambda self, m: 记.append(("INFO", m)),
        "warning": lambda self, m: 记.append(("WARN", m)),
    })()
    try:
        管 = BrowserManager.__new__(BrowserManager)
        管._chat_tab = None
        管.get_chat_page = lambda 导航=True: 假页(["shell"] * 50)
        管.重开会话标签页(等到挂载秒=0)
    finally:
        browser_launcher.logger = 原logger

    assert not any("已重开并挂载" in 话 for 级, 话 in 记), \
        "读回来是空壳却记了挂载成功"
    assert any("没确认挂载" in 话 for 级, 话 in 记), "没挂载成要照实说一句"
    assert "空壳" in "".join(话 for 级, 话 in 记), "要说清读到的是壳还是没读到"


# ── 会话页这张必须按 normal 加载：eager 会把它掐成 34 个节点的壳 ──────────────

def 装配会话页浏览器(能设=True, 回读="normal"):
    """造一台假浏览器：`new_tab()` 回一张假页，假页把动作按顺序记下来。

    顺序是关键判据——加载模式必须在这张页自己导航到会话页**之前**设，
    导航之后再设就只影响到下一次（实测 eager 那一张等 20 秒也不会活过来）。
    """
    class 假页:
        def __init__(self):
            self.动作 = []
            self.加载过 = []
            self.load_mode = "eager"
            self.page = self

        def normal(self):
            if not 能设:
                raise AttributeError("这一版 DrissionPage 没有 load_mode")
            self.动作.append("设 normal")
            self.load_mode = 回读

        def get(self, url):
            self.动作.append("导航")
            self.加载过.append(url)

    假页.set = property(lambda 自: type("假set", (), {"load_mode": 自})())

    class 假实例:
        def __init__(self):
            self.开过带地址的 = []
            self.新页 = 假页()

        def new_tab(self, url=""):
            self.开过带地址的.append(url)
            return self.新页

        def _get_browser(self):
            return self

    return 假实例()


def test_会话页这张要先设normal再导航():
    from boss_bot import browser_launcher
    from boss_bot.browser_launcher import BrowserManager

    实例 = 装配会话页浏览器()
    管 = BrowserManager.__new__(BrowserManager)
    管._instance = 实例
    管._chat_tab = None
    页 = 管.get_chat_page()

    assert 实例.开过带地址的 == [""], \
        "会话页要先开一张空白页（不带地址），否则加载模式来不及设在这一张上"
    动作 = 页._get_active().动作
    assert 动作[:2] == ["设 normal", "导航"], "顺序反了就是白设：%s" % 动作
    assert 页._get_active().加载过 == [BrowserManager.BOSS_CHAT_URL]


def test_设不上normal要照实说并且不许静默():
    """DrissionPage 换版本、或这一张根本没设上：日志要有这句话，
    不然面板记的是"已经按 normal 重开了"，而侧栏还是 0 行。"""
    from boss_bot import browser_launcher
    from boss_bot.browser_launcher import BrowserManager

    记 = []
    原 = browser_launcher.logger
    browser_launcher.logger = type("记日志", (), {
        "debug": lambda self, m, *a, **k: 记.append(("DEBUG", m)),
        "info": lambda self, m, *a, **k: 记.append(("INFO", m)),
        "warning": lambda self, m, *a, **k: 记.append(("WARN", m)),
    })()
    try:
        for 场景, 能设, 回读 in (("抛异常", False, "eager"), ("回读不认账", True, "eager")):
            记.clear()
            实例 = 装配会话页浏览器(能设=能设, 回读=回读)
            管 = BrowserManager.__new__(BrowserManager)
            管._instance = 实例
            管._chat_tab = None
            管.get_chat_page()
            话 = "".join(m for 级, m in 记 if 级 == "WARN")
            assert "normal" in 话, "%s 这一档没照实说： %s" % (场景, 话 or "（一句 warn 都没有）")
            assert 实例.新页.加载过, "%s 设没设成都得把这一页导航到会话页" % 场景
    finally:
        browser_launcher.logger = 原


def test_重开等到挂载的秒数盖得住实测挂载耗时():
    """normal 加载实测 6~9 秒出侧栏；上限低于这个数就是"页活着却判它没挂载"。"""
    from boss_bot.browser_launcher import BrowserManager
    import inspect

    源 = inspect.getsource(BrowserManager.重开会话标签页)
    assert "等到挂载秒: float = " in 源
    秒 = float(源.split("等到挂载秒: float = ")[1].split(")")[0])
    assert 秒 >= 15, "重开后只等 %.0f 秒，实测 normal 要 6~9 秒才出侧栏，风控抖动就误判" % 秒

