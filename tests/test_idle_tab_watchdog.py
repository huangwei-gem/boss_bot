# -*- coding: utf-8 -*-
"""闲置标签页的清扫要有一条不依赖投递腿的出口。

现场（2026-10-16 13:47 只读探两个号）：
- 机器 15.3 GB 物理内存只剩 0.52 GB 可用，交换区已经吃了 16.5 GB；
- 9223 上 9 个 page 目标里 **6 个完全不应答 CDP**（`Runtime.evaluate` 6 秒超时），
  其中包括投递腿自己钉住的那张搜索页和 3 张闲置的 job_detail；
- 从 12:49 起，账号2 两条腿（greet_loop / reply_loop）一条日志都没有，主账号
  的 greet 腿同样停在 12:49:16「BOSS 自动发出招呼语（第二种机制）」之后——
  没有轮次结束，也就没有轮次收尾的那次清扫。

这就是死循环：清扫挂在轮次收尾里，而轮次收尾要等这一单点完「沟通」之后走完；
页面冻住 → 这一单永远走不完 → 页不收 → 内存更紧 → 更多页冻住。
9223 挂着 4 张 job_detail（一张实测约 400 MB）一个钟头没人能关，就是这么来的。

修法不是给那一单加超时：线程是卡在 websocket 的收发里，Python 层的计时器抢不
回来。要加一条**只用 CDP 的 HTTP 端点**（`/json` 与 `/json/close/{id}`）的看守
线程——实测在渲染进程冻住的时候，这两个 HTTP 接口照样秒回，而它不碰任何一条
腿共用的 websocket。判据（哪张算没人认领）与保护集（哪张在册）仍旧只留一份。
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import (BrowserManager, BrowserInstance,  # noqa: E402
                                       close_idle_job_tabs,
                                       close_idle_job_tabs_http,
                                       闲置岗位页, 挑闲置岗位页)


def 目标(tid, url, 类型="page"):
    return {"id": tid, "url": url, "type": 类型,
            "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/%s" % tid}


class 假开关:
    """顶上 urlopen：记下发过哪些 URL，谁坏了就抛。"""

    def __init__(self, 清单, 坏的一批=(), 关不掉的=()):
        self.清单 = 清单
        self.坏的一批 = tuple(坏的一批)
        self.关不掉的 = set(关不掉的)
        self.发过 = []

    def __call__(self, url, timeout):
        self.发过.append(url)
        if "/json/close/" in url:
            if url.rsplit("/", 1)[-1] in self.关不掉的:
                raise OSError("404: not found")
            return 假响应("{}")
        if any(坏 in url for 坏 in self.坏的一批):
            raise OSError("拿不到")
        return 假响应(json.dumps(self.清单, ensure_ascii=False))


class 假响应:
    def __init__(self, 正文):
        self._正文 = 正文.encode("utf-8")

    def read(self):
        return self._正文

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


JOBS = "https://www.zhipin.com/web/geek/jobs?query=%E6%95%B0"
CHAT = "https://www.zhipin.com/web/geek/chat"
LOGIN = "https://www.zhipin.com/web/user/?ka=header-login"


def 岗位页(n):
    return "https://www.zhipin.com/job_detail/%d.html" % n


class 判据只留一份Test:
    """哪些页算"没人认领的岗位详情页"，WS 那条路和 HTTP 那条路必须共用一个判断。"""

    def test_判据只认岗位详情页(self):
        assert 闲置岗位页(岗位页(1)) is True
        for 不该关 in (JOBS, CHAT, LOGIN, 岗位页(2).replace("job_detail", "job-detail")):
            assert 闲置岗位页(不该关) is False, 不该关

    def test_读不到url不等于岗位详情页(self):
        assert 闲置岗位页("") is False
        assert 闲置岗位页(None) is False

    def test_挑出来的是id且在册的一张不留(self):
        清单 = [目标("T-JOBS", JOBS), 目标("T-CHAT", CHAT),
                目标("T-D1", 岗位页(1)), 目标("T-D2", 岗位页(2))]
        assert 挑闲置岗位页(清单, ("T-CHAT",)) == ["T-D1", "T-D2"]
        assert 挑闲置岗位页(清单, ("T-D1", "T-D2")) == []

    def test_搜索页停在岗位详情页时在册保护照样管用(self):
        """按 id 保护才成立：这一种 URL 就是 job_detail，按 URL 挑就会自杀。"""
        清单 = [目标("T-JOBS", 岗位页(0))]
        assert 挑闲置岗位页(清单, ("T-JOBS",)) == []

    def test_worker那类目标绝不关(self):
        """/json 里还混着 service_worker、shared_worker，URL 也可能带 job_detail。"""
        清单 = [目标("T-SW", 岗位页(9), 类型="service_worker"),
                目标("T-Other", "chrome-extension://abc/background.js", 类型="other")]
        assert 挑闲置岗位页(清单, ()) == []

    def test_ws那条路也用同一个判据(self):
        src = (ROOT / "boss_bot" / "browser_launcher.py").read_text(encoding="utf-8")
        assert src.count('job_detail" not in url') == 0, "判据抄了两份，将来补一类只补一半"
        起 = src.index("def close_idle_job_tabs(")
        止 = src.index("class BrowserManager")
        assert "闲置岗位页(url)" in src[起:止], "WS 那条路还在自己判 URL"
        assert "闲置岗位页(" in src[src.index("def close_idle_job_tabs_http"):止], \
            "HTTP 那条路没走同一份判据"


class HTTP清扫Test:

    def test_用http把闲置的岗位详情页关掉(self):
        清单 = [目标("T-JOBS", JOBS), 目标("T-CHAT", CHAT),
                目标("T-D1", 岗位页(1)), 目标("T-D2", 岗位页(2)), 目标("T-D3", 岗位页(3))]
        开 = 假开关(清单)
        assert close_idle_job_tabs_http("127.0.0.1:9223", ("T-JOBS", "T-CHAT"), 打开=开) == 3
        assert [u.rsplit("/", 1)[-1] for u in 开.发过 if "/json/close/" in u] == ["T-D1", "T-D2", "T-D3"]

    def test_没有要关的一张也不发close(self):
        清单 = [目标("T-JOBS", JOBS), 目标("T-CHAT", CHAT)]
        开 = 假开关(清单)
        assert close_idle_job_tabs_http("127.0.0.1:9223", ("T-JOBS",), 打开=开) == 0
        assert not [u for u in 开.发过 if "/json/close/" in u], "没有待关的还去发 close，会把在册的碰掉"

    def test_拿不到标签页列表回零不抛(self):
        """看守线程一死，两个号就再没人收页——这一圈失败必须咽下去。"""
        开 = 假开关([], 坏的一批=("/json",))
        assert close_idle_job_tabs_http("127.0.0.1:9399", (), 打开=开) == 0

    def test_单张关不掉继续关其余(self):
        清单 = [目标("T-D1", 岗位页(1)), 目标("T-D2", 岗位页(2)), 目标("T-D3", 岗位页(3))]
        开 = 假开关(清单, 关不掉的={"T-D2"})
        assert close_idle_job_tabs_http("127.0.0.1:9223", (), 打开=开) == 2

    def test_返回的不是json也不抛(self):
        class 坏响应(假响应):
            def read(self):
                return b"<html>503</html>"

        class 坏开:
            def __call__(self, url, timeout):
                return 坏响应("")

        assert close_idle_job_tabs_http("127.0.0.1:9223", (), 打开=坏开()) == 0


class 保护集只算一次Test:

    def test_两条清扫路用同一份在册id(self):
        m = BrowserManager.__new__(BrowserManager)
        钉 = type("页", (), {"tab_id": "T-JOBS", "browser": None})()
        m._instance = BrowserInstance(chrome_page=钉)
        m._search_tab = m._instance
        m._chat_tab = None
        m._greet_chat_tab = None
        assert m._在册标签页id() == {"T-JOBS"}

        源 = Path(ROOT / "boss_bot" / "browser_launcher.py").read_text(encoding="utf-8")
        for 名 in ("def sweep_idle_tabs(", "def sweep_idle_tabs_http("):
            起 = 源.index(名)
            段 = 源[起:起 + 1400]
            assert "self._在册标签页id()" in 段, "%s 自己拼保护集，两处会走偏" % 名


class 看守线程Test:

    def _管理器(self, 在册="T-JOBS"):
        m = BrowserManager.__new__(BrowserManager)
        m._debug_port = 9223
        钉 = type("页", (), {"tab_id": 在册, "browser": None})()
        m._instance = BrowserInstance(chrome_page=钉)
        m._search_tab = m._instance
        m._chat_tab = None
        m._greet_chat_tab = None
        m._看守 = None
        m._看守停 = None
        return m

    def _换掉http那一步(self, monkeypatch, 清单):
        """看守走的哪一步、带没带在册 id，用替身记下来（真端口在这台机器上是别人的浏览器）。"""
        记 = {"叫过": []}
        import boss_bot.browser_launcher as bl

        def 替身(address, keep_ids=(), timeout=5.0, 打开=None):
            记["叫过"].append((address, set(keep_ids)))
            return len(挑闲置岗位页(清单, keep_ids))

        monkeypatch.setattr(bl, "close_idle_job_tabs_http", 替身)
        return 记

    def test_看守一圈收掉冻住时也挂着的岗位页(self, monkeypatch):
        清单 = [目标("T-JOBS", JOBS)] + [目标("T-D%d" % i, 岗位页(i)) for i in (1, 2, 3, 4)]
        记 = self._换掉http那一步(monkeypatch, 清单)
        m = self._管理器()
        assert m.sweep_idle_tabs_http() == 4
        assert 记["叫过"] == [("127.0.0.1:9223", {"T-JOBS"})], "地址或保护集不对"

    def test_浏览器没起来看守不动手(self, monkeypatch):
        清单 = [目标("T-D1", 岗位页(1))]
        记 = self._换掉http那一步(monkeypatch, 清单)
        m = self._管理器()
        m._instance = None
        assert m.sweep_idle_tabs_http() == 0
        assert 记["叫过"] == [], "端口还没归属就该不动"

    def test_端口读不到自己那份也不动手(self, monkeypatch):
        """9223 被兄弟项目占着那种：不是我们的浏览器就不关别人的页。"""
        清单 = [目标("T-D1", 岗位页(1))]
        记 = self._换掉http那一步(monkeypatch, 清单)
        m = self._管理器()
        m._debug_port = None
        assert m.sweep_idle_tabs_http() == 0
        assert 记["叫过"] == []

    def test_认不出搜索页就一张都不关(self, monkeypatch):
        """保护集缺了钉住的那一张 = 把投递腿钉着的页当闲置关掉，那是自杀。

        macOS 那条路 `_get_active()` 可能给出一张没有 tab_id 的对象；判不了
        就不动手，和"读不到 URL 宁可留着"是同一条规矩。
        """
        清单 = [目标("T-D1", 岗位页(1))]
        记 = self._换掉http那一步(monkeypatch, 清单)
        m = self._管理器()
        m._instance._page.tab_id = None
        assert m.sweep_idle_tabs_http() == 0
        assert 记["叫过"] == [], "认不出在册的搜索页还敢扫，就是在册那张的死刑"

    def test_ws那条路认不出搜索页也不动手(self, monkeypatch):
        记 = []
        import boss_bot.browser_launcher as bl
        monkeypatch.setattr(bl, "close_idle_job_tabs",
                            lambda 浏览器, keep_ids=(), only_ids=None: 记.append(keep_ids) or 0)
        m = self._管理器()
        钉 = m._instance._page
        浏览器 = type("B", (), {"tab_ids": [], "get_tab": lambda s, t: None, "address": "x"})()
        钉.browser = 浏览器
        钉.tab_id = None
        assert m.sweep_idle_tabs() == 0
        assert 记 == [], "两条清扫路对「判不了就不关」必须同一口径"

    def test_启动把看守拉起来并且是守护线程(self):
        源 = Path(ROOT / "boss_bot" / "browser_launcher.py").read_text(encoding="utf-8")
        起 = 源.index("def launch(self)")
        段 = 源[起:起 + 1200]
        assert "start_idle_tab_watchdog()" in 段, "浏览器一起来就该带上这条不依赖腿的出口"
        起 = 源.index("def start_idle_tab_watchdog")
        段 = 源[起:起 + 1600]
        assert "daemon=True" in 段
        assert "Event()" in 段, "裸 while True + sleep 停不下来"

    def test_关闭浏览器把看守停掉(self):
        源 = Path(ROOT / "boss_bot" / "browser_launcher.py").read_text(encoding="utf-8")
        起 = 源.index("def close(self):")
        段 = 源[起:起 + 700]
        assert "stop_idle_tab_watchdog()" in 段

    def test_停信号一到这一圈就不等满(self):
        m = self._管理器()
        m.start_idle_tab_watchdog(interval=3600)
        起 = time.perf_counter()
        m.stop_idle_tab_watchdog()
        # 判据是"不等满那一圈"，不是"两秒内"：全量跑加上两个真浏览器在抢 CPU，
        # 线程被排到 2 秒外很正常（10-17 全量 2178 条里就红了这一条，单跑 2.2 秒过）。
        # 留 5 秒照样抓得住"压根没听信号、睡满 3600 秒才退"那种写法。
        assert time.perf_counter() - 起 < 5, "等满一小时才退，面板关浏览器就卡住"

    def test_一圈里抛异常线程还要活着(self, monkeypatch):
        def 炸(address, keep_ids=(), timeout=5.0, 打开=None):
            raise OSError("端口挂了")

        import boss_bot.browser_launcher as bl
        monkeypatch.setattr(bl, "close_idle_job_tabs_http", 炸)
        m = self._管理器()
        m.start_idle_tab_watchdog(interval=0.05)
        try:
            time.sleep(0.3)
            assert m._看守 is not None and m._看守.is_alive(), "一圈失败就把看守弄死了"
        finally:
            m.stop_idle_tab_watchdog()


class 旧的ws清扫不受影响Test:

    def test_ws那条路还是按id保护(self):
        class 页:
            def __init__(self, tid, url):
                self.tab_id, self._url = tid, url
                self.关过 = False

            @property
            def url(self):
                return self._url

            def close(self):
                self.关过 = True

        列表 = [页("T-D1", 岗位页(1)), 页("T-JOBS", 岗位页(0))]
        浏览器 = type("B", (), {"tab_ids": [p.tab_id for p in 列表],
                                "get_tab": lambda s, t: next(p for p in 列表 if p.tab_id == t)})()
        assert close_idle_job_tabs(浏览器, keep_ids=("T-JOBS",)) == 1
        assert (列表[0].关过, 列表[1].关过) == (True, False)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
