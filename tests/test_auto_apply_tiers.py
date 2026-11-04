# -*- coding: utf-8 -*-
"""「90 分这条线以上机器自己投，60~90 存起来给他看」这套分档的行为锁。

2026-11-04 他的原话：「AI匹配度90分的就自己投递，60以上90以下的一下存起来我看看」，
随后又改了一次口径：「你不需要投递，你负责搜索和初筛和回复就行」——所以配置里
`greet.auto_apply` 现在是 **false**，这套判据照旧留着（界面上那颗开关他随时能开），
这一族的门也就同时钉住"关着的时候一条都不许多勾"。

四件事各钉一头：
① 档只有一份判据（`job_pool.投递分档`）：机器拿去投的、表头那一格筛的、
   行上那个标签画的，三处必须同一条——抄第二套就会"筛出来 0 行、机器明明投了 5 条"；
② 两条线来自配置（`生效分档线`），界面上的**数字**也不许写死：他把线调到 85，
   下拉还在说 90，就是"看着对、其实没生效"那一类要人猜的账；
③ 撤过勾的行不再自动勾（他撤一次=表过一次态），撞了他手写否决词的行不自动投
   （那三十来个词比 AI 的一句 90 更接近他本人）；
④ 配置整份覆盖写的那一步（面板任何一次保存都是 to_dict）必须把这四个键带上，
   少写一条等于他点一次保存就把这条设置抹了——这坑 10-10 那天踩过（AI 接口 15→0）。

每条反向哨兵都验过：判据拆掉会红，判据写严过头也会红。
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FLASK_DIR = str(ROOT / "flask-version")

from boss_bot import job_pool as J  # noqa: E402
from boss_bot.unified_config import UnifiedConfig  # noqa: E402

HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")


def 行(分, 状态=None, 否=None, 勾=None, 名="岗位", 见过=1.0):
    return {"id": str(名) + str(分), "status": 状态 or J.状态待选,
            "job_name": 名, "last_seen_at": 见过,
            "ai": ({"分": 分, "判于": 1.0} if 分 is not None else {}),
            "gate": ({"否决命中": 否} if 否 else {}),
            "勾选": 勾 or {}}


# ── ① 那一条判据 ──────────────────────────────────────────

class Test分档那一句:
    def test_过线算自动投_线以下算待你看(self):
        assert J.投递分档(行(95), 90, 60) == J.档自动投
        assert J.投递分档(行(90), 90, 60) == J.档自动投   # 等于线算过（他要的是「90 分的就自己投」）
        assert J.投递分档(行(89.5), 90, 60) == J.档待你看
        assert J.投递分档(行(60), 90, 60) == J.档待你看
        assert J.投递分档(行(59), 90, 60) == J.档分不够

    def test_没判过的行不算任何好档(self):
        """AI 这次没答上来（分是空的）不能被画成"待你看"——那等于把"没判"说成"判过了"。"""
        assert J.投递分档(行(None), 90, 60) == J.档没判
        assert J.投递分档({"ai": {}}, 90, 60) == J.档没判
        assert J.投递分档({}, 90, 60) == J.档没判

    def test_撞了他手写的否决词就不许自动投(self):
        """否决词是他一条一条填的「这些一律不要」；AI 给这种行打 90 分是判错。"""
        assert J.投递分档(行(95, 否="坐班"), 90, 60) == J.档待你看

    def test_分数是字符串也认(self):
        """判分结论偶尔落成 "90"（模型给的是字符串）；读不成数字就等于这一档永远空。"""
        assert J.投递分档({"ai": {"分": "92", "判于": 1.0}}, 90, 60) == J.档自动投
        assert J.投递分档({"ai": {"分": "92x", "判于": 1.0}}, 90, 60) == J.档没判


class Test撤过勾不再自动勾:
    def test_面板撤勾的那一行不进候选(self):
        撤 = {"来源": "面板点击", "动作": "撤勾回待选"}
        assert J.人工撤过勾(行(95, 勾=撤)) is True
        assert J.自动投候选([行(95, 勾=撤)], 90, 60) == []

    def test_机器自己写的痕迹不算他撤过(self):
        """机器上一轮勾过、这一轮又被判回待选（试投没成留在队列里那种）不能从此不理它。"""
        assert J.人工撤过勾(行(95, 勾={"来源": "机器按 AI 分", "动作": "勾成要投"})) is False
        assert len(J.自动投候选([行(95, 勾={"来源": "机器按 AI 分"})], 90, 60)) == 1

    def test_没有勾选痕迹照旧能自动勾(self):
        assert len(J.自动投候选([行(95)], 90, 60)) == 1


class Test候选那一句只管该管的:
    def test_已投过_标不投_已在队列的都不再挑(self):
        行s = [行(95, 状态=J.状态已投), 行(95, 状态=J.状态不投),
               行(95, 状态=J.状态已选), 行(95, 状态=J.状态待选)]
        挑 = J.自动投候选(行s, 90, 60)
        assert len(挑) == 1, "机器不替他重复点名，也不把他撤掉的行又勾回来"

    def test_分高的在前(self):
        """每轮有个数上限——那先投的必须是更有把握的那几条，不然上限被 91 分占满、98 分排到下轮。"""
        挑 = J.自动投候选([行(91, 名="a"), 行(98, 名="b"), 行(95, 名="c")], 90, 60)
        assert [r["job_name"] for r in 挑] == ["b", "c", "a"]

    def test_新抓的排前同分时(self):
        挑 = J.自动投候选([行(95, 名="旧", 见过=100.0), 行(95, 名="新", 见过=900.0)], 90, 60)
        assert 挑[0]["job_name"] == "新"


# ── ② 两条线只从配置来 ────────────────────────────────────

class Test生效分档线:
    def test_从配置对象读(self):
        cfg = UnifiedConfig()
        cfg.greet.auto_apply_min_score = 85
        cfg.greet.review_min_score = 55
        assert J.生效分档线(cfg) == (85.0, 55.0)

    def test_从字典读也认(self):
        """面板那边有些调用点手上是一份 dict（to_dict 的产物），不能只认对象。"""
        assert J.生效分档线({"greet": {"auto_apply_min_score": 80,
                                    "review_min_score": 50}}) == (80.0, 50.0)

    def test_读不到就退回模块默认(self):
        assert J.生效分档线(None) == (float(J.自动投分数线), float(J.待你看分数线))
        assert J.生效分档线({"greet": {}}) == (90.0, 60.0)
        assert J.生效分档线({"greet": {"auto_apply_min_score": "abc"}}) == (90.0, 60.0)

    def test_两条线交叉时把待看线抬上来(self):
        """自动投线 60、待你看线 80 是手滑：一条岗会同时"既自动投又待你看"。"""
        assert J.生效分档线({"greet": {"auto_apply_min_score": 60,
                                    "review_min_score": 80}}) == (60.0, 60.0)

    def test_界面上没有写死的分数线(self):
        """「≥90」「60-89」这两个数字只许来自接口告诉它的 分档线。

        写死在渲染函数里 = 第二种真相：他把线调到 85，界面还在说 90。
        注释里出现数字不算——扫之前先把注释行剪掉（这条门自己就被注释打过一次，
        项目里"判据本身要先验一次"是吃过亏写下来的）。
        """
        体 = HTML[HTML.index("function pool档标签"):HTML.index("function pool同步分档标签")]
        代码 = "\n".join(l for l in 体.splitlines() if not l.strip().startswith("//"))
        assert "pool分档线" in 代码, "那两档的数字没读 分档线"
        for 硬 in ["90", "89", "60"]:
            assert 硬 not in 代码, "pool档标签 的代码里写死了 %s——线改了界面还在说旧数" % 硬

    def test_开关关着的那句要说没开(self):
        """他 11-04 后半句改的口径是「你不需要投递」：关着还印「自动投（≥90）6」
        就读起来像机器在自己发消息。"""
        体 = HTML[HTML.index("function pool档标签"):HTML.index("function pool同步分档标签")]
        assert "pool自动投开关" in 体, "这句没问开关，关着的时候也在说机器会自动投"
        assert "开关关着" in 体


# ── ③ 筛的那一格与投的那一步同源 ─────────────────────────

class Test筛与投同源:
    def test_筛自动投筛出来的就是机器会挑的(self):
        行s = [行(95), 行(89), 行(60), 行(59), 行(95, 否="坐班"),
              行(95, 状态=J.状态已投), 行(95, 勾={"来源": "面板点击", "动作": "撤勾回待选"})]
        筛 = J.筛行(行s, AI档=J.档自动投, 自动线=90, 待看线=60)
        挑 = J.自动投候选(行s, 90, 60)
        assert sorted(r["id"] for r in 筛) == sorted(r["id"] for r in 挑), \
            "表头那一格筛出的行数和机器真拿去勾的不是同一批"

    def test_筛待你看(self):
        筛 = J.筛行([行(95), 行(89), 行(60), 行(59)], AI档=J.档待你看, 自动线=90, 待看线=60)
        assert [r["ai"]["分"] for r in 筛] == [89, 60]

    def test_没给线时按模块默认筛(self):
        """调用点漏喂线也不能筛出空表骗人——默认值就是那两条模块常量。"""
        assert len(J.筛行([行(95)], AI档=J.档自动投)) == 1

    @pytest.mark.parametrize("函数名,字样", [
        ("api_job_pool", "自动线"),
        ("_档计数", "自动投候选"),
    ])
    def test_后端真的把线喂进去了(self, 函数名, 字样):
        """接口读了配置里那两条线、那一档用机器同一句候选判据——少接一头就是两套账。"""
        起 = APP.index("def " + 函数名)
        体 = APP[起:起 + 4000]
        assert 字样 in 体

    def test_界面上那两档的value与后端档名一致(self):
        """下拉里 value 写错一个字，那一格就永远是"筛不出行"，而数字还在上面挂着。"""
        assert '<option value="自动投">' in HTML
        assert '<option value="待你看">' in HTML
        assert J.档自动投 == "自动投" and J.档待你看 == "待你看"


# ── ④ 机器自动勾那一步 ───────────────────────────────────

def _桩loop(池, tmp_path, **盖):
    """造一个只带配置和池的 loop，浏览器、线程、面板都不起。"""
    from boss_bot.main_loop import UnifiedBotLoop
    cfg = UnifiedConfig()
    cfg.greet.auto_apply = 盖.get("auto_apply", True)
    cfg.greet.auto_apply_min_score = 盖.get("线", 90)
    cfg.greet.review_min_score = 盖.get("待看线", 60)
    cfg.greet.auto_apply_max_per_round = 盖.get("上限", 5)
    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp.config = cfg
    lp._running = 盖.get("running", True)
    lp._greet_paused = False
    lp.account_index = 0
    lp._logs = []
    lp._log = lambda 级别, 话: lp._logs.append((级别, str(话)))
    lp._job_pool = lambda: 池
    return lp


@pytest.fixture()
def 池(tmp_path):
    with patch("boss_bot.job_pool.resolve_path", return_value=tmp_path):
        p = J.岗位池(account_index=0)
        p.收([{"job_name": "AI产品经理", "company": "A", "url": "https://www.zhipin.com/job_detail/a1.html"},
             {"job_name": "大模型产品", "company": "B", "url": "https://www.zhipin.com/job_detail/b2.html"},
             {"job_name": "数据分析师", "company": "C", "url": "https://www.zhipin.com/job_detail/c3.html"},
             {"job_name": "AI产品（坐班）", "company": "D", "url": "https://www.zhipin.com/job_detail/d4.html"}])
        ids = [r["id"] for r in p.列表()]
        p.改(ids[0], ai={"分": 95, "判于": 1.0})
        p.改(ids[1], ai={"分": 92, "判于": 1.0})
        p.改(ids[2], ai={"分": 70, "判于": 1.0})
        p.改(ids[3], ai={"分": 96, "判于": 1.0}, gate={"否决命中": "坐班"})
        return p, ids


class Test自动勾那一步:
    def test_过线的被勾上_线以下的不动(self, 池):
        p, ids = 池
        lp = _桩loop(p, None)
        assert lp._自动勾过线的(p) == 2
        状 = {r["id"]: r["status"] for r in p.列表()}
        assert 状[ids[0]] == J.状态已选 and 状[ids[1]] == J.状态已选
        assert 状[ids[2]] == J.状态待选, "线以下的要被投出去——那是替他做了没批准的事"
        assert 状[ids[3]] == J.状态待选, "撞了他手写否决词的，AI 给 96 分也不许自动投"

    def test_留痕写的是机器不是人(self, 池):
        p, ids = 池
        _桩loop(p, None)._自动勾过线的(p)
        痕 = [r for r in p.列表() if r["id"] == ids[0]][0]["勾选"]
        assert 痕["来源"] == "机器按 AI 分" and "90" in 痕["动作"]
        assert 痕["首于"], "第一次动这一行的时间要留着，丢了就答不出这条什么时候进池的"

    def test_开关关掉一条都不勾(self, 池):
        """他 11-04 后半句改的口径就落在这里：机器只搜索 + 初筛 + 回复，不投。"""
        p, ids = 池
        assert _桩loop(p, None, auto_apply=False)._自动勾过线的(p) == 0
        assert all(r["status"] == J.状态待选 for r in p.列表())

    def test_每轮上限夹住(self, 池):
        p, ids = 池
        assert _桩loop(p, None, 上限=1)._自动勾过线的(p) == 1, "一次勾几十条=把每日额度一次打满"
        assert _桩loop(p, None, 上限=0)._自动勾过线的(p) == 0, "填 0 就是把这一步关掉"

    def test_撤过勾的这一辈子不再自动勾(self, 池):
        p, ids = 池
        p.标状态([ids[1]], J.状态待选, 留痕={"来源": "面板点击", "动作": "撤勾回待选"})
        assert _桩loop(p, None)._自动勾过线的(p) == 1, "撤过一次还接着勾=把他的操作盖掉"
        assert [r for r in p.列表() if r["id"] == ids[1]][0]["status"] == J.状态待选

    def test_线改了候选跟着改(self, 池):
        p, ids = 池
        lp = _桩loop(p, None, 线=70)      # 线降到 70：那条 70 分的也该进来了
        assert lp._自动勾过线的(p) == 3

    def test_没在跑就不勾(self, 池):
        """点了停止之后还往队列里塞东西，下一次启动会莫名投出几条。"""
        p, ids = 池
        assert _桩loop(p, None, running=False)._自动勾过线的(p) == 0

    def test_写不进盘就照实说不谎报(self, 池):
        p, ids = 池
        lp = _桩loop(p, None)
        with patch.object(type(p), "改", return_value=(False, "盘上读不回来")):
            assert lp._自动勾过线的(p) == 0
        assert any("自动勾没写进去" in 话 for 别, 话 in lp._logs)


class Test两档互斥:
    """同一条岗位不能既算「机器自动投」又算「待你看」——两个数字加起来会超过真正待办的行数。

    机器让出来的那两种（撞了他手写的否决词、他撤过勾）必须落进「待你看」：
    机器不替他决定的东西，本来就是给他看的。
    """

    撤 = {"来源": "面板点击", "动作": "撤勾回待选"}

    def test_机器挑走的就不算待你看(self):
        r = 行(95)
        assert J.机器会挑这条(r, 90, 60) and not J.待他看的(r, 90, 60)

    def test_撤过勾的95分落到待你看(self):
        r = 行(95, 勾=self.撤)
        assert not J.机器会挑这条(r, 90, 60)
        assert J.待他看的(r, 90, 60), "他撤过一次，机器就别再碰，但这条还得让他看得见"

    def test_撞否决词的95分落到待你看(self):
        r = 行(95, 否="坐班")
        assert not J.机器会挑这条(r, 90, 60)
        assert J.待他看的(r, 90, 60)

    def test_已经投过标过不投的两档都不算(self):
        for 状态 in (J.状态已投, J.状态不投, J.状态已选):
            r = 行(95, 状态=状态)
            assert not J.机器会挑这条(r, 90, 60) and not J.待他看的(r, 90, 60), 状态

    def test_两档加起来等于待选里过线的行数(self):
        行s = [行(95), 行(92, 勾=self.撤), 行(96, 否="坐班"), 行(70), 行(50),
               行(95, 状态=J.状态已投), 行(80, 状态=J.状态不投)]
        自动 = len(J.自动投候选(行s, 90, 60))
        看 = sum(1 for r in 行s if J.待他看的(r, 90, 60))
        过线待办 = sum(1 for r in 行s
                       if r["status"] == J.状态待选
                       and float(r["ai"].get("分") or 0) >= 60)
        assert 自动 + 看 == 过线待办 == 4

    def test_筛行两档也互斥(self):
        行s = [行(95), 行(92, 勾=self.撤), 行(96, 否="坐班"), 行(70), 行(50)]
        a = J.筛行(行s, AI档=J.档自动投, 自动线=90, 待看线=60)
        b = J.筛行(行s, AI档=J.档待你看, 自动线=90, 待看线=60)
        assert not ({r["id"] for r in a} & {r["id"] for r in b})
        assert len(a) == 1 and len(b) == 3


# ── ⑦ 「低于 60 分的别再存在池里」（11-04 他补的口径）─────

class Test低分行挪出池:
    """他说的是「不要存在那个数据库里面了，没意义」，项目里这条族的规矩是只挪不删。

    两头都要钉：该挪的挪走（不然四千行里翻四百条能看的），
    不该挪的一条不许动（没判过分的、他自己勾过的、他标过不投的）。
    """

    @pytest.fixture()
    def 副本池(self, tmp_path):
        with patch("boss_bot.job_pool.resolve_path", return_value=tmp_path):
            p = J.岗位池(account_index=0, 目录=str(tmp_path))
            样 = [("能投的岗", 95), ("擦边的岗", 70), ("普工", 20), ("主播", 0),
                 ("老师", 55), ("还没判的", None)]
            p.收([{"job_name": n, "company": "公司" + n,
                   "url": "https://www.zhipin.com/job_detail/%s.html" % i}
                  for i, (n, _) in enumerate(样)])
            行s = p.列表()
            for (n, 分), r in zip(样, 行s):
                if 分 is not None:
                    p.改(r["id"], ai={"分": 分, "判于": 1.0})
            return p, {n: r for (n, _), r in zip(样, 行s)}

    def test_低于线的挪走_高于线的留下(self, 副本池):
        p, 表 = 副本池
        挪了, 话 = p.挪走低于线(60, "口径A")
        assert 挪了 == 3, "20 / 0 / 55 这三条该走"
        assert [r["job_name"] for r in p.列表()] == ["能投的岗", "擦边的岗", "还没判的"]
        assert "job_pool_screened_out" in 话

    def test_没判过分的一条不动(self, 副本池):
        """"还没轮到精筛"不等于"没达到条件"——挪走它等于替他判了。"""
        p, 表 = 副本池
        p.挪走低于线(60, "口径A")
        assert any(r["job_name"] == "还没判的" for r in p.列表())

    def test_他勾过的_投过的_标过不投的不许挪(self, 副本池):
        p, 表 = 副本池
        p.改(表["普工"]["id"], status=J.状态已投)
        p.改(表["主播"]["id"], status=J.状态不投)
        p.改(表["老师"]["id"], status=J.状态已选)
        挪了, _ = p.挪走低于线(60, "口径A")
        assert 挪了 == 0, "机器不替他撤销自己点过的名，也不把他的账挪走"

    def test_留档带着原因时刻和当时那套口径(self, 副本池):
        p, 表 = 副本池
        p.挪走低于线(60, "口径A")
        份 = json.loads(Path(str(p.筛掉路径)).read_text(encoding="utf-8"))
        assert len(份) == 3
        for r in 份:
            assert "低于 60" in r["挪出原因"] and r["挪出时刻"]
            assert r["挪出口径"] == "口径A", \
                "没盖口径就没法在改了阈值/提示词之后把这批岗捞回来重判"

    def test_换了判定口径就该让他重新进池(self, 副本池):
        """旧结论跟新要求没关系了：这一条不放开，"换一份筛选要求重判"就永远捞不回这些岗。"""
        p, 表 = 副本池
        p.挪走低于线(60, "口径A")
        assert len(p.筛掉身份("口径A")) == 3
        assert p.筛掉身份("口径B") == set(), "换了阈值/提示词/要求文件还在拦=这些候选永久消失"
        assert p.筛掉身份("") == set(), "还没存过要求文件时一个都不拦"

    def test_留档读不出来就一条都不动(self, 副本池):
        """那份是唯一的留档：读不出来还照样挪，等于把原件盖掉。"""
        p, 表 = 副本池
        Path(p.筛掉路径).write_text("{坏文件", encoding="utf-8")
        挪了, 话 = p.挪走低于线(60, "口径A")
        assert 挪了 == 0 and "读不出来" in 话
        assert len(p.列表()) == 6

    def test_线填0就是这条不收(self, 副本池):
        p, 表 = 副本池
        assert p.挪走低于线(0, "口径A")[0] == 0
        assert p.挪走低于线("很高", "口径A")[0] == 0

    def test_再收一次不重复登记(self, 副本池):
        """同一条岗位换了要求又进来、又没过线：留档里可以有多次记录，池里不许有重复行。"""
        p, 表 = 副本池
        assert p.挪走低于线(60, "口径A")[0] == 3
        assert p.挪走低于线(60, "口径A")[0] == 0, "第二次进来不该再挪一遍"

    def test_配置带着这条开关(self):
        cfg = UnifiedConfig()
        assert cfg.greet.drop_below_review is True
        cfg._apply_bot_config({"greet": {"drop_below_review": False}})
        assert cfg.greet.drop_below_review is False
        assert cfg.to_dict()["greet"]["drop_below_review"] is False

    def test_抓取腿真的问了这道闸(self):
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert "池.挪走低于线(" in 源
        assert "池.筛掉身份(" in 源, "挪出去的岗位下一轮又被捡回来重判——白烧精筛额度"


# ── ⑧ 开关关着的时候不许说"机器会投" ─────────────────────

class Test开关关着要说实话:
    def test_接口要回开关状态(self):
        """11-04 他后半句改的口径是「你不需要投递」。面板印着「自动投（≥90）6」
        而开关其实关着，他就会以为机器在自己发消息。"""
        assert "自动投开关" in APP
        assert '"自动投开关"' in APP or "自动投开关" in APP[APP.index('"分档线"') - 800:
                                                        APP.index('"分档线"') + 800]

    def test_界面上那句要说清是没开(self):
        段 = HTML[HTML.index("function pool档标签"):HTML.index("function pool同步分档标签")]
        assert "自动投开关" in 段 or "开关" in 段, "关着的时候还在说机器会自动投"

# ── ⑤ 配置整份覆盖写时这四个键不许丢 ─────────────────────

class Test配置覆盖写不丢这四个键:
    def test_to_dict带上了(self):
        cfg = UnifiedConfig()
        cfg.greet.auto_apply = True
        cfg.greet.auto_apply_min_score = 85
        cfg.greet.review_min_score = 55
        cfg.greet.auto_apply_max_per_round = 3
        段 = cfg.to_dict()["greet"]
        assert 段["auto_apply"] is True
        assert 段["auto_apply_min_score"] == 85
        assert 段["review_min_score"] == 55
        assert 段["auto_apply_max_per_round"] == 3
        assert "capture_only" in 段

    def test_写出去再读回来一模一样(self, tmp_path):
        """面板任何一次「保存配置」都是 to_dict() 整份覆盖——这四个键少写一条就没了。

        先原样存一份再改：`save()` 里那道"内存这份像出厂默认就别盖掉盘上账号/接口"
        的闸会拦住空对空的覆盖写（那是防 10-10 那天测试把用户配置盖掉三次的），
        这条门要验的是"改完还能原样读回来"，不是验那道闸。
        """
        cfg = UnifiedConfig()
        路径 = tmp_path / "bot_config.json"
        路径.write_text("{}", encoding="utf-8")
        cfg.save(str(路径))                      # 先落一份同形状的，让守卫有的比
        cfg.greet.auto_apply = False
        cfg.greet.auto_apply_min_score = 88
        cfg.greet.review_min_score = 62
        cfg.greet.auto_apply_max_per_round = 7
        cfg.save(str(路径))
        回 = UnifiedConfig.load(str(路径))
        assert 回.greet.auto_apply is False
        assert 回.greet.auto_apply_min_score == 88
        assert 回.greet.review_min_score == 62
        assert 回.greet.auto_apply_max_per_round == 7

    def test_越界的值被夹住(self):
        """线填 0 等于"全都自己投"、填 120 等于"永远不投"；上限填 9999 等于没有上限。"""
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"greet": {"auto_apply_min_score": 120,
                                        "review_min_score": -5,
                                        "auto_apply_max_per_round": 9999}})
        assert cfg.greet.auto_apply_min_score == 100
        assert cfg.greet.review_min_score == 0
        assert cfg.greet.auto_apply_max_per_round == 50

    def test_坏值不炸也不当成0(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"greet": {"auto_apply_min_score": "很高",
                                        "auto_apply_max_per_round": ""}})
        assert cfg.greet.auto_apply_min_score == 90
        assert cfg.greet.auto_apply_max_per_round == 5

    def test_线上那份配置现在是关着自动投的(self):
        """他 11-04 的口径：机器只搜索 + 初筛 + 回复。盘上要是变成 True，这条得红。"""
        import json
        配 = json.loads((ROOT / "bot_config.json").read_text(encoding="utf-8"))
        assert 配["greet"]["capture_only"] is True
        assert 配["greet"]["auto_apply"] is False


# ── ⑥ 界面上那四个控件真的接到了配置 ─────────────────────

class Test界面控件接线:
    def test_四个控件都在(self):
        for id in ["advAutoApply", "advAutoApplyMin", "advReviewMin", "advAutoApplyMax"]:
            assert 'id="%s"' % id in HTML, "少了 %s：要么没摆出来，要么改名把接线扯断了" % id

    def test_保存时四个都写回(self):
        for 键 in ["auto_apply", "auto_apply_min_score", "review_min_score",
                  "auto_apply_max_per_round"]:
            assert ("config.greet." + 键) in HTML, "%s 没进保存，界面改了等于没改" % 键

    def test_加载时四个都回显(self):
        for id in ["advAutoApply", "advAutoApplyMin", "advReviewMin", "advAutoApplyMax"]:
            开关位 = "setEl('%s'" % id
            数字位 = "setVal('%s'" % id
            assert 开关位 in HTML or 数字位 in HTML, \
                "%s 只写不读：保存后重开界面看着像没生效" % id

    def test_每轮上限那个0不许被当成没填(self):
        """`parseInt(x) || 5` 会把 0 变成 5——填 0 是"关掉机器自己勾"。"""
        段 = HTML[HTML.index("config.greet.auto_apply_max_per_round") - 1200:
                 HTML.index("config.greet.auto_apply_max_per_round")]
        assert "_advNum(" in 段, "这一族不许用 || 默认值 的写法"
