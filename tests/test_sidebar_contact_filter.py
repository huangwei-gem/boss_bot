# -*- coding: utf-8 -*-
"""侧栏滚不到的会话改用联系人搜索点开（日志一天三百多条跳过逼出来的）。

根因不是搜索框，是侧栏虚拟列表：171 行只渲染 ~40 行，不排在前面的人滚到底
也不在 DOM 里，于是「侧栏滚到底也没有会话」——这些人一次回复都收不到。

实测过两件事（tools/check_contact_filter.py --diagnose）：
1. 搜索框是受控组件，直接赋 .value 不认，要走原生 setter 再派发 input 事件；
2. 打字不会筛侧栏（.friend-content 一行不少），它弹自己的结果浮层
   li.search-list，回车没用，必须点浮层里那一行。

第二条决定了一个必须测的接线：浮层那一下点击已经把会话打开了，
enter_chat 不许再去点一次侧栏行；用完还得清掉搜索框里的字。
"""
import json

import pytest

from boss_bot.page_handler import BossChatHandler as PageHandler


def _handler(page=None):
    ph = PageHandler.__new__(PageHandler)
    ph.page = page
    return ph


class Recorder:
    """记录调用顺序，并按预设回答"行在不在屏幕上 / 搜索点不点得到"。"""

    def __init__(self, has_row=False, scroll_ok=False, search_ok=True):
        self.calls = []
        self._has_row = has_row
        self._scroll_ok = scroll_ok
        self._search_ok = search_ok

    def sidebar_has_row(self, name, company):
        self.calls.append(("has_row", name, company))
        return self._has_row

    def scroll(self, name, company, max_steps=30):
        self.calls.append(("scroll", name, company))
        return self._scroll_ok

    def search(self, name, company, timeout=6.0):
        self.calls.append(("search", name, company))
        if isinstance(self._search_ok, str):
            return self._search_ok
        return "ok" if self._search_ok else "no_result"

    def clear(self):
        self.calls.append(("clear",))

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def wire():
    def build(rec, **over):
        ph = _handler()
        ph._sidebar_has_row = rec.sidebar_has_row
        ph._scroll_to_chat_row = rec.scroll
        ph._open_chat_by_search = rec.search
        ph._clear_contact_filter = rec.clear
        for k, v in over.items():
            setattr(ph, k, v)
        return ph
    return build


def _handler_with(rec):
    """不用 fixture 的写法：有些类里的用例只想按录好的答案走一遍三级定位。"""
    ph = _handler()
    ph._sidebar_has_row = rec.sidebar_has_row
    ph._scroll_to_chat_row = rec.scroll
    ph._open_chat_by_search = rec.search
    ph._clear_contact_filter = rec.clear
    return ph


class Test定位三级:
    def test_行就在屏幕上时不滚也不搜(self, wire):
        rec = Recorder(has_row=True)
        assert wire(rec)._locate_chat_row("陈女士", "爱森电商") == (True, False)
        assert rec.names() == ["has_row"]

    def test_滚到了就不用搜(self, wire):
        rec = Recorder(has_row=False, scroll_ok=True)
        assert wire(rec)._locate_chat_row("陈女士", "爱森电商") == (True, False)
        assert rec.names() == ["has_row", "scroll"]

    def test_滚到底才动搜索(self, wire):
        rec = Recorder(has_row=False, scroll_ok=False, search_ok=True)
        assert wire(rec)._locate_chat_row("陈女士", "爱森电商") == (True, True)
        assert rec.names() == ["has_row", "scroll", "search"]

    def test_三级都用上还是没人就认失败(self, wire):
        rec = Recorder(has_row=False, scroll_ok=False, search_ok=False)
        assert wire(rec)._locate_chat_row("陈女士", "爱森电商") == (False, False)


class Test搜索点开后:
    CHAT = {"name": "陈女士", "company": "爱森电商", "index": 3}

    def test_只剩核对不许再点一次侧栏(self, wire):
        rec = Recorder(has_row=False, scroll_ok=False, search_ok=True)
        verified = []
        ph = wire(rec, _wait_and_verify=lambda n, c, opened_by_index=False:
                  verified.append((n, c)) or True,
                  _click_and_verify=lambda ci, retries: pytest.fail(
                      "搜索浮层已经点开了会话，不该再点侧栏行"))
        assert ph.enter_chat(dict(self.CHAT)) is True
        assert verified == [("陈女士", "爱森电商")]

    def test_用完清掉搜索框(self, wire):
        rec = Recorder(has_row=False, scroll_ok=False, search_ok=True)
        ph = wire(rec, _wait_and_verify=lambda n, c, opened_by_index=False: True)
        ph.enter_chat(dict(self.CHAT))
        assert rec.names()[-1] == "clear"

    def test_核对没过也要清(self, wire):
        """漏清的话，下一轮搜索框里还留着上一个名字，结果浮层会替我们"选中"
        一个上一个人——回错人比不回更糟。"""
        rec = Recorder(has_row=False, scroll_ok=False, search_ok=True)
        ph = wire(rec, _wait_and_verify=lambda n, c, opened_by_index=False: False)
        assert ph.enter_chat(dict(self.CHAT)) is False
        assert rec.names()[-1] == "clear"

    def test_行本来就在屏幕上就不许动搜索框(self, wire):
        rec = Recorder(has_row=True)
        ph = wire(rec, _click_and_verify=lambda ci, retries: True)
        assert ph.enter_chat(dict(self.CHAT)) is True
        assert "search" not in rec.names() and "clear" not in rec.names()

    def test_压根找不到人就不清也不核对(self, wire):
        rec = Recorder(has_row=False, scroll_ok=False, search_ok=False)
        checked = []
        ph = wire(rec, _wait_and_verify=lambda n, c, opened_by_index=False:
                  checked.append(1) or True)
        assert ph.enter_chat(dict(self.CHAT)) is False
        assert not checked and "clear" not in rec.names()


class Test搜索框写法:
    def test_受控组件要走原生setter(self):
        """直接 inp.value=x 时 React 不认，写字等于没写"""
        js = PageHandler._CONTACT_FILTER_JS
        assert "HTMLInputElement.prototype" in js
        assert 'dispatchEvent(new Event("input"' in js
        assert "boss-search-input" in js

    def test_结果行按姓名加公司认人(self):
        """重名在侧栏实测 4 组/34 行，只认姓名会回错人；
        浮层里的公司名是简称，所以互为包含算同一家。"""
        js = PageHandler._SEARCH_RESULT_JS
        assert "li.search-list" in js
        assert ".boss-name" in js and ".company-name" in js
        assert "indexOf(b) >= 0" in js and "b.indexOf(a) >= 0" in js
        assert 'items[i].click()' in js, "浮层不点就没有下一步，回车是没用的"

    def test_浮层只剩一个人时公司截断也要点开(self):
        """存档里的公司是被截过名的（"义乌市睿笔网络技..."），互为包含也配不上。

        实测 2026-10-07：跳过 156 次里 23 个是"姓名在浮层、公司全等失败"。
        搜索关键字就是姓名，浮层只剩一行时已经没有第二种可能，
        不点等于当着 HR 的面不回话；点完还有顶栏姓名核对兜着。
        """
        js = PageHandler._SEARCH_RESULT_JS
        assert "unique_name" in js, "浮层唯一命中没被区分出来，只能整条判成找不到"
        assert "items.length === 1" in js

    def test_唯一命中当成正面结果(self):
        rec = Recorder(has_row=False, scroll_ok=False, search_ok="unique_name")
        ph = _handler_with(rec)
        assert ph._locate_chat_row("陈女士", "旺旺集团") == (True, True)

    def test_多个同名时不许猜一个(self):
        """重名是实测存在的：浮层里剩 3 个"刘女士"时，公司又对不上就只能放弃，
        点错一个等于给另一个公司的人回话。"""
        rec = Recorder(has_row=False, scroll_ok=False, search_ok="name_only")
        ph = _handler_with(rec)
        assert ph._locate_chat_row("刘女士", "沙果") == (False, False)
        assert "clear" not in rec.names(), "没点开就别清搜索框，更别核对陌生人"

    def test_写字时姓名转义进JS(self):
        sent = []

        class P:
            def run_js(self, script, as_expr=False):
                sent.append(script)
                return "ok"
        ph = _handler(P())
        assert ph._set_contact_filter("陈女士") is True
        assert json.dumps("陈女士", ensure_ascii=False) in sent[0]

    def test_清空传的是空串(self):
        sent = []

        class P:
            def run_js(self, script, as_expr=False):
                sent.append(script)
                return "ok"
        ph = _handler(P())
        ph._clear_contact_filter()
        assert 'setter.call(inp, "")' in sent[0]

    def test_名字为空时不去写字(self):
        """空关键字会搜出整屏陌生人，然后被"公司包含"挑中一个不相干的人"""
        written = []

        class P:
            def run_js(self, script, as_expr=False):
                written.append(1)
                return "ok"
        ph = _handler(P())
        assert ph._open_chat_by_search("", "爱森电商") == "no_name"
        assert not written

    def test_页面没有搜索框时不装作成功(self):
        class P:
            def run_js(self, script, as_expr=False):
                return "no-input"
        ph = _handler(P())
        assert ph._set_contact_filter("陈女士") is False
        assert ph._open_chat_by_search("陈女士", "爱森电商") == "no_input"

    def test_浮层一直没有结果时不无限等(self):
        # 写字成功了、只是搜不到人：这种要走到超时，不能当成"页面没搜索框"
        class P:
            def run_js(self, script, as_expr=False):
                return "ok" if "HTMLInputElement" in script else "no_result"
        ph = _handler(P())
        assert ph._open_chat_by_search("陈女士", "爱森电商", timeout=0.6) == "no_result"
