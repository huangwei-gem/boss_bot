# -*- coding: utf-8 -*-
"""点不开的那几路会话：认人的判据、滚到位的预算、失败原因各说各的。

2026-10-18 用临时浏览器实测（tools/probe_scroll_depth.py、
tools/probe_sidebar_membership.py、tools/probe_search_trigger.py）拿到三条事实，
每一条都对现在代码里的一处判死：

1. 侧栏不是"30 步够得着"。账号0 滚到第 76 步位置还在动（52570/54564px，
   收到 658 行），而 `_scroll_to_chat_row` 的上限是 30 步 ×700px = 21000px。
   "李女士|重庆劳多拉企业管理"这一行整份滚完是存在的（工具打出来了），
   可它排在滚不到的那半截里——那张交换微信的卡片因此挂了 45 小时。
2. 行里"公司"那一格和存档常不一致。实测"苏女士"行里写"厦门中洲联创科技"、
   存档是"厦门中洲联创"；"宋女士"那一行公司格是空的，而人确实在侧栏里
   （preview="接受一下面试"，正是存档里欠她的那句话）。
   现在探针和点击都要 `comp === want.c` 精确相等，对不上就判"查无此人"。
3. 所有失败原因被并进一句「侧栏滚到底、搜索也找不到会话」：搜索框没找到、
   浮层是空的、同名几行公司都对不上、脚本抛错——四种病开一张方子，
   所以这个问题藏了两天，看不出来到底是哪一头坏了。

顺带纠正一个我自己在上一轮下的错结论：`input.boss-search-input` 不是搜岗位的框。
线上聊天页只有一张可见搜索框，placeholder 就是"搜索30天内的联系人"，
写姓名进去 1~3 秒就出浮层（实测宋女士 4 行、李女士 39 行、唐女士 10 行）。
"""
import json
import logging
import pathlib

import pytest

from boss_bot.page_handler import BossChatHandler, 认行, 是同一家


def _page_handler源码():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "boss_bot" / "page_handler.py").read_text(encoding="utf-8")


def _main_loop源码():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")


def 行(name, company, preview="", index=0, y=0):
    return {"index": index, "name": name, "company": company,
            "preview": preview, "unread_count": 0, "_y": y}


class Test是同一家:
    def test_简称与全称互为包含算同一家(self):
        """实测：行里"厦门中洲联创科技"，存档"厦门中洲联创"。"""
        assert 是同一家("厦门中洲联创科技", "厦门中洲联创")
        assert 是同一家("厦门中洲联创", "厦门中洲联创科技")

    def test_一模一样也算(self):
        assert 是同一家("重庆劳多拉企业管理", "重庆劳多拉企业管理")

    def test_空的那一格不算匹配任何公司(self):
        """公司格空是"这一行没渲染出公司"，不是"和谁都能配"——
        配上就是拿名字猜人，重名实测一堆。"""
        assert not 是同一家("", "厦门中洲联创")
        assert not 是同一家("厦门中洲联创", "")
        assert not 是同一家("", "")

    def test_不含不互相包含就不算(self):
        assert not 是同一家("健萌", "茶颜悦色")

    def test_存档被截断成省略号也算同一家(self):
        """数过：2506 份会话存档里 208 份的公司长这样"上海华屹数码科技..."，
        那是 BOSS 自己截的。带着省略号谁也不包含谁，这些人一律"查无此人"。"""
        assert 是同一家("上海华屹数码科技有限公司", "上海华屹数码科技...")
        assert 是同一家("四会市优采信息科", "四会市优采信息科...")

    def test_整条都是省略号时不能配上任何一家(self):
        assert not 是同一家("...", "上海华屹数码科技")


class Test认行:
    """把"这一行是不是我要找的那个人"收成一个函数。

    以前这同一条判据写在三处（探针 JS、点击 JS、核对 selected 的 Python），
    改一处漏两处——今天这一族"点不开"里有两条就是这么来的。
    """

    李女士们 = [行("李女士", "尚美科技", index=0),
              行("李女士", "上海嘉驰信息技术咨询", index=1),
              行("李女士", "重庆劳多拉企业管理", index=2)]

    def test_同名多行时点对公司那一句(self):
        idx, 依据 = 认行(self.李女士们, "李女士", "重庆劳多拉企业管理")
        assert (idx, 依据) == (2, "company")

    def test_同名多行且公司都对不上时不猜(self):
        idx, 依据 = 认行(self.李女士们, "李女士", "不存在的公司")
        assert idx < 0
        assert 依据.startswith("dup_name")

    def test_公司简称也能对上(self):
        rows = [行("苏女士", "厦门中洲联创科技")]
        idx, 依据 = 认行(rows, "苏女士", "厦门中洲联创")
        assert (idx, 依据) == (0, "company")

    def test_公司那格空的行靠最后一句正文认(self):
        """实测宋女士那一行 company=""，但 .last-msg-text 是"接受一下面试"，
        和存档里欠她的最后一句一模一样。"""
        rows = [行("宋女士", "", "接受一下面试"),
                行("宋女士", "大连工学辅仕教育科技", "您好，看到您的招聘信息")]
        idx, 依据 = 认行(rows, "宋女士", "长沙南舒文化传媒", "接受一下面试")
        assert (idx, 依据) == (0, "preview")

    def test_空公司那行同名不唯一时还是不许猜(self):
        rows = [行("宋女士", "", "接受一下面试"),
                行("宋女士", "", "接受一下面试")]
        idx, 依据 = 认行(rows, "宋女士", "长沙南舒文化传媒", "接受一下面试")
        assert idx < 0

    def test_最后一句太短不足以定人(self):
        """"好的"那种两三个字的回复满屏都是，拿来当身份等于没身份。"""
        rows = [行("宋女士", "", "好的")]
        idx, 依据 = 认行(rows, "宋女士", "长沙南舒文化传媒", "好的")
        assert idx < 0

    def test_名字压根不在屏幕上(self):
        idx, 依据 = 认行(self.李女士们, "王先生", "尚美科技")
        assert idx < 0 and 依据 == "no_name"

    def test_存档没给公司时按名字唯一认(self):
        rows = [行("蔡青", "某某公司"), 行("汤军辉", "另一家")]
        idx, 依据 = 认行(rows, "蔡青", "")
        assert idx == 0 and 依据 == "company"

    def test_存档没给公司且同名两行时不认(self):
        rows = [行("李女士", "尚美科技"), 行("李女士", "健萌")]
        idx, _ = 认行(rows, "李女士", "")
        assert idx < 0


class FakePage:
    """侧栏假页面：一屏 5 行、行高 80px，按 scrollTop 决定渲染哪几行。

    只认三种脚本：复位（RESET）、跳到指定位置（JUMP）、往下滚（scrollTop），
    其余按"读当前渲染的侧栏行"回 JSON。
    """

    行高 = 80
    屏 = 10

    def __init__(self, rows):
        self.rows = rows
        self.pos = 0
        self.clicks = []
        self.jumps = []
        self.scrolls = 0
        self.url = "https://www.zhipin.com/web/geek/chat"

    def 渲染(self):
        start = self.pos // self.行高
        return self.rows[start:start + self.屏]

    def run_js(self, js, as_expr=False):
        if "READ_ROWS" in js:
            return json.dumps([dict(r, index=i, _y=self.pos)
                               for i, r in enumerate(self.渲染())],
                              ensure_ascii=False)
        if "RESET" in js:
            self.pos = 0
            return "0"
        if "JUMP" in js:
            self.jumps.append(int(js.split("JUMP_TO:")[1].split("*/")[0]))
            self.pos = self.jumps[-1]
            return str(self.pos)
        if "scrollTop" in js:
            self.scrolls += 1
            self.pos = min(self.pos + 700,
                           max(0, len(self.rows) * self.行高 - self.屏 * self.行高))
            return f"{self.pos}/{len(self.rows) * self.行高}"
        if 'friends[' in js:
            idx = int(js.split("friends[")[1].split("]")[0])
            self.clicks.append(idx)
            return "ok"
        return ""


def 装(rows):
    h = BossChatHandler.__new__(BossChatHandler)
    h.page = FakePage(rows)
    return h


class Test滚得到位:
    def test_收集时把每一行的滚动位置记进名册(self):
        h = 装([行("HR0", "公司0"), 行("HR1", "公司1")])
        h._sidebar_rows()
        assert h.行位置()["HR0"] == [0], "跳格要靠这个位置，没记就得逐格滚"

    def test_一个名字只留最近几格并且新的先用(self):
        """每跳一格都要读一遍屏，记几十个位置会把找行本身拖成慢动作；
        列表也随时重排，最老那些位置本来就没意义。"""
        h = 装([])
        for i in range(20):
            h._登记行位置([行("HR9", "公司9", y=i * 700)])
        表 = h.行位置()["HR9"]
        assert len(表) == BossChatHandler.名册每人最多记
        assert h._名册位置("HR9")[0] == 19 * 700, "最近记的那一格应该先试"

    def test_名册里有位置就先跳那一格(self, monkeypatch):
        """深排在后面的会话，逐格滚到那儿要几十步；采集那趟本来就把位置读过了，
        跳过去一眼核对就行。"""
        from boss_bot import page_handler as ph
        rows = [行(f"HR{i}", f"公司{i}") for i in range(120)]
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装(rows)
        h.行位置()["HR100"] = [8000]
        assert h._scroll_to_chat_row("HR100", "公司100") is True
        assert h.page.jumps[:1] == [8000], "没按名册位置跳，还是在从顶逐格滚"

    def test_三十步之外的人才算数(self, monkeypatch):
        """这条就是那条 45 小时的卡片：目标在第 45 格，旧的 30 步上限永远够不着。"""
        from boss_bot import page_handler as ph
        rows = [行(f"HR{i}", f"公司{i}") for i in range(60)]
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装(rows)
        assert h._scroll_to_chat_row("HR45", "公司45") is True

    def test_真没有这个人还是要如实说找不到(self, monkeypatch):
        from boss_bot import page_handler as ph
        rows = [行(f"HR{i}", f"公司{i}") for i in range(20)]
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装(rows)
        assert h._scroll_to_chat_row("不存在", "某公司") is False

    def test_名册里压根没这个名字就别瞎滚(self, monkeypatch):
        """11:02 实测：为了找一个不在列表里的人，滚了 40 秒到 46348/46837
        才报"没到底"。本轮采集已经收过整份名册，名字不在里面就滚不出来。"""
        from boss_bot import page_handler as ph
        rows = [行(f"HR{i}", f"公司{i}") for i in range(60)]
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装(rows)
        h.seen_chat_rows = [行(f"HR{i}", f"公司{i}") for i in range(60)]
        assert h._scroll_to_chat_row("查无此人", "某公司") is False
        assert h.page.scrolls == 0, "名字不在名册里还滚，一轮十几条就是把回复腿拖死"


class Test点开用的也是同一份判据:
    def test_点击点的是公司对上的那一行而非第一个同名(self, monkeypatch):
        from boss_bot import page_handler as ph
        rows = [行("李女士", "尚美科技"), 行("李女士", "上海嘉驰信息技术咨询"),
                行("李女士", "重庆劳多拉企业管理")]
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装(rows)
        h._wait_and_verify = lambda n, c, **k: True
        assert h._click_and_verify({"name": "李女士", "company": "重庆劳多拉企业管理",
                                    "index": 0}, retries=0) is True
        assert h.page.clicks[0] == 2, f"点到第 {h.page.clicks[0]} 行，那是尚美科技的李女士"

    def test_核对selected时简称算同一家(self, monkeypatch):
        """点开成功了但核对判成陌生人，就会一路重试到"最终失败"——
        日志里那一族「会话切换校验失败」有这个成因。"""
        from boss_bot import page_handler as ph
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装([])
        h.read_selected_row = lambda: {"index": 3, "name": "苏女士",
                                      "company": "厦门中洲联创科技", "title": ""}
        h.get_boss_name = lambda: "苏女士"
        h.page.run_js = lambda js, as_expr=False: "ready"
        assert h._wait_and_verify("苏女士", "厦门中洲联创") is True

    def test_空公司那一行按正文点开之后核对也要放行(self, monkeypatch):
        from boss_bot import page_handler as ph
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装([])
        h.read_selected_row = lambda: {"index": 0, "name": "宋女士",
                                      "company": "", "title": ""}
        h.get_boss_name = lambda: "宋女士"
        h.page.run_js = lambda js, as_expr=False: "ready"
        assert h._wait_and_verify("宋女士", "长沙南舒文化传媒", 依据="preview") is True
        h.read_selected_row = lambda: {"index": 0, "name": "宋女士",
                                      "company": "大连工学辅仕教育科技", "title": ""}
        assert h._wait_and_verify("宋女士", "长沙南舒文化传媒", 依据="preview") is False

    def test_判据只留一处(self):
        """反向哨兵：不许再在 JS 里写第二份 姓名/公司 比较。"""
        src = _page_handler源码()
        assert "comp === want.c" not in src, "侧栏探针里那份精确相等的判据还留着"
        assert "_SIDEBAR_HAS_ROW_JS" not in src, "认行之外不该再有第二个认人入口"


class Test失败原因各说各的:
    """混在一句里就没法修：上一轮我就是被这句话带偏，跑去怀疑搜索框选错了。"""

    def 采(self, caplog):
        caplog.set_level(logging.WARNING, logger="boss_bot.page_handler")
        return [r.getMessage() for r in caplog.records
                if r.name == "boss_bot.page_handler"]

    def test_搜索框没找到说的是框的问题(self, caplog, monkeypatch):
        from boss_bot import page_handler as ph
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)

        class P:
            def run_js(self, js, as_expr=False):
                return "no-input" if "HTMLInputElement" in js else '{"v":"no_result"}'
        h = BossChatHandler.__new__(BossChatHandler)
        h.page = P()
        assert h._open_chat_by_search("宋女士", "某公司", timeout=0.3) == "no_input"
        文 = self.采(caplog)
        assert any("搜索框" in m for m in 文), 文

    def test_浮层空与同名对不上要写得不一样(self, caplog, monkeypatch):
        from boss_bot import page_handler as ph
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)

        def 造(回执):
            class P:
                def run_js(self, js, as_expr=False):
                    if "HTMLInputElement" in js:
                        return "ok"
                    return 回执
            h = BossChatHandler.__new__(BossChatHandler)
            h.page = P()
            return h
        caplog.clear()
        assert 造('{"v":"no_result"}')._open_chat_by_search(
            "许波波", "盒马鲜生", timeout=0.3) == "no_result"
        第一句 = self.采(caplog)
        caplog.clear()
        assert 造(json.dumps({"v": "name_only", "n": 39,
                             "comps": ["尚美科技", "健萌", "茶颜悦色"]}))._open_chat_by_search(
            "李女士", "重庆劳多拉企业管理", timeout=0.3) == "name_only"
        第二句 = self.采(caplog)
        assert 第一句 != 第二句
        assert any("39" in m for m in 第二句), f"同名有几个、公司是什么没打出来: {第二句}"
        assert any("尚美科技" in m for m in 第二句), 第二句

    def test_搜索脚本抛错要单独一条(self, caplog, monkeypatch):
        from boss_bot import page_handler as ph
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)

        class P:
            def run_js(self, js, as_expr=False):
                if "HTMLInputElement" in js:
                    return "ok"
                raise RuntimeError("页面断开")
        h = BossChatHandler.__new__(BossChatHandler)
        h.page = P()
        assert h._open_chat_by_search("宋女士", "某公司", timeout=0.3) == "error"
        assert any("报错" in m or "抛" in m for m in self.采(caplog))

    def test_enter_chat_把原因带进日志(self, caplog, monkeypatch):
        from boss_bot import page_handler as ph
        monkeypatch.setattr(ph.time, "sleep", lambda s: None)
        h = 装([行("HR0", "公司0")])
        h._open_chat_by_search = lambda n, c, timeout=6.0: "name_only"
        caplog.set_level(logging.WARNING, logger="boss_bot.page_handler")
        assert h.enter_chat({"name": "李女士", "company": "重庆劳多拉企业管理"}) is False
        文 = self.采(caplog)
        assert any("name_only" in m or "同名" in m for m in 文), 文

    def test_混账那句已经从源码里没了(self):
        assert "侧栏滚到底、搜索也找不到会话" not in _page_handler源码()


def _补扫环(rows, convs):
    """只喂 `_backfill_pending_resumes` 需要的最小面，不碰浏览器。"""
    from boss_bot.main_loop import UnifiedBotLoop

    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp.logs = []
    lp._log = lambda level, msg: lp.logs.append(f"[{level}] {msg}")
    lp.account_index = 0
    lp._run_stale = lambda: False
    lp._resume_delivered = lambda *a, **k: False
    lp._msg_store = type("_S", (), {"get_all_chats_detail": lambda self: convs})()
    记 = []
    lp._chat_handler = type("_H", (), {
        "get_all_chats": lambda self: rows,
        "enter_chat": lambda self, row: 记.append(row) or False,
    })()
    lp.点开的行 = 记
    return lp


def 欠简历会话(name="沈女士", company="上海能良"):
    return {"chat_name": name, "job_name": "数据标注", "company": company,
            "account_index": 0,
            "messages": [{"text": "方便发一份简历吗", "is_mine": False}]}


class Test补扫也走同一份判据:
    """补发简历那条腿自己另写了一份认人规则（`len(命中)==1` 才敢认）。

    2026-10-18 11:09:19 线上：
        [主账号] 补扫欠简历的会话：1 个 —— 沈女士
        [主账号] 侧栏收集完成：527 个会话，其中置顶 1 个
        [主账号] ⏭️ 跳过 [沈女士]：侧栏按 姓名+公司 对不上这一行（存档公司='上海能良'），不猜人
    那份私有判据是"同名行前缀对得上、而且全屏只能有一行"才敢认，所以两行其实
    是同一个人（公司被 BOSS 截成两种写法）也会判成重名而跳过——下面第一条就是这个
    样例，跑出来的日志和线上那句一字不差。而 11:16:41 回复腿用新判据在同一条列表里
    跳格认出了 `[沈女士|上海能良]`，说明人对得上、公司是包含关系：她那单卡在的
    就是补扫这一路。旧那句只说"对不上"，看不出是"压根没这个人"还是"多个同名"，
    现在原因原样进日志。这一条跟侧栏探针、点击、核对 selected 那三处是同一件事，
    本轮既然把三份合成了一份，第四份没有理由留着。
    """

    def test_同名两行但公司是两种截断写法要认得出来(self):
        rows = [行("沈女士", "上海能良电子科技..."), 行("沈女士", "上海能良电子科技有限公司")]
        lp = _补扫环(rows, [欠简历会话()])
        lp._backfill_pending_resumes()
        assert len(lp.点开的行) == 1, f"同一个人被当成重名跳过了: {lp.logs}"

    def test_简称与全称对上(self):
        """实测那族：行里"厦门中洲联创科技"、存档"厦门中洲联创"。"""
        rows = [行("苏女士", "厦门中洲联创科技")]
        lp = _补扫环(rows, [欠简历会话("苏女士", "厦门中洲联创")])
        lp._backfill_pending_resumes()
        assert len(lp.点开的行) == 1, lp.logs

    def test_存档名字带空格也要认得出来(self):
        """补扫的名字来自盘上的存档，不是刚读回来的行——多一个空格就全程配不上。"""
        rows = [行("沈女士", "上海能良电子科技有限公司")]
        lp = _补扫环(rows, [欠简历会话("沈女士 ", "上海能良")])
        lp._backfill_pending_resumes()
        assert len(lp.点开的行) == 1, lp.logs

    def test_查无此人要说查无此人(self):
        lp = _补扫环([行("赵女士", "别家")], [欠简历会话()])
        lp._backfill_pending_resumes()
        assert lp.点开的行 == []
        assert any("no_name" in m for m in lp.logs), f"原因没落到日志: {lp.logs}"

    def test_公司是别家要把行里那家打出来(self):
        """只打存档公司看不出到底是哪儿错了——线上那句就只给了存档侧。"""
        rows = [行("沈女士", "途聚人力")]
        lp = _补扫环(rows, [欠简历会话()])
        lp._backfill_pending_resumes()
        assert lp.点开的行 == []
        assert any("途聚人力" in m for m in lp.logs), f"行里的公司没打出来: {lp.logs}"

    def test_判据只留一处(self):
        src = _main_loop源码()
        assert "def same(a, b):" not in src, "补扫里又留了一份私有判据"
        assert "认行" in src, "补扫没走 page_handler 那份唯一判据"
