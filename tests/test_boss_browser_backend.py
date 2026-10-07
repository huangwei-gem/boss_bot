# -*- coding: utf-8 -*-
"""浏览器真实运行形态：接口与判据的回归。

为什么要这一份：界面上原来只有"无头"那个开关，说的是**配置**；而浏览器是启动那一刻
定型的——改完没重启、或者那个端口上根本是别人起的浏览器（9223 被别的项目占过一次），
光看开关都看不出来。2026-10-07 为了搞清两个号是不是真在无头跑，只能去翻进程命令行。

踩过的坑锁在这里：**不能拿 UA 判无头**。实测 cloakbrowser 带 --headless=new 起来后，
进程命令行 headless=YES，而 /json/version 的 UA 里 HeadlessChrome 被抹平了——
信 UA 就会把无头说成有头。所以无头与否以本进程启动时记下的那份为准，UA 只用来
发现"这端口上不是我们起的那个浏览器"。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fake_version(monkeypatch, ua, browser="Chrome/146.0.7680.177"):
    from boss_bot import browser_launcher as BL

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json as _json
            return _json.dumps({"Browser": browser, "User-Agent": ua}).encode()

    monkeypatch.setattr(BL, "urlopen", lambda *a, **k: _Resp())


class 真实运行模式Test:
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")

    def test_我们起的无头浏览器不能被UA骗成有头(self, monkeypatch):
        from boss_bot import browser_launcher as BL
        BL.LAUNCHED_MODES[9222] = True
        _fake_version(monkeypatch, self.UA)          # UA 里没有 Headless 字样
        got = BL.browser_mode(9222)
        assert got["running"] and got["headless"] is True, got
        assert got["依据"] == "本进程启动记录", got

    def test_我们起的有头窗口不谎报无头(self, monkeypatch):
        from boss_bot import browser_launcher as BL
        BL.LAUNCHED_MODES[9223] = False
        _fake_version(monkeypatch, self.UA.replace("Chrome/", "HeadlessChrome/"))
        got = BL.browser_mode(9223)
        assert got["headless"] is False and got["与启动记录不符"] is True, got

    def test_不是我们起的浏览器要说明依据是UA(self, monkeypatch):
        """端口上跑着别人的浏览器时，只能报"按 UA 看到的样子"，不冒充知道更多。"""
        from boss_bot import browser_launcher as BL
        BL.LAUNCHED_MODES.pop(9333, None)
        _fake_version(monkeypatch, self.UA)
        got = BL.browser_mode(9333)
        assert got["running"] and got["headless"] is False, got
        assert "UA" in got["依据"], got

    def test_端口没人监听就说没在跑(self):
        from boss_bot import browser_launcher as BL
        got = BL.browser_mode(1)          # 不可能有服务
        assert got["running"] is False, got

    def test_启动时记下模式(self):
        """记录点必须写在启动入口，不然这份账永远是空的。"""
        import inspect
        from boss_bot import browser_launcher as BL
        assert "LAUNCHED_MODES[int(port)] = bool(headless)" in inspect.getsource(BL.launch_browser)


def test_状态接口给每个账号补真实浏览器形态(monkeypatch):
    """监控和界面都从 /api/status 读，不该各自去猜端口。"""
    sys.path.insert(0, str(ROOT / "flask-version"))
    import app as A
    monkeypatch.setattr(A, "_account_cookie_state", lambda i, account=None: {"valid": True})
    monkeypatch.setattr(A, "browser_mode",
                        lambda port: {"running": True, "headless": True, "port": port,
                                      "browser": "Chrome/146", "依据": "本进程启动记录",
                                      "与启动记录不符": False})
    got = A._enrich_status({"accounts": [{"index": 0}, {"index": 1}]})
    assert got["accounts"][0]["browser"]["headless"] is True
    assert got["accounts"][1]["browser"]["port"] == 9223, "每个号要报自己的端口"


def test_界面把真实模式贴在开关旁边():
    """配置有一个开关、实际运行态无处可看 = 出事只能翻进程命令行。"""
    html = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="advHeadlessReal"' in html
    assert "function renderHeadlessReality" in html
    assert "renderHeadlessReality(data);" in html, "状态轮询没喂给它就是死控件"
    assert "重启面板才生效" in html, "配置与实际不一致必须说人话，不许静默显示配置值"


def test_真实模式判定不许读window上的config():
    """config/dataScope 都是 let 声明的，不挂在 window 上——从 window 取属性永远 undefined，

    不一致判定会一边倒地说"要重启"，看着像功能坏了。这个坑项目里踩过，锁住。
    """
    html = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
    段 = html[html.index("function renderHeadlessReality"):html.index("function applyStatusUpdate")]
    assert "window.config" not in 段, 段[:200]
    assert "typeof config !== 'undefined'" in 段
