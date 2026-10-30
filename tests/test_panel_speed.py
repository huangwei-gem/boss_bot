# -*- coding: utf-8 -*-
"""面板提速那三条出口的锁（2026-10-30）。

他原话「感觉现在很卡啊」。现测的账（同一台机器、线上面板 5001）：
- `curl /api/status` 稳定 **2.024 秒**，单独探那个没人应答的调试端口 **2.022 秒**，
  答话的 9223 只花 **6 毫秒** —— 整个接口的耗时就是那一次 urlopen；
- `/api/job_pool` 一次吐 **3151 行 / 2.9 MB**，首页 HTML **431 KB**，
  三千行铺进 DOM 之后每滚一下都要重排整表。

三条修法各钉一处，缺一条就会长成"测试过了但他还是卡"：
① 浏览器形态按端口缓存几秒，过期**先给旧的那份**、另起线程去问新的；
② 文本响应 gzip（文件下载、附件一律不压）；
③ 岗位池按页给（默认 200 行，limit=0 才是全量）。
"""
import base64
import gzip
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLASK_DIR = str(ROOT / "flask-version")
HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")


def _函数体(名):
    m = re.search(r"\nfunction " + re.escape(名) + r"\s*\(", HTML)
    assert m, "界面上已经没有 %s() 了" % 名
    i = HTML.index("{", m.start())
    depth = 0
    for j in range(i, len(HTML)):
        if HTML[j] == "{":
            depth += 1
        elif HTML[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML[i:j + 1]
    raise AssertionError("%s 的花括号没闭合" % 名)


@pytest.fixture
def 面板(tmp_path):
    if FLASK_DIR not in sys.path:
        sys.path.insert(0, FLASK_DIR)
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    with patch("boss_bot.job_pool.resolve_path", return_value=tmp_path):
        yield FLASK_APP


@pytest.fixture(autouse=True)
def _形态清场():
    """_形态缓存 是模块级的：不清干净，上一条留下的答案会让下一条一次都不问浏览器。"""
    if FLASK_DIR not in sys.path:
        sys.path.insert(0, FLASK_DIR)
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    FLASK_APP._形态缓存.clear()
    FLASK_APP._形态在刷.clear()
    原TTL = FLASK_APP._形态TTL秒
    yield FLASK_APP
    FLASK_APP._形态缓存.clear()
    FLASK_APP._形态在刷.clear()
    FLASK_APP._形态TTL秒 = 原TTL


def _塞(FLASK_APP, 号=0, n=25, 备注="", 地点=None):
    from boss_bot.job_pool import 岗位池
    池 = 岗位池(account_index=号)
    池.收([{"job_name": "数据分析师%d" % i,
            "url": "https://www.zhipin.com/job_detail/%d.html" % (i + 1),
            "company": "公司%d" % i, "salary": "8-10K", "note": 备注,
            "location": (地点(i) if 地点 else "长沙")} for i in range(n)])
    return 池


def _全量id(接):
    return [str(r["id"]) for r in (接.get("rows") or [])]


class 分页Test:
    def test_默认一页两百条_total仍是筛后的全量(self, 面板):
        """total 必须是"筛出来一共多少条"，不是"这次给了多少条"：
        界面上「筛出 N 条」和数据范围的角标都读它，读成页内行数
        就等于把两百五十条说成两百条。"""
        _塞(面板, n=250)
        d = 面板.app.test_client().get("/api/job_pool?account=0").get_json()
        assert d["status"] == "ok"
        assert d["total"] == 250, "total 被页内行数顶掉了"
        assert len(d["rows"]) == 200, "默认页不是 200 行"
        assert d["limit"] == 200 and d["offset"] == 0

    def test_limit等于零才是全量(self, 面板):
        _塞(面板, n=250)
        d = 面板.app.test_client().get("/api/job_pool?account=0&limit=0").get_json()
        assert len(d["rows"]) == 250 and d["total"] == 250

    def test_翻页拼起来等于全量不多不少不重(self, 面板):
        """翻页最容易坏的是"漏一条"和"重一条"：比首尾相接的结果，不数行数。"""
        池 = _塞(面板, n=25)
        c = 面板.app.test_client()
        全 = _全量id(c.get("/api/job_pool?account=0&limit=0").get_json())
        assert len(全) == len(池.列表()) == 25
        拼 = []
        for 偏 in (0, 10, 20):
            d = c.get("/api/job_pool?account=0&limit=10&offset=%d" % 偏).get_json()
            assert len(d["rows"]) == (10 if 偏 < 20 else 5), "最后一页形状不对：%s" % 偏
            assert d["offset"] == 偏
            拼 += _全量id(d)
        assert 拼 == 全, "三页拼起来和全量对不上（多、少，或顺序错位）"
        assert len(set(拼)) == len(拼), "有行跨页重复"

    def test_偏移超出总数回空表不报错(self, 面板):
        _塞(面板, n=3)
        r = 面板.app.test_client().get("/api/job_pool?account=0&limit=10&offset=999")
        assert r.status_code == 200
        d = r.get_json()
        assert d["rows"] == [] and d["total"] == 3, "越界要如实回空表，界面才有依据退回第一页"

    def test_读不懂的页参数当没给(self, 面板):
        """limit/offset 是地址栏和脚本里拼出来的：一个字母不该把整个接口顶成 500，
        顶红了界面上就是一句"岗位池读取失败"。"""
        _塞(面板, n=5)
        c = 面板.app.test_client()
        for 坏 in ("abc", "", "-1", "1e3"):
            r = c.get("/api/job_pool?account=0&limit=%s&offset=%s" % (坏, 坏))
            assert r.status_code == 200, "坏参数把接口顶红了：%s" % 坏
            assert r.get_json()["total"] == 5
        assert len(c.get("/api/job_pool?account=0&limit=abc").get_json()["rows"]) == 5

    def test_筛选与分页同一条请求里各管各的(self, 面板):
        """先筛后切页：total 是筛后的条数，页从筛后那份里切。
        反过来（先切页再筛）会筛出用户根本没在那一页见过的行。"""
        from boss_bot.job_pool import 岗位池
        岗位池(account_index=0).收(
            [{"job_name": "数据分析师%d" % i,
              "url": "https://www.zhipin.com/job_detail/%d.html" % (i + 1),
              "company": "公司%d" % i} for i in range(18)]
            + [{"job_name": "普工%d" % i,
                "url": "https://www.zhipin.com/job_detail/p%d.html" % i,
                "company": "厂%d" % i} for i in range(4)])
        c = 面板.app.test_client()
        筛全 = c.get("/api/job_pool?account=0&q=数据分析师&limit=0").get_json()
        assert 筛全["total"] == 18 and len(筛全["rows"]) == 18
        d = c.get("/api/job_pool?account=0&q=数据分析师&limit=10&offset=10").get_json()
        assert d["total"] == 18 and len(d["rows"]) == 8
        assert _全量id(d) == _全量id(筛全)[10:18], "页不是从筛后那份里切的"
        assert all("普工" not in r["job_name"] for r in d["rows"]), "切页把筛掉的普工放回来了"

    def test_合并视图也按页给(self, 面板):
        _塞(面板, 号=0, n=120)
        _塞(面板, 号=1, n=100)
        with patch.object(面板, "_ensure_config") as 配:
            配.return_value.greet.accounts = [{}, {}]
            c = 面板.app.test_client()
            d = c.get("/api/job_pool?account=all").get_json()
            assert d["total"] == 220 and len(d["rows"]) == 200
            全 = c.get("/api/job_pool?account=all&limit=0").get_json()
            assert len(全["rows"]) == 220
            assert {r.get("account_index") for r in 全["rows"]} == {0, 1}

    def test_城市下拉与状态计数不受分页影响(self, 面板):
        """这两个数他拿去决定"池里到底有哪些城市"：按当前这页算，
        第 201 条之后才出现的城市就从下拉里消失了——筛不到、也说不清池里有没有。"""
        _塞(面板, n=210, 地点=lambda i: "上海" if i >= 200 else "长沙")
        d = 面板.app.test_client().get("/api/job_pool?account=0").get_json()
        assert len(d["rows"]) == 200, "样例没跨页，这条等于没测：%s 行" % len(d["rows"])
        assert not any(r["location"] == "上海" for r in d["rows"]), "上海那条就在第一页，筛不到也看不见"
        城市 = {c.get("city"): c.get("count") for c in (d["cities"] or [])}
        assert 城市.get("长沙") == 200 and 城市.get("上海") == 10, \
            "城市清单只数了这一页（上海 10 条从下拉里消失）：%s" % 城市
        assert d["stats"]["总数"] == 210, "状态计数按页算了"

    def test_分页读不动盘上的数据(self, 面板):
        池 = _塞(面板, n=230)
        before = json.dumps(池.列表(), sort_keys=True, ensure_ascii=False)
        c = 面板.app.test_client()
        for 偏 in (0, 100, 200):
            c.get("/api/job_pool?account=0&limit=100&offset=%d" % 偏)
        assert json.dumps(池.列表(), sort_keys=True, ensure_ascii=False) == before, \
            "读接口把池改了"


class 压缩Test:
    def _大响应(self, 面板, 头=None):
        _塞(面板, n=30, 备注="岗位备注岗位备注岗位备注")
        return 面板.app.test_client().get("/api/job_pool?account=0",
                                         headers=头 or {"Accept-Encoding": "gzip"})

    def test_文本响应压完确实小了也读得回来(self, 面板):
        r = self._大响应(面板)
        原文 = gzip.decompress(r.data)
        assert len(原文) >= 2048, "样例没到压缩门槛，这条等于没测：%d 字节" % len(原文)
        assert r.headers.get("Content-Encoding") == "gzip", "没压：面板还是要吐几兆"
        assert int(r.headers["Content-Length"]) == len(r.data), "Content-Length 没跟着改"
        assert len(r.data) < len(原文), "压了个寂寞（比原文还长就该发原文）"
        assert r.headers.get("Vary") == "Accept-Encoding", "不写 Vary，缓存会串供"
        回 = json.loads(原文.decode("utf-8"))
        assert 回["status"] == "ok" and 回["total"] == 30

    def test_浏览器不谈gzip就原样发(self, 面板):
        r = self._大响应(面板, 头={"Accept-Encoding": "identity"})
        assert "Content-Encoding" not in r.headers, "对方没要 gzip 也硬压，收端解不出来"
        assert r.get_json()["total"] == 30

    def test_没到门槛的小响应不压(self, 面板):
        c = 面板.app.test_client()
        c.get("/api/job_pool?account=1")     # 先把那份空池文件建出来
        r = c.get("/api/job_pool?account=1", headers={"Accept-Encoding": "gzip"})
        assert len(r.get_data()) < 2048, "样例太大，这条等于没测门槛：%d 字节" % len(r.get_data())
        assert "Content-Encoding" not in r.headers

    def test_附件类响应不许压(self, 面板):
        """导出 Excel 那六个口是 send_file 发的、支持 Range 的部分请求。gzip 之后再按
        字节范围切就对不上——压坏了不报错，只会拿到一份打不开的文件。"""
        with 面板.app.test_request_context("/", headers={"Accept-Encoding": "gzip"}):
            正文 = '{"x":"' + "y" * 8000 + '"}'

            带附件 = 面板.app.make_response(正文)
            带附件.headers["Content-Type"] = "application/json"
            带附件.headers["Content-Disposition"] = 'attachment; filename="台账.xlsx"'
            assert "Content-Encoding" not in 面板._压文本响应(带附件).headers, "附件也被压了"

            # 同一份正文、只把 Content-Disposition 拿掉：必须压上——
            # 这才证明拦住上面那条的是这一道闸，不是别的东西
            纯文本 = 面板.app.make_response(正文)
            纯文本.headers["Content-Type"] = "application/json"
            assert 面板._压文本响应(纯文本).headers.get("Content-Encoding") == "gzip", \
                "正例压不上，上面那条负例就是假通过"

    def test_压不动就发原文(self, 面板):
        """随机内容压完反而更长（base64 那种）：这时候发原文，
        不能为了"都压上了"把响应变大。"""
        正文 = json.dumps({"r": base64.b64encode(os.urandom(4000)).decode()})
        原文 = 正文.encode("utf-8")
        assert len(原文) >= 2048
        with 面板.app.test_request_context("/", headers={"Accept-Encoding": "gzip"}):
            响 = 面板.app.make_response(原文)
            响.headers["Content-Type"] = "application/json"
            with patch.object(面板.gzip, "compress", lambda *a, **k: b"z" * (len(原文) + 10)):
                出 = 面板._压文本响应(响)
            assert "Content-Encoding" not in 出.headers, "压不动还硬标 gzip"
            assert 出.get_data() == 原文, "压不动时应把原文一字不改发出去"


class 状态缓存Test:
    def _桩(self, 次, 睡=0.0, 坏=False):
        def 假(端口):
            次.append(端口)
            if 睡:
                time.sleep(睡)
            if 坏:
                raise RuntimeError("端口没应答")
            return {"running": len(次) % 2 == 0, "headless": None, "port": 端口,
                    "browser": "Chrome/1", "依据": "桩", "与启动记录不符": False,
                    "本项目": True}
        return 假

    def test_几秒内重复问只问一次(self, 面板):
        次 = []
        with patch.object(面板, "browser_mode", side_effect=self._桩(次)):
            a = 面板._浏览器形态(9222)
            b = 面板._浏览器形态(9222)
            c = 面板._浏览器形态(9222)
        assert len(次) == 1, "点了三次开关就问了三台浏览器：那两秒又回来了"
        assert a == b == c

    def test_过期先给旧的不等浏览器(self, 面板):
        """这一条就是 /api/status 那 2.024 秒的解药：请求线程一次都不等浏览器。"""
        次 = []
        端口 = 9222
        with patch.object(面板, "browser_mode", side_effect=self._桩(次, 睡=0.4)):
            面板._形态TTL秒 = 0
            面板._浏览器形态(端口)                    # 冷启动：没有旧答案，只能自己等
            assert len(次) == 1
            起点 = time.time()
            旧 = 面板._浏览器形态(端口)                # 过期：给旧的，另起线程去问
            耗时 = time.time() - 起点
            assert 耗时 < 0.2, "过期这一问还是自己在等浏览器：%.3f 秒" % 耗时
            assert 旧["依据"] == "桩"
            等完 = time.time() + 3
            while time.time() < 等完:
                with 面板._形态锁:
                    收工 = len(次) >= 2 and 端口 not in 面板._形态在刷
                if 收工:
                    break
                time.sleep(0.02)
            assert len(次) == 2, "后台那一问根本没发生"
        assert 端口 not in 面板._形态在刷, "刷新中的记号漏了，之后这个端口再也不问新的"

    def test_缓存里那份换过端口各算各的(self, 面板):
        """两个号两个调试端口：共用一格答案，账号2 的状态点会跟着账号1 变。"""
        次 = []
        with patch.object(面板, "browser_mode", side_effect=self._桩(次)):
            a = 面板._浏览器形态(9222)
            b = 面板._浏览器形态(9223)
        assert a["port"] == 9222 and b["port"] == 9223, "缓存串了端口"
        assert 次 == [9222, 9223]

    def test_面板刚起来那一次自己等(self, 面板):
        次 = []
        with patch.object(面板, "browser_mode", side_effect=self._桩(次)):
            assert 面板._浏览器形态(9229)["port"] == 9229
        assert 次 == [9229]

    def test_问坏了不留脏答案也不锁死下次(self, 面板):
        次 = []
        with patch.object(面板, "browser_mode", side_effect=self._桩(次, 坏=True)):
            with pytest.raises(RuntimeError):
                面板._浏览器形态(9230)                 # 冷启动问坏了：照实抛，不编答案
            面板._刷形态(9230)                         # 后台问坏了：不许把异常抛到线程外
        assert 9230 not in 面板._形态缓存, "问坏了还留了个答案在缓存里"
        assert 面板._形态在刷 == set(), "坏一次就把这个端口锁死了"

    def test_enrich_status走的是缓存这条路(self, 面板):
        """接口自己还去调 browser_mode 的话，缓存等于装了个没人用的摆设。"""
        名 = set(面板._enrich_status.__code__.co_names)
        assert "_浏览器形态" in 名, "状态接口没接进缓存：%s" % sorted(名)
        assert "browser_mode" not in 名, "还在请求线程里直接问浏览器"


class 界面分页Test:
    def test_分页条与状态回填都在(self):
        assert 'id="poolPager"' in HTML and 'id="poolPagerText"' in HTML
        assert "pool页翻(-1)" in HTML and "pool页翻(1)" in HTML
        体 = _函数体("loadJobPool")
        assert "POOL页.总数 = Number(data.total)" in 体, "没回填总数，分页条永远不出现"
        assert "POOL页.偏移 = Number(data.offset)" in 体, "20 秒轮询重拉后会跳回第一页"
        assert "pool画分页();" in 体, "数据到了不画分页条"
        assert "pool页Qs()" in 体, "请求里没带 limit/offset，服务端还是吐全量"

    def test_换筛选与重置都回第一页(self):
        assert "POOL页.偏移 = 0" in _函数体("applyPoolFilter"), "换了条件还停在第 5 页"
        assert "POOL页.偏移 = 0" in _函数体("resetPoolFilter"), "重置没回第一页"

    def test_翻到没有的那一页要退回第一页(self):
        """删空当前页、或筛窄到不足三页：这一页一条没有但池里有货，
        这时候照下面那句「没有符合当前筛选的岗位」就是假话。"""
        体 = _函数体("loadJobPool")
        assert "POOL页.偏移 > 0" in 体, "空页没考虑是因为停在后面的页"
        assert "poolPendingRefresh = true" in 体, "退页只是记号，没打算重拉"
        assert 体.index("POOL页.偏移 > 0") < 体.index("renderJobPool("), \
            "退页排在画表之后：先白画一次空表，红字提示都已经上屏了"

    def test_表清空时分页条跟着收(self):
        体 = _函数体("poolShowNotice")
        assert "pool画分页();" in 体 and "POOL页.总数 = 0" in 体, \
            "表都清空了还挂着「第 1–200 条 / 共 3151 条」"

    def test_表头全选说的是本页(self):
        """分页以后那一勾只管这一页：话要说成「本页」，
        说「当前筛出的」会让他以为一次勾走了筛出来的 3151 条。"""
        assert HTML.count('aria-label="全选本页筛出的岗位"') == 1
        assert 'aria-label="全选当前筛出的岗位"' not in HTML, "旧措辞还在，勾的却是本页"
        体 = _函数体("poolSyncSelection")
        assert "本页这" in 体 and "筛出 " in 体, "全选框的提示没交代还有多少条在别的页"
        assert "本页勾了" in 体, "已勾条数没分开说本页勾了几条"

    def test_分页参数不许混进筛选那串(self):
        """poolFilterQs 的参数集合要和表头那几格逐格对得上（门在
        tests/test_job_pool_filters.py）——limit/offset 混进去等于多两格没人筛的框。"""
        筛 = _函数体("poolFilterQs")
        assert "limit=" not in 筛 and "offset=" not in 筛
        页 = _函数体("pool页Qs")
        assert "limit=" in 页 and "offset=" in 页


class 别的出口Test:
    def test_仓库里没有不带limit的读池出口(self):
        """默认一页 200 条这件事，最容易坏的不是面板，是"顺手 GET 一下想拿全量"的脚本：
        少读两千九百条不报错，只是数字小。生产代码与工具一律显式带 limit
        （tests/ 除外——那边的池是几条几条造的，那条套件自己钉自己的形状）。"""
        列 = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True)
        assert 列.returncode == 0, "列不出仓库里的文件，这条门等于没跑"
        文件s = [p for p in 列.stdout.decode("utf-8").split("\0")
                if p.endswith(".py") and not p.startswith("tests/")
                and "venv" not in p.replace("\\", "/").split("/")]
        assert 文件s, "git ls-files 什么都没列出来"
        没带 = []
        for p in 文件s:
            try:
                文 = (ROOT / p).read_text(encoding="utf-8")
            except Exception:
                continue
            for 行 in 文.splitlines():
                if "/api/job_pool" in 行 and ("api_get(" in 行 or ".get(" in 行
                                              or "requests.get" in 行):
                    if "limit" not in 行:
                        没带.append("%s：%s" % (p, 行.strip()[:90]))
        assert not 没带, "这些读岗位池的出口没带 limit，会被默认一页 200 条静默截断：\n" + \
            "\n".join(没带)
