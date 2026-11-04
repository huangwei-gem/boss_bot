# -*- coding: utf-8 -*-
"""岗位池表头下面那一行逐列筛选（2026-10-29 他截图问「筛选这里不能加吗？」）。

这里钉两件要紧事：

① **每一格只筛自己那一列**。最容易做错成「往每一列里都搜一遍」：他要的是「备注那格
   填了字就只看备注」，所以每一列的门都配一条「这一格不该命中别列的字」的反向断言。

② **界面那一格、HTTP 那个参数名、后端那个形参，三处逐格对上**。这是他撞过好几轮的
   那类毛病（README「每个输入框都要真实生效」）：加了个框、后端读都不读；或者反过来
   留一个界面上根本没有入口的死参数。三处各有一份清单，比对才作数。

判据只有 `job_pool.筛行` 一份：单号列表和「全部账号」的合并视图都走它。
"""
import copy
import inspect
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from boss_bot.job_pool import AI匹配数, 筛行, 薪资折月

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")

# 「文档指纹」不算表头的一格：它是「现在按哪份要求判的」这个上下文，不是哪一列。
# 「自动线」「待看线」同理：那是他此刻把自动投/待你看这两条线划在哪（来自配置），
# 界面上是下拉里两档的**名字**跟着它变，不是多摆一个输入框。
_不是格子 = {"行s", "文档指纹", "自动线", "待看线"}


def _函数体(名, 要整段=False):
    """按大括号配对切出 index.html 里那个 function 的函数体"""
    m = re.search(r"\nfunction " + re.escape(名) + r"\s*\(", HTML)
    assert m, "界面上没有 %s() 了" % 名
    起点 = HTML.index("{", m.start())
    深 = 0
    for i in range(起点, len(HTML)):
        深 += HTML[i] == "{"
        深 -= HTML[i] == "}"
        if 深 == 0:
            return HTML[m.start():i + 1] if 要整段 else HTML[起点:i + 1]
    raise AssertionError("%s 的大括号没配对" % 名)


def _页声明():
    """`var POOL页 = {...}`：node 里跑 resetPoolFilter 得带上它——重置现在还要
    把"停在第几页"归零，少了这条声明就是 ReferenceError，而不是"没归零"。"""
    m = re.search(r"var POOL页 = \{[^}]*\};", HTML)
    assert m, "界面上没有 POOL页 那条声明（分页状态搬家了，node 那套要跟着改）"
    return m.group(0)


def _界面表():
    """POOL_FILTERS 那张表：界面每一格 ↔ 它发出去的那个 query 参数名"""
    m = re.search(r"var POOL_FILTERS = \[(.*?)\n\];", HTML, re.S)
    assert m, "index.html 里没有 POOL_FILTERS 那张表了"
    对 = re.findall(r"\{id: '([^']+)', qs: '([^']+)'\}", m.group(1))
    assert 对, "POOL_FILTERS 一格都没解析出来（形状变了，门要跟着改）"
    return 对


def _http表():
    """app.py 里那张「query 参数名 → 筛行形参」的对照表"""
    m = re.search(r"_池筛参数 = \((.*?)\)\n", APP, re.S)
    assert m, "app.py 里没有 _池筛参数 那张表了"
    对 = re.findall(r'\("([a-z_]+)", "([^"]+)"\)', m.group(1))
    assert 对, "_池筛参数 一格都没解析出来"
    return 对


def _后端形参():
    return [p for p in inspect.signature(筛行).parameters if p not in _不是格子]


def _筛行函数体():
    """筛行 源码里文档串之后的那截——签名里写着形参名不算「用上了」"""
    源 = inspect.getsource(筛行)
    头 = 源.index('"""')
    末 = 源.index('"""', 头 + 3) + 3
    return 源[末:]


def _筛选行控件():
    """表头下面那一行里真摆出来的控件 id（纯展示的图标格没有 id，不算）"""
    块 = re.search(r'<tr class="pool-filter-row">(.*?)</tr></thead>', HTML, re.S)
    assert 块, "岗位池表头里没有那一行逐列筛选了"
    return re.findall(r'id="(poolFilter[A-Za-z]+)"', 块.group(1))


# ── 当样本的一池子行：每一列都故意长得不一样 ──
现在 = time.time()
全部id = {"1", "2", "3", "4", "5"}


def _行():
    return [
        {"id": "1", "job_name": "数据标注员", "company": "长沙云标注",
         "city": "长沙·岳麓区", "salary": "15-20K", "note": "问过了没回",
         "status": "new",
         "ai": {"分": 95, "合": True, "理由": "命中数据分析方向", "doc": "h1",
                "判于": 现在},
         "gate": {"方向命中": True}, "captured_at": 现在},
        {"id": "2", "job_name": "仓库管理员", "company": "某某物流",
         "city": "上海·浦东", "salary": "31-36元/时", "note": "",
         "status": "applied",
         "ai": {"分": 10, "合": False, "理由": "命中否决词「坐班」", "doc": "h1",
                "判于": 现在},
         "gate": {"方向命中": False}, "captured_at": 现在 - 3 * 86400},
        {"id": "3", "job_name": "数据分析（可坐班）", "company": "云裁数据",
         "city": "长沙·天心区", "salary": "面议", "note": "这家是猎头",
         "status": "selected", "ai": {},
         "gate": {"方向命中": True, "否决命中": "坐班", "要人看一眼": True},
         "captured_at": 现在 - 10 * 86400},
        {"id": "4", "job_name": "AI训练助教", "company": "某某教育",
         "city": "长沙", "salary": "211-221元/天", "note": "",
         "status": "rejected", "ai": {},
         "gate": {"方向命中": True, "否决命中": "助教"},
         "captured_at": 现在 - 3600},
        {"id": "5", "job_name": "内容审核", "company": "某网安",
         "city": "上海", "salary": "4111-4611元/月", "note": "",
         "status": "new", "ai": {}, "gate": {"词表没读到": True},
         "captured_at": 现在},
    ]


def _挑(**筛):
    筛.setdefault("文档指纹", "h1")
    return {r["id"] for r in 筛行(_行(), **筛)}


class 界面后端与判据三处逐格对得上Test:
    def test_界面每一格都发得出参数(self):
        界 = {qs for _, qs in _界面表()}
        接 = {qs for qs, _ in _http表()}
        assert 界 == 接, "界面有格但 HTTP 那边不读（或反过来）：差集 %s" % (界 ^ 接)

    def test_参数名与后端形参逐格对上(self):
        接 = {中 for _, 中 in _http表()}
        后 = set(_后端形参())
        assert 接 == 后, "读到的参数没变成 筛行 的形参（或有个形参没人喂）：差集 %s" % (接 ^ 后)

    def test_表头那一行的控件都在这张表里(self):
        """加一格忘了写进 POOL_FILTERS：框在屏上、填了字表一动不动"""
        表 = {i for i, _ in _界面表()}
        没接线 = [i for i in _筛选行控件() if i not in 表]
        assert not 没接线, "表头这些控件没进 POOL_FILTERS，等于摆了个假框：%s" % 没接线

    def test_表里的每一格在界面上都真有一个控件(self):
        """反方向也要对：表里挂个界面上没有的 id，poolVal 永远读到空，那一格永远筛不出东西"""
        for 控件, _ in _界面表():
            次 = HTML.count('id="%s"' % 控件)
            assert 次 == 1, "%s 在界面上有 %d 个（0＝没那个框，≥2＝getElementById 只认第一个）" % (
                控件, 次)

    def test_每个形参都被真的用上(self):
        """接了参数却不参与判定＝那个框是摆设。专抓「签名里加了、函数体忘了改」"""
        没用上的 = [p for p in _后端形参() if p not in _筛行函数体()]
        assert not 没用上的, "筛行 收了这些参数却没在函数体里拿它们筛：%s" % 没用上的

    def test_旧的两颗勾选框没留下死参数(self):
        """界面把「只看本地没否过的」「只看 AI 匹配」换成表头两格之后，HTTP 那边不能
        留一套没人发的别名：同一个意思两种写法，早晚一种生效一种不生效"""
        for 禁 in ("only_pass", "ai_match"):
            assert 禁 not in APP, "%s 还在后端读，可界面上已经没有那个入口了" % 禁
        for 禁 in ("poolFilterOnlyPass", "poolFilterAiMatch"):
            assert 禁 not in HTML, "%s 已经换掉了，别再挂回来" % 禁

    def test_拼参数与判空与重置都跟着那张表(self):
        """三处都得遍历 POOL_FILTERS：任何一处改成手写清单，新加的那一格就漏"""
        for 函数, 关键 in (("poolFilterQs", "POOL_FILTERS.map"),
                          ("poolFilterOn", "POOL_FILTERS.some"),
                          ("resetPoolFilter", "POOL_FILTERS.forEach")):
            assert 关键 in _函数体(函数), \
                "%s 不再遍历 POOL_FILTERS（改成手写清单＝会漏新加的一格）" % 函数


class 逐列各筛各的Test:
    def test_岗位那一格只搜岗位名(self):
        assert _挑(岗位="标注") == {"1"}
        assert _挑(岗位="数据") == {"1", "3"}
        assert _挑(岗位="云标注") == set(), "「云标注」是公司里的字，岗位那格填它该一条不中"

    def test_公司那一格只搜公司(self):
        assert _挑(公司="云标注") == {"1"}
        assert _挑(公司="云裁") == {"3"}
        assert _挑(公司="数据") == {"3"}, "只有公司名里有「数据」的那条该留下"
        assert _挑(公司="数据标注员") == set(), "岗位名串进了公司这一格"

    def test_备注那一格只搜他自己写的备注(self):
        assert _挑(备注="猎头") == {"3"}
        assert _挑(备注="问过了") == {"1"}
        assert _挑(备注="坐班") == set(), "「坐班」在岗位标题里，不在备注里"

    def test_判分理由那一格只搜AI给的那句话(self):
        assert _挑(判分理由="命中否决词") == {"2"}
        assert _挑(判分理由="数据分析") == {"1"}
        assert _挑(判分理由="猎头") == set(), "「猎头」是他写的备注，不是 AI 的理由"

    def test_状态那一格认界面上那四个值(self):
        assert _挑(状态="applied") == {"2"}
        assert _挑(状态="new") == {"1", "5"}
        assert _挑(状态="") == 全部id, "空值＝这一格没参与"

    def test_城市那一格带商圈也认(self):
        assert _挑(城市="长沙") == {"1", "3", "4"}
        assert _挑(城市="上海") == {"2", "5"}

    def test_搜全部那一格还是跨列的(self):
        """工具栏那个「搜全部」跟逐列是两件事：它就是要在几列里一起找"""
        assert _挑(关键字="云裁") == {"3"}
        assert _挑(关键字="岳麓") == {"1"}

    def test_两格同时填是交集不是并集(self):
        """加筛选最容易做错成「或」：那样他会看到一堆根本没满足条件的行，以为筛错了"""
        assert _挑(岗位="数据", 城市="上海") == set()
        assert _挑(岗位="数据", 城市="长沙") == {"1", "3"}
        assert _挑(状态="new", 薪资下限K="15") == {"1"}

    def test_薪资那一格折成元每月再比(self):
        # 15-20K → 15000；31-36元/时 → 31×8×22＝5456；211-221元/天 → 211×22＝4642；
        # 4111-4611元/月 → 4111；面议 → 读不出，填了下限就不参与
        assert _挑(薪资下限K="4") == {"1", "2", "4", "5"}
        assert _挑(薪资下限K="5") == {"1", "2"}, \
            "时薪那条折出来是 5456，比月薪 4111 那条高：不折单位这两条会一起进出"
        assert _挑(薪资下限K="6") == {"1"}
        assert _挑(薪资下限K="15") == {"1"}
        assert _挑(薪资下限K="50") == set(), "上限填高了必须筛空，不许拿「面议」冒充过线"
        assert _挑(薪资下限K="") == 全部id, "这一格空着＝不看它，「面议」那几条照样在屏上"

    def test_薪资那格读的数与界面同一份(self):
        """折月的算法只有一份：界面「≥K」和后端「≥K」算出不一样的数，他筛完就是一脸雾"""
        assert 薪资折月("15-20K") == 15000
        assert 薪资折月("31-36元/时") == 5456
        assert 薪资折月("211-221元/天") == 4642
        assert 薪资折月("4111-4611元/月") == 4111
        assert 薪资折月("面议") is None

    def test_薪资格读不懂时整条不看这一格(self):
        """那个框里粘了别的东西、或浏览器塞了奇怪的值：不能报错，也不能筛空"""
        assert _挑(薪资下限K="abc") == 全部id
        assert _挑(薪资下限K="0") == 全部id

    def test_抓到那一格只认最近几天(self):
        assert _挑(抓到天内="1") == {"1", "4", "5"}
        assert _挑(抓到天内="7") == {"1", "2", "4", "5"}
        assert _挑(抓到天内="") == 全部id

    def test_AI那一格四档(self):
        assert _挑(AI档="未判") == {"3", "4", "5"}
        assert _挑(AI档="判过") == {"1", "2"}
        assert _挑(AI档="合") == {"1"}
        assert _挑(AI档="不合") == {"2"}

    def test_AI那一格与顶部那个数字同一句判据(self):
        行s = _行()
        for 指纹 in ("h1", ""):
            assert AI匹配数(行s, 指纹) == len(
                筛行(行s, AI档="合", 文档指纹=指纹)), "数字说几条、筛出来却另一条判据"

    def test_粗筛那一格四档(self):
        assert _挑(粗筛档="没过过的") == {"1", "3", "5"}, \
            "方向命中只撞软词的（行3）不许被藏掉，那是他点名方向的正主"
        assert _挑(粗筛档="被否过的") == {"2", "4"}
        assert _挑(粗筛档="要人看一眼") == {"3"}
        assert _挑(粗筛档="词表没读到") == {"5"}
        assert _挑(粗筛档="") == 全部id

    def test_认不出的档位不参与而不是筛空(self):
        """下拉里选了个后端没写过的词＝那条判据没实现；这时不许把整池筛成 0 条，
        那会让他以为「池子里没有」，而真相是界面和后端对不上"""
        assert _挑(AI档="没这个档") == 全部id
        assert _挑(粗筛档="没这个档") == 全部id

    def test_筛只是筛一个字都不改(self):
        """他每筛一次都不该动到状态、备注或 AI 结论"""
        原样 = _行()
        期望 = copy.deepcopy(原样)
        筛行(原样, 岗位="数据", 状态="new", 薪资下限K="15")
        assert 原样 == 期望


class 前端真跑一遍Test:
    """把 index.html 里那几个函数拿出来在 node 里真跑：光比对字面量抓不到「表里有、函数没读」"""

    @pytest.fixture(scope="class")
    def 表字面量(self):
        if shutil.which("node") is None:
            pytest.skip("需要 node 才能真跑前端那几个函数")
        m = re.search(r"var POOL_FILTERS = \[.*?\n\];", HTML, re.S)
        assert m, "没找到 POOL_FILTERS 那段"
        return m.group(0)

    def _跑(self, 表字面量, 值, 跑重置=False):
        """在 node 里把那几格填上（可选再点一次「重置」），把拼出来的 query 读回来。
        元素对象按 id 存住：不然 `el.value = ''` 改的是个临时副本，重置测了等于没测。
        POOL页 那条声明也要一起搬进来：重置现在还要把停在第几页归零，
        少了它就是 ReferenceError，而不是"没归零"。"""
        js = (
            "var VALS = %s;\n"
            "var EL = {};\n"
            "var document = {getElementById: function (id) {"
            "  if (!(id in EL)) EL[id] = {value: VALS[id] === undefined ? '' : VALS[id],"
            "                             options: [], innerHTML: '', textContent: ''};"
            "  return EL[id]; }};\n"
            "function loadJobPool(x) { return 1; }\n"
            "function toast(a, b) { return 1; }\n"
            "%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n"
            "%s"
            "console.log(JSON.stringify({qs: poolFilterQs(), 在筛: poolFilterOn(),"
            " 偏移: POOL页.偏移}));"
            % (json.dumps(值, ensure_ascii=False), 表字面量, _页声明(),
               _函数体("poolVal", 要整段=True),
               # poolFilterQs 现在多依赖 pool排参数()（表头点排与下拉那两项共用一份状态），
               # 切片里少带它就是 ReferenceError——这条注释说的正是它自己踩过的坑
               _函数体("pool排参数", 要整段=True),
               "var POOL排 = {列: '', 向: 'desc'};\n",
               _函数体("poolFilterQs", 要整段=True),
               _函数体("poolFilterOn", 要整段=True), _函数体("resetPoolFilter", 要整段=True),
               "POOL页.偏移 = 400;\n" if 跑重置 else "",
               "resetPoolFilter();" if 跑重置 else "")
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(js)
            路径 = f.name
        try:
            进 = subprocess.run(["node", 路径], capture_output=True, text=True, timeout=30)
        finally:
            Path(路径).unlink(missing_ok=True)
        assert 进.returncode == 0, 进.stderr
        return json.loads(进.stdout.strip())

    def _参数(self, 串):
        return parse_qs(urlparse("?" + 串).query, keep_blank_values=True)

    def test_填了哪几格就带哪几个值(self, 表字面量):
        结 = self._跑(表字面量, {"poolFilterJob": "标注", "poolFilterSalaryK": "15",
                                 "poolFilterStatus": "new"})
        参 = self._参数(结["qs"])
        assert 参["job"] == ["标注"] and 参["salary_min_k"] == ["15"]
        assert 参["status"] == ["new"]
        assert set(参) == {qs for _, qs in _界面表()} | {"sort"}, \
            "带的参数集合跟那张表不是一张：差集 %s" % (
                set(参) ^ ({qs for _, qs in _界面表()} | {"sort"}))
        assert 结["在筛"] is True

    def test_一格没填时不算在筛(self, 表字面量):
        结 = self._跑(表字面量, {i: "" for i, _ in _界面表()})
        assert 结["在筛"] is False, "没筛任何东西也算「有筛选在生效」，空表会怪到筛选头上"
        参 = self._参数(结["qs"])
        assert all(v == [""] for k, v in 参.items() if k != "sort")

    def test_排序照常带上但不当筛选(self, 表字面量):
        结 = self._跑(表字面量, {i: "" for i, _ in _界面表()})
        assert "sort=" in 结["qs"], "排序没进请求：屏上顺序跟他刚选的不是一回事"
        assert 结["在筛"] is False

    def test_中文档位要能原样到后端(self, 表字面量):
        """没 encode 的话「没过过的」在 query 里就是断的，后端读到一半字，筛出来永远是空"""
        结 = self._跑(表字面量, {"poolFilterGateState": "没过过的",
                                 "poolFilterAiState": "合"})
        assert "%E6%B2%A1" in 结["qs"], "没走 encodeURIComponent：中文参数会散架"
        参 = self._参数(结["qs"])
        assert 参["gate_state"] == ["没过过的"] and 参["ai_state"] == ["合"]

    def test_重置逐格清空(self, 表字面量):
        """手写的重置清单会漏掉新加的那一格——那一格筛完就再也退不回去"""
        填 = "坐班"
        结 = self._跑(表字面量, {i: 填 for i, _ in _界面表()}, 跑重置=True)
        参 = self._参数(结["qs"])
        还剩 = {k: v for k, v in 参.items() if k != "sort" and v != [""]}
        assert not 还剩, "点了重置这些格还有值：%s" % 还剩
        assert 结["在筛"] is False, "重置完还算「有筛选在生效」，空表会怪到筛选头上"
        assert 结["偏移"] == 0, "重置只清了格、人还停在第 3 页：屏上看着是「全部」，其实只是第三页"
