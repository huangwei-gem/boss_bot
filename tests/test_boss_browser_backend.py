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

from boss_bot import browser_launcher as BL


def _fake_version(monkeypatch, ua, browser="Chrome/146.0.7680.177"):
    from boss_bot import browser_launcher as BL

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json as _json
            return _json.dumps({"Browser": browser, "User-Agent": ua}).encode()

    monkeypatch.setattr(BL, "urlopen", lambda *a, **k: _Resp())
    # 这一组的题目是"无头能不能靠 UA 判"，端口归属另有一组专门测；
    # 真机上 9223 此刻坐着别的项目的浏览器，不钉住这一条整组会随环境红。
    monkeypatch.setattr(BL, "port_is_ours", lambda port: True)


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


class 端口归属Test:
    """9223 上现在坐着别的项目的 cloakbrowser（boss-auto-apply 的 Temp/DrissionPage profile）。

    面板按 /json/version 报"这个号在跑、无头"，可那根本不是我们的浏览器——
    我们那个号的循环是停的。运维上要看的不是"端口有没有人答"，是"答的是不是我们的"。
    """

    def _patch_owner(self, monkeypatch, cmdline):
        import psutil

        class _L:
            port = 9223

        class _C:
            laddr = _L()
            pid = 50096
            status = "LISTEN"

        class _P:
            def cmdline(self):
                return cmdline

        monkeypatch.setattr(psutil, "net_connections", lambda kind="tcp": [_C()])
        monkeypatch.setattr(psutil, "Process", lambda pid: _P())
        monkeypatch.setattr(BL, "_OWNER_CACHE", {})

    def _version_ok(self, monkeypatch):
        import json as _json

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return _json.dumps({"Browser": "Chrome/146.0.0.0",
                                    "User-Agent": "Mozilla/5.0 Chrome/146.0.0.0"}).encode()

        monkeypatch.setattr(BL, "urlopen", lambda *a, **k: _Resp())

    def test_别人项目的浏览器不算在跑(self, monkeypatch):
        self._version_ok(monkeypatch)
        self._patch_owner(monkeypatch, [
            r"C:\Users\x\Downloads\coding项目\boss-auto-apply\cloakbrowser\chrome.exe",
            "--remote-debugging-port=9223",
            r"--user-data-dir=C:\Users\x\AppData\Local\Temp\DrissionPage\userData\9223"])
        got = BL.browser_mode(9223)
        assert got["本项目"] is False, got
        assert got["running"] is False, got
        assert "不是本项目" in got["依据"], got

    def test_我们自己profile算在跑(self, monkeypatch):
        from pathlib import Path
        self._version_ok(monkeypatch)
        根 = str(Path(BL.__file__).resolve().parent.parent)
        self._patch_owner(monkeypatch, [
            根 + r"\cloakbrowser\chrome.exe", "--remote-debugging-port=9222",
            r"--user-data-dir=" + 根 + r"\browser_data\account_0"])
        got = BL.browser_mode(9222)
        assert got["本项目"] is True and got["running"] is True, got

    def test_查不到进程不拿这个定罪(self, monkeypatch):
        """psutil 不可用/拿不到 pid 时不能把在跑的浏览器说成没跑。"""
        import psutil
        self._version_ok(monkeypatch)
        monkeypatch.setattr(psutil, "net_connections", lambda kind="tcp": [])
        monkeypatch.setattr(BL, "_OWNER_CACHE", {})
        assert BL.browser_mode(9222)["running"] is True

    def test_归属查询要缓存(self, monkeypatch):
        """/api/status 几秒一次，每次都翻进程表就是把运维检查做成性能问题。"""
        calls = []
        import psutil

        class _C:
            laddr = type("A", (), {"port": 9222})()
            pid = 1
            status = "LISTEN"

        def fake(kind="tcp"):
            calls.append(1)
            return [_C()]

        monkeypatch.setattr(psutil, "net_connections", fake)
        monkeypatch.setattr(BL, "_OWNER_CACHE", {})
        for _ in range(5):
            BL.port_is_ours(9222)
        assert len(calls) == 1, calls


def test_界面把端口上是别人浏览器这件事说出来():
    """端口答了就算"在跑"会谎报：9223 上是 boss-auto-apply 的浏览器，我们那个号是停的。"""
    html = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
    段 = html[html.index("function renderHeadlessReality"):html.index("function applyStatusUpdate")]
    assert "b.本项目 === false" in 段, 段[:400]
    assert "别的项目的浏览器" in 段


def test_补拒类工具动手前先验端口归属():
    """attach() 按端口连，谁在听连谁——别人的浏览器上点一次"拒绝"就是替我们的号说话。"""
    src = (ROOT / "tools" / "decline_offline_interviews.py").read_text(encoding="utf-8")
    段 = src[src.index("def attach("):src.index("def search_contact(")]
    assert "port_is_ours" in 段, 段[:200]
    assert "拒绝在这个号上动手" in 段
