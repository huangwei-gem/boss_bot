# -*- coding: utf-8 -*-
"""账号2 为什么"老是让登录"：盘上那四份登录项是会话级的，浏览器一重启就没了。

线上取证（2026-10-19，logs/boss_bot.log + 两份 Cookie 文件原样）：

    14:23:38,209 browser_launcher  Cookie 落盘: 17 个 Cookie 已写入 zhipin_cookies_1.json
    14:23:52,366 page_handler      用户确认登录，正在保存 Cookie...
    14:24:06,156 page_handler      ✅ Cookie 已保存（15 个 Cookie 已写入 ...）

14:23:38 那份（自动那条腿）字段是齐的：

    {"name":"isOHPC","value":"1","domain":"www.zhipin.com","path":"/",
     "expires":1794967996.8,"httpOnly":false,"session":false,...}   ← 16~17 条

14:24:06 他点了「我已登录」之后，同一份文件被换成：

    {"name":"wt2","value":"Dk1vT7GO3g...","domain":".zhipin.com"}    ← 15 条，只剩三个字段

差在这一处：`page_handler._get_all_cookies()` 自己另写了一套读取，先问
`_get_browser_obj()` —— 那个函数返回的是 `BrowserInstance.browser`，而它是
`self._chromium`，**只有 macOS 那条路才有值**；Windows 上是 None，于是每次
都掉进兜底 `self.browser.cookies()`。DrissionPage 4.1.1.4 里这个签名不带
`all_info` 时只做一件事（_pages/chromium_base.py:418）：

    r = [{'name': c['name'], 'value': c['value'], 'domain': c['domain']} for c in cookies]

path / expires / httpOnly / secure 全被削掉，还只取当前那一张页的域名范围。
Chrome 只把**带到期时间**的 Cookie 写进 profile，会话级的那一份随进程一起没了，
所以下一次启动（内存回收、无头↔有头切换、页面断开重开）就得重新人工登录。
他看到的"账号2 老是让我登录"就是这么来的。

这一轮把三件事钉死：读取只许一份、落盘要留全字段并给会话级登录项补到期时间、
写完回灌一次让正在跑的浏览器也换成持久 Cookie。
"""

import io
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.browser_launcher import (
    BrowserInstance, 保存登录Cookie, 可写入Cookie, 规范待写Cookie, 会话Cookie续期天数)

VERIFY_URL = ("https://www.zhipin.com/web/passport/zp/verify.html"
              "?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fgeek%2Fjob-recommend")
# 线上盘上那一份的原样：14:24:06 被「我已登录」削成的形态
三字段会话项 = {"name": "wt2", "value": "Dk1vT7GO3gZuxqyk", "domain": ".zhipin.com"}


def 浏览器里的一条(名="zp_at", 值="v", 到期=-1, 域名=".zhipin.com", **over):
    """CDP Storage.getCookies 回来的那种形态（只读视图，字段一大把）"""
    c = {"name": 名, "value": 值, "domain": 域名, "path": "/", "expires": 到期,
         "size": len(名) + len(值), "httpOnly": False, "session": 到期 < 0,
         "priority": "Medium", "sourceScheme": "NonSecure", "sourcePort": 443}
    c.update(over)
    return c


class 假浏览器对象:
    """Windows 上 `ChromiumPage.browser` 那个位置的东西：只认 CDP"""

    def __init__(self, cookies):
        self._cookies = [dict(c) for c in cookies]
        self.cdp = []
        self.拒绝写 = False

    def _run_cdp(self, method, **kw):
        self.cdp.append((method, kw))
        if method == "Storage.getCookies":
            return {"cookies": [dict(c) for c in self._cookies]}
        if method == "Storage.setCookies" and self.拒绝写:
            raise RuntimeError("CDP 断了")
        return {}


class 假页面:
    """形态照 ChromiumPage：cookies() 不带 all_info 就只回三个字段"""

    def __init__(self, cookies):
        self.browser = 假浏览器对象(cookies)
        self._cookies = [dict(c) for c in cookies]
        self.url = "https://www.zhipin.com/web/geek/chat"
        self.got = []

    def cookies(self, all_domains=False, all_info=False):
        if all_info:
            return [dict(c) for c in self._cookies]
        return [{"name": c["name"], "value": c["value"], "domain": c["domain"]}
                for c in self._cookies]

    def get(self, url):
        self.got.append(url)

    def run_js(self, js):
        return None


def 真浏览器(cookies):
    """Windows 形态的 BrowserInstance：_page 有值、_chromium 是 None"""
    return BrowserInstance(chrome_page=假页面(cookies))


def _写(path, cookies):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False)


def _读(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _挑(盘上, 名):
    for c in 盘上:
        if c.get("name") == 名:
            return c
    raise AssertionError(f"盘上没有 {名}: {[c.get('name') for c in 盘上]}")


class Test落盘要留全字段并补到期时间:
    """会话级 Cookie 落盘 = 写了一份注定会随进程消失的凭据"""

    def test_BOSS域名的会话级登录项要补上未来到期时间(self):
        now = time.time()
        出 = 规范待写Cookie([三字段会话项], now=now)
        assert 出[0]["expires"] > now, "没到期时间的登录项必须补一个未来时间戳"
        assert 出[0]["expires"] <= now + 会话Cookie续期天数 * 86400 + 5

    def test_已经有到期时间的不许改写(self):
        """BOSS 自己发的真到期时间是判据的一部分，不能一律刷成 30 天"""
        未来 = time.time() + 3600
        出 = 规范待写Cookie([浏览器里的一条("bst", 到期=未来)], now=time.time())
        assert 出[0]["expires"] == 未来

    def test_域外的会话Cookie不许续期(self):
        """别家的临时凭据续成 30 天是凭空造状态，只动 zhipin 的"""
        出 = 规范待写Cookie([浏览器里的一条("abtest", 域名="apihub.agnes-ai.com",
                                        到期=-1)], now=time.time())
        assert 出[0]["expires"] == -1

    def test_补了到期时间就不许再自称会话级(self):
        出 = 规范待写Cookie([浏览器里的一条(到期=-1)], now=time.time())
        assert 出[0].get("session") is False, (
            "盘上写着 session=true 却带 expires，回读时按哪算？")

    def test_缺path的补成根路径(self):
        出 = 规范待写Cookie([三字段会话项], now=time.time())
        assert 出[0]["path"] == "/"

    def test_不许改进出参那份列表(self):
        原 = [dict(三字段会话项)]
        规范待写Cookie(原, now=time.time())
        assert 原 == [三字段会话项], "把浏览器那份就地改了，调用方后面的判断就废了"

    def test_保存登录Cookie写进盘的是规范后的那份(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            成功, 说明 = 保存登录Cookie(target, [三字段会话项,
                                             浏览器里的一条("bst", 到期=-1)])
            assert 成功 is True, 说明
            盘上 = _读(target)
            assert _挑(盘上, "wt2")["expires"] > time.time()
            assert _挑(盘上, "bst")["path"] == "/"
            assert "30 天" in 说明 or "有效期" in 说明, f"说明要交代补了什么: {说明}"


class Test回灌浏览器的字段要CDP收得下:
    """读回来的是只读视图，原样喂回 Storage.setCookies 是把 size/session 也塞进去"""

    def test_只留setCookies收得下的字段(self):
        出 = 可写入Cookie(浏览器里的一条(到期=time.time() + 100))
        assert set(出) <= {"name", "value", "domain", "path", "expires",
                           "httpOnly", "secure", "sameSite", "priority", "sameParty"}, \
            f"混进了只读字段: {sorted(set(出))}"
        assert "size" not in 出 and "session" not in 出

    def test_盘上只剩三字段的老文件也要能写回去(self):
        """用户现在盘上就是那份被削薄的文件：加载这一端要自己补全，
        不能要求他先重登一次才能把这次修复用上"""
        出 = 可写入Cookie(规范待写Cookie([三字段会话项], now=time.time())[0])
        assert 出["path"] == "/" and 出["expires"] > time.time()

    def test_回灌时喂给CDP的是补全后的(self):
        inst = 真浏览器([浏览器里的一条(到期=-1)])
        inst._set_cookies([dict(三字段会话项)])
        方法, 参数 = inst._page.browser.cdp[-1]
        assert 方法 == "Storage.setCookies", f"没走 CDP: {方法}"
        喂进去 = 参数["cookies"][0]
        assert 喂进去["expires"] > time.time() and 喂进去["path"] == "/"
        assert "size" not in 喂进去


class Test确认登录那条腿不再把会话写薄:
    """14:24:06 那一下：他点「我已登录」，盘上那份齐的反而被换成薄的"""

    def _handler(self, cookies, tmp, 停在="https://www.zhipin.com/web/geek/chat"):
        from boss_bot.page_handler import BossChatHandler
        target = os.path.join(tmp, "zhipin_cookies_1.json")
        _写(target, [浏览器里的一条("zp_at", 值="上一轮的好会话", 到期=time.time() + 7200)])
        with patch("boss_bot.page_handler.launch_browser"):
            h = BossChatHandler(cookie_file=target)
        h.browser = 真浏览器(cookies)
        # 页要拿真的假对象：MagicMock 上 `page.got` 永远不是 []，"没导航"这条断言
        # 会绿成假绿（而"不许导航"正是这次的验收项之一）
        h.page = 假页面(cookies)
        h.page.url = 停在
        return h, target

    def test_确认登录后盘上还得是全字段带到期时间的(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            h, target = self._handler(
                [浏览器里的一条("wt2", 到期=-1), 浏览器里的一条("bst", 到期=-1)], td)
            成功, 说明 = h._save_cookies()
            assert 成功 is True, 说明
            盘上 = _读(target)
            assert len(盘上) == 2, f"只存了当前那张页的 Cookie: {len(盘上)} 条"
            for 名 in ("wt2", "bst"):
                assert _挑(盘上, 名)["expires"] > time.time(), (
                    f"{名} 还是会话级——浏览器一重启这个号就得重登")
                assert _挑(盘上, 名)["path"] == "/"

    def test_page_handler里不许再有第二套Cookie读取(self):
        """兜底那套 `cookies()` 就是削字段的凶手；读取只许 browser_launcher 一处

        按 AST 查而不是按源码文本查：这个模块的注释里要解释当年那套读取为什么错，
        文本门会把解释一起杀掉，于是下一次没人愿意写注释。
        """
        import ast
        import boss_bot.page_handler as PH
        树 = ast.parse(io.open(PH.__file__, encoding="utf-8").read())
        函数名 = {n.name for n in ast.walk(树) if isinstance(n, ast.FunctionDef)}
        assert "_get_all_cookies" not in 函数名 and "_get_browser_obj" not in 函数名, (
            f"page_handler 又自己开了一套 Cookie 读取: {sorted(函数名)}")
        字符串 = {n.value for n in ast.walk(树) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str)}
        assert "Storage.getCookies" not in 字符串, "CDP 读取只许在 browser_launcher 一处"

    def test_确认登录不许把风控验证页掀走(self):
        """他正滑到一半，`self.page.get("https://www.zhipin.com")` 一冲就白做"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            h, _ = self._handler([浏览器里的一条("wt2", 到期=-1)], td, 停在=VERIFY_URL)
            assert h.confirm_and_save() is True
            assert h.page.got == [], "人在验证页上，这一页一次都不许刷新"

    def test_停在任何页面确认登录都不导航(self):
        """那句"先导航到主站确保 Cookie 作用域正确"已经不成立了：读的是
        浏览器级 Storage.getCookies，本来就跨域名。为没有收益的动作去掀人的页面，
        就是 2026-10-19「你刷新太频繁了，我根本就登录不了」那一头。"""
        import tempfile
        for 停在 in ("https://www.zhipin.com/web/geek/chat",
                     "https://www.zhipin.com/web/user/?ka=header-login",
                     VERIFY_URL):
            with tempfile.TemporaryDirectory() as td:
                h, target = self._handler([浏览器里的一条("wt2", 到期=-1)], td, 停在=停在)
                assert h.confirm_and_save() is True, 停在
                assert h.page.got == [], f"{停在} 被导航了 {h.page.got}"
                assert _挑(_读(target), "wt2")["expires"] > time.time()

    def test_确认登录那条路上不许出现任何一次导航(self):
        import inspect
        from boss_bot.page_handler import BossChatHandler
        段 = inspect.getsource(BossChatHandler.confirm_and_save)
        assert "page.get(" not in 段, "confirm_and_save 里又剩了一次翻页"


class Test存完就把持久Cookie打回正在跑的浏览器:
    """只写盘不够：此刻这个浏览器里的登录项还是会话级的，进程一走就没了"""

    def test_落盘成功后回灌一次(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            inst = 真浏览器([浏览器里的一条("wt2", 到期=-1)])
            inst.save_cookies(target)
            回灌 = [kw for 方法, kw in inst._page.browser.cdp
                    if 方法 == "Storage.setCookies"]
            assert 回灌, "落盘成了却没把带有效期的那份写回浏览器"
            assert 回灌[-1]["cookies"][0]["expires"] > time.time()

    def test_回灌失败不许把落盘说成失败(self):
        """盘上已经成了，这时候因为 CDP 报错去 raise 会让人以为没登录成功"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            inst = 真浏览器([浏览器里的一条("wt2", 到期=-1)])
            inst._page.browser.拒绝写 = True
            说明 = inst.save_cookies(target)
            assert _读(target)[0]["expires"] > time.time(), "文件没写成"
            assert "个 Cookie" in 说明, f"成功那句话还得留着: {说明}"

    def test_读不到登录项照旧raise不动文件(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            _写(target, [浏览器里的一条("zp_at", 值="上一轮的好会话",
                                    到期=time.time() + 7200)])
            inst = 真浏览器([{"name": "abtest", "value": "1", "domain": ".zhipin.com"}])
            try:
                inst.save_cookies(target)
                raise AssertionError("闸门没拦住")
            except RuntimeError as e:
                assert "登录" in str(e)
            assert _读(target)[0]["value"] == "上一轮的好会话"

    def test_三条腿都从同一个save_cookies走(self):
        import inspect
        from boss_bot.page_handler import BossChatHandler
        from boss_bot.greet_engine import GreetEngine
        for 名, fn in (("page_handler", BossChatHandler._save_cookies),
                       ("greet_engine", GreetEngine._save_cookies)):
            段 = inspect.getsource(fn)
            assert "save_cookies(" in 段, (
                f"{名} 还在自己读自己写，拿不到这次补的到期时间")


class Test测试不许往用户的备份目录里塞东西:
    """2026-10-19 14:26 我自己跑一趟 pytest，data/cookie_backups/ 里就多了三份
    "上一轮的好会话"——那是 fixture 造的假 Cookie，会把真备份按数量轮换挤掉"""

    def test_备份目录在测试里指向临时处(self):
        import boss_bot.browser_launcher as BL
        目录 = BL._cookie_backup_dir()
        assert os.path.normpath(目录).lower() != os.path.normpath(
            os.path.join(ROOT, "data", "cookie_backups")).lower(), (
            "cookie 备份目录还指着仓库那份：测试会挤掉用户真正的登录备份")
