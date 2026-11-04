# -*- coding: utf-8 -*-
"""2026-11-04 收尾四项优化的行为锁。

审计出来的四件事都是数出来的，不是感觉：

① 「获取全部会话列表失败: PageDisconnectedError」今天一天 1908 行 ERROR（logs 现数），
   把「谁把投递停了」那种真要看的行全埋了；
② 「/api/chats」一次吐 13,072,236 字节 / 现测 9.70 秒，而面板一个字节都不请求它
   （聊天正文走 /api/chats/<会话名>，点开哪一路取哪一路）——它纯粹是给脚本用的，
   而「顺手 GET 一下想拿全量」的脚本少读两千八百条不报错，只是数字小；
③ 「联系与简历」「岗位候选池」两个 tab 开机印 0，而接口此刻分别有 543 行和 835 行。
   那是「还没点开取过」，不是「没有」；
④ 池表 835 行只能按下拉那两个预设排，表头点不动，读屏也说不出当前按哪列、升还是降。

每条都配反向哨兵：判据拆掉要红，写过头（把另一个号一起静音、把老地址写死）也要红。
"""
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "flask-version"))

from boss_bot import page_handler as PH  # noqa: E402
from boss_bot.job_pool import 可排序列, 排序行  # noqa: E402

HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")


# ──  同一族失败只报第一声 ─────────────────────────────

def _桩handler(账号="主账号", 抛=None, 读数=None):
    h = PH.BossChatHandler.__new__(PH.BossChatHandler)   # 线上那些测试也这么造
    h._account_id = 账号
    h._browser_manager = None

    class _页:
        """只把"读侧栏列表"那一次调用打坏。

        get_all_chats 的归零/滚动两次 run_js 在 try 外面（它们坏了不该走这条收敛），
        真正被 catch 的是读列表那一次——脚本里有 `rows.push`。桩必须照这个形状来，
        不然测的是"滚动调用抛异常"，跟要收敛的那 1908 行不是同一件事。
        """
        url = "https://www.zhipin.com/web/geek/chat"
        def get(self, *a, **k):
            return None
        def run_js(self, script="", *a, **k):
            if "rows.push" in str(script):
                if 抛 is not None:
                    raise 抛
                return json.dumps(读数 if 读数 is not None else [])
            return "0/700"
    h.page = _页()
    h._登记行位置 = lambda rows: None
    return h


@pytest.fixture(autouse=True)
def _不真等():
    """这一族一轮就 50 次，get_all_chats 里每轮都要 sleep——不掐掉的话这一份测试要跑两分钟。
    测试等的是"叫了几次"，不是"墙上几点"。"""
    with patch("boss_bot.page_handler.time.sleep", lambda *a, **k: None):
        yield


@pytest.fixture(autouse=True)
def _清台账():
    PH._会话列表坏过的族.clear()
    yield
    PH._会话列表坏过的族.clear()


def _报了的(caplog):
    return [r for r in caplog.records if "会话列表失败" in r.message]


class Test会话列表失败不再刷屏:
    def test_同一族只报第一次(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="boss_bot.page_handler"):
            h = _桩handler(抛=RuntimeError("与页面的连接已断开"))
            for _ in range(50):
                h.get_all_chats(max_rounds=1)
        错 = [r for r in _报了的(caplog) if r.levelname == "ERROR"]
        沉 = [r for r in _报了的(caplog) if r.levelname == "DEBUG"]
        assert len(错) == 1, "50 轮刷了 %d 行 ERROR，收敛没生效" % len(错)
        assert len(沉) == 49, "后面那些不许静默吞掉——转 DEBUG 是留着能捞出来查"

    def test_读到过一次就重新计(self, caplog):
        """坏→好→坏 必须还能报出第二声，否则「好了又坏」被永久静音。"""
        with caplog.at_level(logging.ERROR, logger="boss_bot.page_handler"):
            坏 = _桩handler(抛=RuntimeError("断开"))
            坏.get_all_chats(max_rounds=1)
            坏.get_all_chats(max_rounds=1)
            好 = _桩handler(读数=[{"name": "张女士", "company": "某公司"}])
            好.get_all_chats(max_rounds=1)
            坏.get_all_chats(max_rounds=1)
        assert len(_报了的(caplog)) == 2, [r.message[:40] for r in _报了的(caplog)]

    def test_两个号互不静音(self, caplog):
        """台账按账号分一份：主号安静了不许把账号2 一起闭嘴。"""
        with caplog.at_level(logging.ERROR, logger="boss_bot.page_handler"):
            a = _桩handler(账号="主账号", 抛=RuntimeError("断开"))
            b = _桩handler(账号="账号2", 抛=RuntimeError("断开"))
            a.get_all_chats(max_rounds=1)
            b.get_all_chats(max_rounds=1)
        assert len(_报了的(caplog)) == 2

    def test_不同族各报各的(self, caplog):
        class 别的错(Exception):
            pass

        with caplog.at_level(logging.ERROR, logger="boss_bot.page_handler"):
            h = _桩handler(抛=RuntimeError("断开"))
            h.get_all_chats(max_rounds=1)

            def 换一种坏(script="", *a, **k):
                if "rows.push" in str(script):
                    raise 别的错("另一种坏")
                return "0/700"
            h.page.run_js = 换一种坏
            h.get_all_chats(max_rounds=1)
        assert len(_报了的(caplog)) == 2, "成因不同就是两件事，不能拿「同一族」把第二种坏藏起来"

    def test_台账住模块级不挂实例属性(self):
        """挂实例属性会被那些拿 __new__ 造 handler 的测试打空（今天真被打红过一次）。"""
        assert isinstance(PH._会话列表坏过的族, dict)
        h = _桩handler()
        assert not hasattr(h, "_会话列表坏过")
        assert h._会话台账键() == "主账号"

    def test_拿不到账号时退回端口或全局(self):
        h = _桩handler(账号=None)
        assert h._会话台账键() == "全局"


# ──  /api/chats 默认只给一页 ─────────────────────────

@pytest.fixture()
def 面板(tmp_path):
    if str(ROOT / "flask-version") not in sys.path:
        sys.path.insert(0, str(ROOT / "flask-version"))
    with patch("app.UnifiedBotLoop"):
        import app as A
    A.app.config["TESTING"] = True
    return A.app.test_client()


def _假会话(n):
    return [{"chat_name": "会话%03d" % i, "chat_id": "会话%03d#公司%d" % (i, i % 3),
             "company": "公司%d" % (i % 3), "job_name": "岗位%d" % i,
             "account_index": i % 2, "updated_at": i, "message_count": 3,
             "unread_count": 0, "last_message": "你好", "last_time": "昨天",
             "messages": [{"text": "你好", "direction": "in", "mid": str(1000 + i)}]}
            for i in range(n)]


@pytest.fixture()
def 灌好的面板(面板):
    全 = _假会话(12)
    class _假存:
        def __init__(self, *a, **k):
            pass
        def get_all_chats_detail(self):
            return 全
    with patch("app.MessageStore", _假存):
        yield 面板


class Test会话全量不再一口吐完:
    def test_默认一页而不是全量(self, 灌好的面板):
        d = 灌好的面板.get("/api/chats?limit=5").get_json()
        assert d["status"] == "ok"
        assert len(d["chats"]) == 5, "给了 limit=5 就该只回 5 路"
        assert d["total"] >= 12, "total 必须是全量——不然分页条上就是个假数字"
        assert d["limit"] == 5 and d["offset"] == 0

    def test_要全量必须显式limit0(self, 灌好的面板):
        全 = 灌好的面板.get("/api/chats?limit=0").get_json()
        页 = 灌好的面板.get("/api/chats").get_json()
        assert 全["total"] == 全["returned"], "limit=0 才给全量"
        assert 页["limit"] == 50, "默认那一页是 50 路：静默截断比报错更难查"

    def test_坏参数不把接口顶成500(self, 灌好的面板):
        for 串 in ["?limit=很高", "?offset=负数", "?limit=-1"]:
            d = 灌好的面板.get("/api/chats" + 串).get_json()
            assert d["status"] == "ok", 串

    def test_翻页翻得完(self, 灌好的面板):
        收 = set()
        偏 = 0
        while True:
            d = 灌好的面板.get("/api/chats?limit=5&offset=%d" % 偏).get_json()
            收.update(c["chat_name"] for c in d["chats"])
            if len(d["chats"]) < 5:
                break
            偏 += 5
            assert 偏 < 200, "翻不完就是死循环"
        assert len(收) >= 12, "翻完一遍该把 12 路都收到，实际 %d" % len(收)

    def test_仓库里没有不带limit的读会话出口(self):
        """这条跟岗位池那一族同一个道理：脚本少读两千八百条不报错，只是数字小。"""
        列 = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True)
        assert 列.returncode == 0, "列不出仓库里的文件，这条门等于没跑"
        没带 = []
        for f in [p for p in 列.stdout.decode("utf-8").split("\0")
                  if p.endswith(".py") and not p.startswith("tests/")]:
            try:
                文 = (ROOT / f).read_text(encoding="utf-8")
            except Exception:
                continue
            for 行 in 文.splitlines():
                if "/api/chats" not in 行 or "mark_read" in 行:
                    continue
                尾 = 行.split("/api/chats", 1)[1][:3]
                if "<" in 尾:            # /api/chats/<会话名> 那一路是单会话，本来就该整份给
                    continue
                if (".get(" in 行 or "requests.get" in 行) and "limit" not in 行:
                    没带.append("%s：%s" % (f, 行.strip()[:90]))
        assert not 没带, "这些读会话全量的出口没带 limit：\n" + "\n".join(没带)


# ──  没取过的 tab 不许说 0 ───────────────────────────

class Test未加载的tab不说零:
    @pytest.mark.parametrize("id_", ["contactTabCount", "poolTabCount"])
    def test_初始是破折号不是0(self, id_):
        m = re.search(r'id="%s"[^>]*>([^<]*)</span>' % id_, HTML)
        assert m, "%s 这个 tab 计数在界面上找不到了" % id_
        assert m.group(1).strip() == "—", \
            "%s 开机印 %r——台账/池里明明有行，那是「还没点开取过」，不是「没有」" % (id_, m.group(1))
        assert "还没打开" in m.group(0), "title 里得说清为什么没数"

    def test_取到之后照样换成真数字(self):
        """破折号不能变成「永远不更新」：两个 loader 都得把条数写上去。"""
        for 变量 in ["contactLedgerRows.length", "Number(s['总数'])"]:
            assert 变量 in HTML, "%s 那条赋值没了——tab 上的数就再也不动了" % 变量


# ──  表头点着排 ──────────────────────────────────────

def _行(名, 司, 城, 薪, 分, 抓):
    return {"job_name": 名, "company": 司, "city": 城, "salary": 薪,
            "ai": {"分": 分}, "captured_at": 抓, "last_seen_at": 抓}


class Test表头排序:
    行 = staticmethod(_行)
    样 = [_行("jb", "B", "上海", "20K", 90, 3),
          _行("ka", "A", "长沙", "8K", 50, 1),
          _行("ic", "C", "北京", "35K", 70, 2)]

    def test_六列都能排两个方向(self):
        for 列 in ["岗位", "公司", "城市", "薪资", "AI分", "抓到"]:
            降 = [r["job_name"] for r in 排序行(list(self.样), 列 + ":desc")]
            升 = [r["job_name"] for r in 排序行(list(self.样), 列 + ":asc")]
            assert 降 != 升, "%s 两个方向排出来一模一样，等于没排" % 列
            assert sorted(降) == sorted(升) == ["ic", "jb", "ka"], "%s 排丢了行" % 列

    def test_薪资按折月比不按字面(self):
        样 = [self.行("hi", "X", "长沙", "3-5万", 0, 1),
              self.行("lo", "Y", "长沙", "8-10K", 0, 2)]
        assert [r["job_name"] for r in 排序行(list(样), "薪资:desc")] == ["hi", "lo"]

    def test_读不出的薪资排最后(self):
        样 = [self.行("neg", "X", "长沙", "面议", 0, 1),
              self.行("num", "Y", "长沙", "9K", 0, 2)]
        assert 排序行(list(样), "薪资:desc")[0]["job_name"] == "num"

    def test_老写法照旧能用(self):
        """下拉那两项和已发出去的地址都还依赖 分降/新抓的在前，不能因为加了点排就坏。"""
        样 = [self.行("za", "X", "长沙", "9K", 10, 1), self.行("zb", "Y", "长沙", "9K", 80, 5)]
        assert 排序行(list(样), "分降")[0]["job_name"] == "zb"
        assert 排序行(list(样), "新抓的在前")[0]["job_name"] == "zb"
        assert 排序行(list(样), "")[0]["job_name"] == "zb", "空值要退回默认，不能不排"

    def test_不认识的列退回默认而不是排成没排(self):
        样 = [self.行("za", "X", "长沙", "9K", 10, 1), self.行("zb", "Y", "长沙", "9K", 80, 5)]
        assert 排序行(list(样), "密码:asc")[0]["job_name"] == "zb"
        assert 排序行(list(样), "备注:desc")[0]["job_name"] == "zb"

    def test_界面上可点的列等于后端能排的列(self):
        """两处各写一套就会出现「点了没反应」——这项目的老毛病（每个控件都要真生效）。"""
        界面 = re.findall(r'<th scope="col"[^>]*class="pool-sortable" data-col="([^"]+)"', HTML)
        assert set(界面) == set(可排序列) - {"见过"}, \
            "界面 %s vs 后端 %s" % (界面, list(可排序列))
        assert len(界面) == 6
        for 列 in 界面:
            assert "poolSortBy('%s')" % 列 in HTML, "%s 那列没接上点击" % 列

    def test_th里放的是真按钮不是挂在th上的onclick(self):
        """挂 onclick 在 th 上，键盘 Tab 不到、读屏也念不出「这是可排序的列」。"""
        assert 'class="pool-sort-btn"' in HTML
        assert 'type="button"' in HTML
        assert "function pool表头排序态" in HTML
        assert re.search(r'th\[aria-sort="descending"\]', HTML), "降序要有形状（箭头翻转），不只换个颜色"

    def test_下拉与表头只许一个说话(self):
        """两个控件各存一份排序状态，就会出现屏幕按薪资排、请求发分降。"""
        assert "POOL排.列=''" in HTML, "动下拉时没把话语权交回去"
        assert "function pool排参数" in HTML and "pool排参数()" in HTML

    def test_换列时回到第一页(self):
        """停在第三页换排序，那一页的内容跟页码对不上——分页那族已经栽过一次。"""
        体 = HTML[HTML.index("function poolSortBy"):HTML.index("function pool排参数")]
        assert "POOL页.偏移 = 0" in 体

    def test_后端接口真吃这个sort参数(self):
        assert 'request.args.get("sort"' in APP, "sort 参数没接" 


# ── 收尾：不许留下死代码与旧写法 ──────────────────────

class Test收尾不留下死代码:
    def test_没有摆着不用的状态类(self):
        """上一轮我加过 .ui-skel/.ui-state，一查四个视图各有空态，就拆了。
        这条门钉住「别为了好看摆一套没人用的规则」。"""
        for 死 in [".ui-skel", ".ui-state", ".ui-busy", ".pool-skel-row"]:
            assert 死 not in HTML, "%s 又摆回来了：界面上没有哪个元素用它" % 死

    def test_字号全走阶梯(self):
        assert not re.findall(r'font-size\s*:\s*[\d.]+px', HTML), "又写回字面 px 了"
        assert not re.findall(r'font-size\s*:\s*--fs-', HTML), \
            "裸变量名是非法声明，浏览器整条丢弃——字阶会静默失效"

    def test_表头标签都合法闭合(self):
        assert not re.findall(r'<th(?![^>]*>)', HTML), "有 <th> 缺闭合 >"
        assert HTML.count("<thead>") == HTML.count("</thead>")
        assert "</th></th>" not in HTML
