"""起浏览器前必须认出端口上那个浏览器是不是自己的（task #38）。

2026-09-29 现场：主号整天 0 条投递，`netstat` 查到 9222 上挂着一个 11:40 起的
孤儿 cloakbrowser（父进程已亡，profile 是 DrissionPage 的临时目录，只开着
chrome://newtab）。`_launch_windows` 走 `co.set_local_port(9222)`，而 DrissionPage
的语义是"端口上已经有浏览器就直接连"——于是主号连着这个没有任何登录态的空壳
跑，一进 BOSS 就是手机号+短信验证登录墙，表现成成片的"未找到输入框"。

来源是 tools/extract_chat_structure.py：ChromiumOptions 既没设端口也没设 profile，
直接落在 DrissionPage 默认的 9222 + 临时目录上。
"""

import json
import re
from pathlib import Path

import pytest

from boss_bot.browser_launcher import (
    assert_port_is_free_for, parse_browser_cmdline, same_profile)

# 真机取到的孤儿浏览器命令行（截断到够用的部分）
ORPHAN_CMD = (
    r'C:\Users\35796\orca\boss_bot\cloakbrowser\chrome.exe '
    r'--remote-debugging-port=9222 '
    r'--user-data-dir="C:\Users\35796\AppData\Local\Temp\DrissionPage\userData\9222" '
    r'--no-first-run'
)
MINE_CMD = (
    r'C:\Users\35796\orca\boss_bot\cloakbrowser\chrome.exe '
    r'--remote-debugging-port=9222 '
    r'--user-data-dir="C:\Users\35796\orca\boss_bot\browser_data\account_0" '
    r'--no-first-run'
)


class ParseCmdlineTest:
    def test_解析出端口和profile(self):
        got = parse_browser_cmdline(ORPHAN_CMD, pid=42228)
        assert got["port"] == 9222
        assert got["user_data_dir"].endswith(r"Temp\DrissionPage\userData\9222")
        assert got["pid"] == 42228

    def test_没写端口就是DrissionPage默认9222(self):
        """不设 port 的脚本（本次事故来源）也要被认出来"""
        cmd = MINE_CMD.replace("--remote-debugging-port=9222 ", "")
        assert parse_browser_cmdline(cmd)["port"] == 9222

    def test_没写profile就是空串(self):
        cmd = "chrome.exe --remote-debugging-port=9402"
        assert parse_browser_cmdline(cmd)["user_data_dir"] == ""


class SameProfileTest:
    def test_大小写和分隔符和尾斜杠都算同一个(self):
        a = r"C:\Users\35796\orca\boss_bot\browser_data\account_0"
        for b in (a, a.lower(), a.replace("\\", "/"), a + "\\", a.replace("\\", "/") + "/"):
            assert same_profile(a, b), b

    def test_不同账号目录不算同一个(self):
        assert not same_profile(
            r"C:\x\browser_data\account_0", r"C:\x\browser_data\account_1")

    def test_空profile不参与比较(self):
        assert not same_profile("", r"C:\x\browser_data\account_0")


class GuardTest:
    ORPHAN = [{"pid": 42228, "port": 9222,
               "user_data_dir": r"C:\Users\35796\AppData\Local\Temp\DrissionPage\userData\9222",
               "exe": r"C:\x\cloakbrowser\chrome.exe"}]
    MINE = [{"pid": 50088, "port": 9222,
             "user_data_dir": r"C:\x\boss_bot\browser_data\account_0",
             "exe": r"C:\x\cloakbrowser\chrome.exe"}]

    def test_外来profile必须挡住(self):
        mine = r"C:\x\boss_bot\browser_data\account_0"
        with pytest.raises(RuntimeError) as e:
            assert_port_is_free_for(9222, mine, owners=self.ORPHAN)
        msg = str(e.value)
        assert "account_0" in msg, "要说清本账号该用哪个 profile"
        assert "DrissionPage" in msg, "要说清端口上实际挂的是哪个 profile"
        assert "42228" in msg, "要给 pid，否则人工没法收拾"
        assert "taskkill" in msg, "要给可直接执行的处置命令"

    def test_自己的profile挂着就是复用(self):
        mine = r"C:\x\boss_bot\browser_data\account_0"
        assert_port_is_free_for(9222, mine, owners=self.MINE)

    def test_端口没人不用挡(self):
        assert_port_is_free_for(9223, r"C:\x\browser_data\account_1", owners=self.MINE)

    def test_没传profile时不拦(self):
        """老 CLI 不带 profile，无从判定，不能因此把人挡在门外"""
        assert_port_is_free_for(9222, "", owners=self.ORPHAN)


class WiredIntoLaunchTest:
    def test_launch_windows调用守卫(self, monkeypatch, tmp_path):
        from boss_bot import browser_launcher as BL

        calls = []
        monkeypatch.setattr(BL, "_browser_owners", lambda: GuardTest.ORPHAN)
        monkeypatch.setattr(BL, "_is_port_open", lambda h, p, timeout=1.0: True)

        import DrissionPage
        def boom(*a, **k):
            calls.append(1)
            raise AssertionError("守卫失效，还是去连外来浏览器了")
        monkeypatch.setattr(DrissionPage, "ChromiumPage", boom)

        mine = tmp_path / "browser_data" / "account_0"
        with pytest.raises(RuntimeError) as e:
            BL._launch_windows(chrome_path="chrome.exe", headless=False, user_agent="",
                               proxy="", viewport_width=1280, viewport_height=800,
                               port=9222, user_data_dir=str(mine))
        assert "不是本账号" in str(e.value)
        assert not calls, "DrissionPage 都不该被调到"
        assert not mine.exists(), "挡下来了就不该再把本账号的 profile 目录建出来"

    def test_端口空着时不查询进程(self, monkeypatch, tmp_path):
        """没占用就不必为每次启动多付一次 PowerShell 查询"""
        from boss_bot import browser_launcher as BL
        hit = []
        monkeypatch.setattr(BL, "_browser_owners", lambda: hit.append(1) or [])
        monkeypatch.setattr(BL, "_is_port_open", lambda h, p, timeout=1.0: False)
        import DrissionPage
        monkeypatch.setattr(DrissionPage, "ChromiumPage",
                            lambda co, **k: type("P", (), {})())
        BL._launch_windows(chrome_path="chrome.exe", headless=False, user_agent="",
                           proxy="", viewport_width=1280, viewport_height=800,
                           port=9222, user_data_dir=str(tmp_path / "account_0"))
        assert not hit


class PowerShellEncodingTest:
    def test_控制台不是utf8时也要认得出浏览器(self, monkeypatch):
        """真机踩过：PowerShell 按 cp936 输出，subprocess(text=True) 的 utf-8
        解码在读取线程里抛异常，_browser_owners() 只剩空表——守卫看着在跑，
        其实一个都不挡，主号照样连着空壳浏览器跑一整天。"""
        from boss_bot import browser_launcher as BL

        line = ("42228|" + ORPHAN_CMD.replace("C:\\Users", "C:\\用户") + "\r\n")
        captured = {}

        class _FakeSubprocess:
            @staticmethod
            def run(*a, **k):
                captured.update(k)
                return type("R", (), {"stdout": line.encode("cp936"),
                                      "stderr": b"", "returncode": 0})()

        monkeypatch.setattr(BL, "subprocess", _FakeSubprocess())
        owners = BL._browser_owners()
        assert not captured.get("text"), "不许再开 text=True，编码得自己兜"
        assert len(owners) == 1, owners
        assert owners[0]["pid"] == 42228
        assert "用户" in owners[0]["user_data_dir"], "中文路径要还原，不能变乱码"


class NoToolSquatsDefaultPortTest:
    """事故源头：一次性脚本不写端口也不写 profile，正好落到默认 9222 上"""

    @pytest.mark.parametrize("name", ["extract_chat_structure.py", "extract_full_fields.py"])
    def test_脚本必须自带端口和profile(self, name):
        src = (Path(__file__).resolve().parent.parent / "tools" / name).read_text(
            encoding="utf-8")
        i = src.index("ChromiumPage(")
        body = src[max(0, i - 1200):i]
        assert re.search(r"set_local_port|auto_port", body), f"{name} 没设端口"
        assert re.search(r"user_data_path|user-data-dir", body), \
            f"{name} 没设 profile，会用 DrissionPage 临时目录"

    def test_面板检测Cookie也占专用端口(self, tmp_path, monkeypatch):
        """/api/accounts/N/check_cookie 以前既不传端口也不传 profile：
        DrissionPage 退回默认 9222，于是"点一下检测账号2的登录态"是连进主账号
        正在投递的浏览器里翻页面，还顺手把 Cookie 文件里的旧会话注进去。"""
        from boss_bot import browser_launcher as BL

        seen = {}

        class FakeInstance:
            url = "https://www.zhipin.com/web/geek/job-recommend"

            def get(self, _u):
                pass

            def load_cookies(self, _p):
                return True

            def ele(self, _sel, timeout=None):
                return None

            def quit(self):
                pass

        def fake_launch(**kw):
            seen.update(kw)
            return FakeInstance()

        monkeypatch.setattr(BL, "launch_browser", fake_launch)
        ck = tmp_path / "cookies.json"
        ck.write_text(json.dumps([{"name": "wt2", "value": "x", "domain": ".zhipin.com",
                                   "path": "/", "expires": -1}]), encoding="utf-8")
        BL.check_cookie_valid(str(ck), port=9501, user_data_dir=str(tmp_path / "probe"))
        assert seen.get("port") == 9501, "检测 Cookie 不许退回默认端口"
        assert seen.get("user_data_dir"), "检测 Cookie 要用一次性 profile"

    def test_检测环境照抄生产而不是写死无头(self):
        """判定要代表真跑得通：生产改无头后被 BOSS 拦，检测也得跟着报失效，
        反过来写死 headless=True 会把好 Cookie 判死。"""
        src = (Path(__file__).resolve().parent.parent / "flask-version" / "app.py"
               ).read_text(encoding="utf-8")
        call = src.split("result = check_cookie_valid(")[1].split(")\n")[0]
        assert "headless=browser_cfg.headless" in call
        assert "port=9500 + idx" in call
