# -*- coding: utf-8 -*-
"""主日志里三分之一是没人要的话：第三方的连接流水 + 清扫每轮报平安。

现测 logs/boss_bot.log（10-25 那趟，40188 行，探针 .scratch/探针_按logger计数.py）：

    16906 行 42.5%  boss_bot.main_loop      ← 要看的是这个
     6236 行 15.7%  urllib3.util.retry      "Converted retries value: 0 -> Retry(total=0…"
     6236 行 15.7%  urllib3.connectionpool  "Starting new HTTP connection (1): 127.0.0.1…"
     4788 行 12.0%  browser_launcher        其中「标签页清扫(…)关掉 0 张」3105 行 7.7%
     3151 行  7.9%  boss_bot.page_handler

urllib3 那两族合起来 31%：DrissionPage 每轮都走 CDP 端口，它自己就是 HTTP 客户端，
于是"我又连了一次"被播报了一整天。根因是 app.py 把 root logger 设成 DEBUG 让文件
处理器收得到所有日志——这个决定本身没错（要的就是自家 DEBUG 进文件），错在没有把
第三方隔开。日志被淹不只是难看：10-23 那次 20MB 截断先吃掉的就是人要看的那些行，
而 grep 一个"谁把投递停了"要在大海里捞。

清扫那一条是另一回事：看守每 60 秒一圈、轮次收尾一趟，两个号一起算，「关掉 0 张
闲置的岗位详情页」这种纯报平安的话一句都不必说。真动手、读不到 URL、或者页面张数
跟上一圈不一样，才说明世界变了。
"""
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "flask-version"))

from boss_bot.browser_launcher import (  # noqa: E402
    _清扫要说, _清扫最近, close_idle_job_tabs, close_idle_job_tabs_http,
)


@pytest.fixture
def 清账():
    _清扫最近.clear()
    yield
    _清扫最近.clear()


@pytest.fixture(scope="module")
def 面板日志配置(tmp_path_factory):
    """导入 app 只为跑一遍它那段 logging 装配； UnifiedBotLoop 不打真浏览器。"""
    from unittest.mock import patch
    临时 = tmp_path_factory.mktemp("面板日志")
    import boss_bot.unified_config as UC
    原 = (UC.BOT_CONFIG_FILE, UC.OVERRIDES_FILE, UC.USER_PROFILE_FILE)
    UC.BOT_CONFIG_FILE = 临时 / "bot_config.json"
    UC.OVERRIDES_FILE = 临时 / "config_overrides.json"
    UC.USER_PROFILE_FILE = 临时 / "user_profile.json"
    if str(ROOT / "flask-version") not in sys.path:
        sys.path.insert(0, str(ROOT / "flask-version"))
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    UC.BOT_CONFIG_FILE, UC.OVERRIDES_FILE, UC.USER_PROFILE_FILE = 原
    return FLASK_APP


def test_第三方连接流水被钉在_警告(面板日志配置):
    for 名 in ("urllib3", "urllib3.util.retry", "urllib3.connectionpool",
                "requests", "httpcore.connection"):
        assert logging.getLogger(名).getEffectiveLevel() >= logging.WARNING, 名


def test_自家的_仍然进得来(面板日志配置):
    assert logging.getLogger("boss_bot.browser_launcher").getEffectiveLevel() <= logging.DEBUG
    assert logging.getLogger("boss-web").getEffectiveLevel() <= logging.INFO


def test_第三方流水真的进不了处理器(面板日志配置):
    """光看级别不算：挂一个收 DEBUG 的内存处理器，两边各打一句，看谁进得来。"""
    收 = []

    class 记(logging.Handler):
        def emit(self, record):
            收.append(record.name + "|" + record.getMessage())

    h = 记()
    h.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(h)
    try:
        logging.getLogger("urllib3.connectionpool").debug("Starting new HTTP connection (1): 127.0.0.1:9223")
        logging.getLogger("urllib3.util.retry").debug("Converted retries value: 0 -> Retry(total=0)")
        logging.getLogger("boss_bot.browser_launcher").debug("标签页看守：这一圈把两张岗位详情页收了")
    finally:
        root.removeHandler(h)
    assert not [x for x in 收 if x.startswith("urllib3")], 收
    assert any("标签页看守" in x for x in 收), "自家的 DEBUG 被一起钉掉了"


def test_清扫没得关且张数没变就不开口(清账):
    assert _清扫要说("127.0.0.1:9223", 2, 2, 0, 0) is True, "第一圈要说一句"
    assert _清扫要说("127.0.0.1:9223", 2, 2, 0, 0) is False, "一模一样还报＝每轮刷一句"
    assert _清扫要说("127.0.0.1:9223", 3, 2, 0, 0) is True, "页面多了一张必须说"
    # 读不到/关掉这两条要按"连着两圈都一样"来问：光问第一次，形状本身就在
    # 台账里（上一圈是 0 这一圈是 1），删掉这两个触发词也一样会红——那种门是摆设。
    assert _清扫要说("127.0.0.1:9223", 3, 2, 1, 0) is True, "有页读不到 URL 必须说"
    assert _清扫要说("127.0.0.1:9223", 3, 2, 1, 0) is True, "一直读不到不能第二圈就闭嘴"
    assert _清扫要说("127.0.0.1:9223", 5, 2, 0, 2) is True, "真关掉了两张必须说"
    assert _清扫要说("127.0.0.1:9223", 5, 2, 0, 2) is True, "下一圈又关掉两张还得说"


def test_两个号各记各的账(清账):
    """台账按地址分：号1 安静了不能带着号2 一起闭嘴。

    故意让最后一个形状跟号1 记下来的一模一样——不分号的那版判据会在这里
    把号2 的第一句也吞掉。
    """
    assert _清扫要说("127.0.0.1:9223", 4, 4, 0, 0) is True
    assert _清扫要说("127.0.0.1:9223", 4, 4, 0, 0) is False
    assert _清扫要说("127.0.0.1:9222", 4, 4, 0, 0) is True, "另一个号不能跟着这个号闭嘴"


def test_http_看守连跑两圈只说一句(清账, monkeypatch):
    页 = [{"type": "page", "id": "a", "url": "https://www.zhipin.com/web/geek/job?x=1"},
          {"type": "page", "id": "b", "url": "https://www.zhipin.com/web/geek/chat"}]

    class 响:
        def __init__(self, 文): self._文 = 文
        def read(self): return 文编(self._文)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    说 = []

    def 打开(url, t):
        return 响("[]" if "/json/close/" in url else __import__("json").dumps(页))

    import boss_bot.browser_launcher as BL

    class 记(logging.Handler):
        def emit(self, record):
            if "标签页看守" in record.getMessage():
                说.append(record.getMessage())

    h = 记(); h.setLevel(logging.DEBUG); BL.logger.addHandler(h)
    try:
        for _ in range(3):
            close_idle_job_tabs_http("127.0.0.1:9223", keep_ids=["b"], 打开=打开)
    finally:
        BL.logger.removeHandler(h)
    assert len(说) == 1, "三圈只该说一句，实际说了 %d 句：%s" % (len(说), 说)


def test_网页清扫连跑两圈也只说一句(清账, monkeypatch):
    说 = []
    import boss_bot.browser_launcher as BL

    class 假标签:
        def __init__(self, url): self.url = url
        def close(self): pass

    class 假浏览器:
        address = "127.0.0.1:9223"
        tab_ids = ("a", "b")
        def get_tab(self, tid): return 假标签("https://www.zhipin.com/web/geek/job?x=%s" % tid)

    class 记(logging.Handler):
        def emit(self, record):
            if "标签页清扫" in record.getMessage():
                说.append(record.getMessage())

    h = 记(); h.setLevel(logging.DEBUG); BL.logger.addHandler(h)
    try:
        for _ in range(3):
            close_idle_job_tabs(假浏览器(), keep_ids=("a", "b"))
    finally:
        BL.logger.removeHandler(h)
    assert len(说) == 1, "三圈只该说一句，实际说了 %d 句：%s" % (len(说), 说)


def test_看守读不到列表这一句不许省(清账):
    """"读不到"和"没有要收的"必须长得不一样——浏览器没了就从这句看出来
    （tests/test_browser_gone_relaunch.py 记的那次 10061 就是这么发现的）。"""
    说 = []
    import boss_bot.browser_launcher as BL

    def 打开(url, t): raise ConnectionRefusedError("10061")

    class 记(logging.Handler):
        def emit(self, record):
            说.append(record.getMessage())

    h = 记(); h.setLevel(logging.DEBUG); BL.logger.addHandler(h)
    try:
        close_idle_job_tabs_http("127.0.0.1:9224", 打开=打开)
        close_idle_job_tabs_http("127.0.0.1:9224", 打开=打开)
    finally:
        BL.logger.removeHandler(h)
    assert len([x for x in 说 if "读不到标签页列表" in x]) == 2, 说


def 文编(文):
    return 文.encode("utf-8") if isinstance(文, str) else 文
