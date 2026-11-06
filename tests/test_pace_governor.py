# -*- coding: utf-8 -*-
"""节奏闸（boss_bot/pace.py）的行为锁 —— 11-04 被按 IP 关 24 小时之后加的第二层。

当天封禁前两天的现数：每小时 57~61 次搜索整页加载 + 每小时 255~316 轮会话页，
连着五个小时；而且间隔规律到不像真人（会话页平均 11 秒一趟）。
上一轮装的那道 `page_loads_per_hour` 只有一个总数，分不出是哪一族打的，
也管不了"等间隔"和"凌晨三点还在刷"这两件事。

这一层按用户点名的六条来：
1. 天花板再压低：搜索页 ≤15/小时（双号合计）、会话页轮次 ≤60/小时；
2. 间隔随机抖动，不许等间隔；
3. 活跃时段 09:00~18:00（北京时间），窗口外完全静默；
4. 每日上限：搜索页 ≤200/天，回复有日额度；
5. 熔断升级：命中风控按 5min → 15min → 60min 阶梯冷却，而不是一直重试；
6. 面板实时看得见节奏（`/api/pace` + 节奏条）。

每条都配反向哨兵：判据拆掉要红，写过头（窗口把 15:11 判成窗口外、
抖动把间隔摇成 0、熔断前科永久不清）也要红。
"""
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import pace  # noqa: E402
from boss_bot.unified_config import UnifiedConfig  # noqa: E402

HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
import json
CFG = json.loads((ROOT / "bot_config.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _干净闸():
    """进程级全局账，逐条还原——否则这条摇过的种子、装过的限额会漏给下一条。"""
    原 = {k: list(v) if isinstance(v, list) else v
          for k, v in pace.默认限额.items()}
    常量 = (dict(抖动比例=pace.抖动比例, 活跃起=pace.活跃起, 活跃止=pace.活跃止,
                 活跃启用=pace.活跃启用, 熔断阶梯=pace.熔断阶梯,
                 风控窗口秒=pace.风控窗口秒))
    pace.清零()
    yield
    pace.清零()
    for 类, (小, 日) in 原.items():
        pace.默认限额[类] = 小
        pace.默认限额[类] = dict(原[类]) if isinstance(原[类], dict) else 原[类]
    for 名, 值 in 常量.items():
        pace.全局(名, 值)


def _时刻(h, m=0):
    """按北京时间造一个时间戳（窗口那几条要的是"几点"，不是"隔多久"）。"""
    本 = datetime.now(timezone(timedelta(hours=8)))
    return 本.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()


# ── 1. 天花板 ────────────────────────────────────────

class Test天花板:
    def test_搜索页每小时十五次(self):
        pace.装限额(小时={"搜索页": 15})
        for i in range(15):
            assert pace.请求闸("搜索页")[0], "第 %d 发就该能发" % (i + 1)
        允许, 为什么, 等多久 = pace.请求闸("搜索页")
        assert not 允许 and "15" in 为什么 and 等多久 > 0

    def test_会话页每小时六十次(self):
        pace.装限额(小时={"会话页": 60})
        for _ in range(60):
            assert pace.请求闸("会话页")[0]
        assert pace.请求闸("会话页")[0] is False

    def test_两族分账不互相顶(self):
        """搜索页打满了不能把会话页也拦了——那是两种请求，风控敏感度不同。"""
        pace.装限额(小时={"搜索页": 2, "会话页": 5})
        pace.请求闸("搜索页"); pace.请求闸("搜索页")
        assert pace.请求闸("搜索页")[0] is False
        assert pace.请求闸("会话页")[0] is True, "搜索页的账不能算到会话页头上"

    def test_两号共用一份账(self):
        """风控记的是出口 IP：按号分账等于没装。"""
        pace.装限额(小时={"搜索页": 2})
        pace.请求闸("搜索页"); pace.请求闸("搜索页")
        assert pace.请求闸("搜索页")[0] is False

    def test_窗口滑走自动腾位置(self):
        pace.装限额(小时={"搜索页": 1})
        现在 = time.time()
        assert pace.请求闸("搜索页", 现在)[0]
        assert pace.请求闸("搜索页", 现在 + 10)[0] is False
        assert pace.请求闸("搜索页", 现在 + 3601)[0], "一小时前的账还压着就是窗口没滑走"

    def test_填0就是不限量(self):
        pace.装限额(小时={"搜索页": 0})
        for _ in range(50):
            assert pace.请求闸("搜索页")[0], "0 是有效值：关掉这一族的闸，不是全拦"

    def test_未知类别不拦(self):
        assert pace.请求闸("没这一族")[0] is True


# ── 2. 抖动 ─────────────────────────────────────────

class Test抖动:
    def test_摇开且不出范围(self):
        pace.清零()
        值 = [pace.抖动(100) for _ in range(200)]
        assert len(set(值)) > 30, "200 次摇出 30 种以内等于没抖"
        assert all(65 <= v <= 135 for v in 值), "±35% 之外的值不该出现"
        assert min(值) < 100 < max(值), "全在一侧说明摇法是单向的"

    def test_比例0就是原样(self):
        assert pace.抖动(100, 0) == 100

    def test_坏基准不炸(self):
        assert pace.抖动(0) == 0.0
        assert pace.抖动("很快") == 0.0
        assert pace.抖动(None) == 0.0

    def test_同种子可复现(self):
        pace.装随机种子(7); a = [round(pace.抖动(30), 3) for _ in range(5)]
        pace.装随机种子(7); b = [round(pace.抖动(30), 3) for _ in range(5)]
        assert a == b, "测试要能钉住抖动，否则那条断言是随机的"

    def test_摇完不会小于1秒(self):
        assert pace.抖动(1.2) >= 1.0, "摇成 0 秒等于把等待整段抹掉"


# ── 3. 活跃时段 ──────────────────────────────────────

class Test活跃时段:
    def test_窗口内放行(self):
        pace.清零()
        for h in (9, 12, 17):
            assert pace.活跃窗口(_时刻(h))[0], "%d 点该干活" % h

    def test_窗口外静默并给出到点时间(self):
        pace.清零()
        在, 等多久 = pace.活跃窗口(_时刻(3))
        assert not 在 and 6 * 3500 < 等多久 < 6.1 * 3600, "凌晨 3 点该等到早上 9 点"
        assert pace.活跃窗口(_时刻(18))[0] is False, "18:00 是不含的"

    def test_关掉就全天放行(self):
        pace.装限额(活跃=(9, 18, False))
        assert pace.活跃窗口(_时刻(3))[0] is True

    def test_跨零点窗口也认(self):
        pace.装限额(活跃=(22, 6, True))
        assert pace.活跃窗口(_时刻(23))[0] is True
        assert pace.活跃窗口(_时刻(5))[0] is True
        assert pace.活跃窗口(_时刻(12))[0] is False

    def test_坏起止被夹住(self):
        pace.装限额(活跃=(99, -3, True))
        s = pace.现状()
        assert 0 <= s['活跃窗口']['起'] <= 23 and 1 <= s['活跃窗口']['止'] <= 24


# ── 4. 每日上限 ──────────────────────────────────────

class Test每日上限:
    def test_日额度打满就等到明天(self):
        pace.装限额(小时={"搜索页": 100}, 日={"搜索页": 3})
        现在 = time.time()
        for _ in range(3):
            assert pace.请求闸("搜索页", 现在)[0]
        允许, 为什么, 等多久 = pace.请求闸("搜索页", 现在)
        assert not 允许 and "今日" in 为什么
        assert 等多久 > 0, "日额度要说得出还要等多久，不能只说'不行'"

    def test_跨零点自动清零(self):
        pace.装限额(小时={"搜索页": 100}, 日={"搜索页": 2})
        深夜 = _时刻(23, 59)
        pace.请求闸("搜索页", 深夜); pace.请求闸("搜索页", 深夜)
        assert pace.请求闸("搜索页", 深夜)[0] is False
        assert pace.请求闸("搜索页", 深夜 + 120)[0], "过了零点日额度必须清零"

    def test_小时与日两层都要过(self):
        pace.装限额(小时={"搜索页": 2}, 日={"搜索页": 100})
        现在 = time.time()
        pace.请求闸("搜索页", 现在); pace.请求闸("搜索页", 现在)
        assert pace.请求闸("搜索页", 现在)[0] is False      # 小时先到
        assert pace.请求闸("搜索页", 现在 + 3601)[0]         # 小时腾位、日还没满


# ── 5. 熔断升级 ──────────────────────────────────────

class Test熔断升级:
    def test_阶梯五分到十五分到六十分钟(self):
        pace.清零(); 现在 = time.time()
        assert pace.命中风控(现在) == 300
        assert pace.命中风控(现在 + 60) == 900
        assert pace.命中风控(现在 + 120) == 3600
        assert pace.命中风控(现在 + 180) == 3600, "顶档之后不许继续涨"

    def test_冷却期间一切不发(self):
        pace.清零(); 现在 = time.time()
        pace.命中风控(现在)
        for 类 in pace.类别s:
            允许, 为什么, _ = pace.请求闸(类, 现在 + 30)
            assert not 允许 and "熔断" in 为什么, 类

    def test_安静过窗口前科清零(self):
        """不能把一次偶发滑块记成永久前科，否则跑一天会被自己钉死在 60 分钟。"""
        pace.清零(); 现在 = time.time()
        assert pace.命中风控(现在) == 300
        assert pace.命中风控(现在 + 7200) == 300, "隔两小时再命中，该从第一档重新开始"

    def test_冷却会自己到期(self):
        pace.清零(); 现在 = time.time()
        pace.命中风控(现在)
        assert pace.冷却剩余(现在 + 299) > 0
        assert pace.冷却剩余(现在 + 301) == 0
        assert pace.请求闸("搜索页", 现在 + 301)[0]

    def test_阶梯可配且形状不对就整条退回(self):
        pace.装限额(熔断=["600", "1800"])
        现在 = time.time()
        assert pace.命中风控(现在) == 600
        assert pace.命中风控(现在 + 10) == 1800
        pace.装限额(熔断=["900", "300"])       # 不递增 = 形状不对
        assert pace.全局("熔断阶梯") == (600, 1800),             "把写反的阶梯排好序装上，就是配置说 A、闸里装 B"
        pace.装限额(熔断=["30", "99999"])      # 越界
        assert pace.全局("熔断阶梯") == (600, 1800), "越界的阶梯要整条不装"


# ── main_loop 接线 ───────────────────────────────────

def _桩loop(**盖):
    from boss_bot.main_loop import UnifiedBotLoop
    cfg = UnifiedConfig()
    rl = cfg.greet.rate_limit
    rl.search_loads_per_hour = 盖.get("搜索小时", 15)
    rl.chat_rounds_per_hour = 盖.get("会话小时", 60)
    rl.search_loads_per_day = 盖.get("搜索日", 200)
    rl.jitter_ratio = 盖.get("抖动", 35)
    rl.active_hours_enabled = 盖.get("启用窗口", True)
    rl.active_hours_start = 盖.get("起", 9)
    rl.active_hours_end = 盖.get("止", 18)
    rl.risk_cooldown_ladder = 盖.get("阶梯", "300,900,3600")
    rl.page_loads_per_hour = 盖.get("总量", 15)
    cfg.reply.check_interval = 15
    cfg.reply.idle_backoff_max = 120
    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp.config = cfg
    lp._logs = []
    lp._log = lambda 级别, 话: lp._logs.append((级别, str(话)))
    lp._说过 = {}
    lp._notifier = None
    lp._ip封着 = False
    lp._额度说过 = False
    lp._health_why = ""
    # _风控命中一次 会经 _emit_wind 上报，那条要这三个属性（缺一个就是 AttributeError，
    # 而不是"冷却没装上"——桩要像真对象，否则测到的是桩自己的洞）
    lp._wind_control_cb = 盖.get("上报", None)
    lp.account_index = 0
    lp.account_name = "主账号（AI产品经理）"
    return lp


class Test接线:
    def test_配置灌进闸并回读(self):
        lp = _桩loop(搜索小时=12, 会话小时=45, 搜索日=150)
        现状 = lp._装节奏闸()
        assert 现状["每类"]["搜索页"]["小时额度"] == 12
        assert 现状["每类"]["会话页"]["小时额度"] == 45
        assert 现状["每类"]["搜索页"]["日额度"] == 150
        assert 现状["活跃窗口"]["起"] == 9 and 现状["活跃窗口"]["止"] == 18

    def test_窗口外收手并说明(self):
        lp = _桩loop(起=1, 止=2)          # 一个几乎不可能在里面的窗口
        with patch("boss_bot.pace.活跃窗口", return_value=(False, 3600.0)):
            能发, 等多久 = lp._节奏放行("搜索页", "打招呼")
        assert not 能发 and 等多久 == 3600
        assert any("活跃时段" in 话 for 别, 话 in lp._logs)

    def test_熔断期间收手(self):
        lp = _桩loop()
        with patch("boss_bot.pace.冷却剩余", return_value=600.0):
            assert lp._节奏放行("搜索页", "打招呼")[0] is False
        assert any("熔断" in 话 for 别, 话 in lp._logs)

    def test_同一句只说一遍(self):
        """一分钟一趟、每趟两号，不说就会把真要看的行淹掉（这项目为这事收敛过三次）。"""
        lp = _桩loop()
        with patch("boss_bot.pace.活跃窗口", return_value=(False, 3600.0)):
            lp._节奏放行("搜索页", "打招呼")
            lp._节奏放行("搜索页", "打招呼")
            lp._节奏放行("会话页", "回复")
        # 只数 INFO：被去重的那些降级成 DEBUG 了（留着能查，但不许淹掉日志）
        说过的 = [话 for 别, 话 in lp._logs if "活跃时段" in 话 and 别 == "INFO"]
        assert len(说过的) == 2, "该按侧各说一句：两号共用一份账，但日志要看得出是谁收的手"
        沉的 = [话 for 别, 话 in lp._logs if "活跃时段" in 话 and 别 == "DEBUG"]
        assert len(沉的) == 1, "重复那句要转 DEBUG 留着能查，不能一句都不剩"

    def test_风控命中走阶梯(self):
        lp = _桩loop()
        assert lp._风控命中一次("打招呼", "验证码") == 300
        assert any("熔断冷却" in 话 for 别, 话 in lp._logs)

    def test_搜索词与回复轮都问了闸(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert 'self._节奏放行("搜索页", "打招呼")' in src
        assert 'self._节奏放行("会话页", "回复")' in src
        assert "pace.抖动(GREET_ROUND_INTERVAL)" in src, "两轮之间还在用固定 30 秒"
        assert "pace.抖动(self._回复间隔秒())" in src, "回复间隔没叠抖动"

    def test_启动与热重载都装闸(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert src.count("self._装节奏闸()") >= 2, "只在启动装一次，界面改了就不生效"


# ── 7. 界面上这些旋钮三端都接得上 ────────────────────

class Test节奏旋钮接线:
    控件 = [("searchLoadsPerHour", "search_loads_per_hour"),
            ("chatRoundsPerHour", "chat_rounds_per_hour"),
            ("detailLoadsPerHour", "detail_loads_per_hour"),
            ("searchLoadsPerDay", "search_loads_per_day"),
            ("jitterRatio", "jitter_ratio"),
            ("activeHoursStart", "active_hours_start"),
            ("activeHoursEnd", "active_hours_end"),
            ("advActiveHours", "active_hours_enabled")]

    @pytest.mark.parametrize("id_,键", 控件)
    def test_摆出来且读写都接上(self, id_, 键):
        """这条门钉的是本项目反复栽过的那件事：框在屏上、后端读都不读，
        或者只写不读——保存后重开看着像没生效。"""
        assert 'id="%s"' % id_ in HTML, "%s 没摆出来" % id_
        读回 = "setVal('%s'" % id_
        写回 = "setEl('%s'" % id_
        assert 读回 in HTML or 写回 in HTML, "%s 只写不读" % id_
        assert ("config.rate_limit." + 键) in HTML, "%s 保存时没写回" % 键

    def test_活跃时段那颗开关有说法(self):
        """toggleAdvBool 里没有这个 key 的话，点一下什么都不发生（界面装了个假开关）。"""
        assert 'key === "active_hours"' in HTML

    def test_节奏条随状态轮询刷新(self):
        段 = HTML[HTML.index("function refreshAccountStatus"):]
        assert "/api/pace" in 段[:900], "节奏条只在开机取一次，数字会停在旧值"

    def test_开机就取一次且跟着定时器走(self):
        """`refreshAccountStatus` 以前只挂在暂停/恢复那几个按钮回调里，
        开机不动、定时也不动——节奏条会一直停在"正在读取节奏…"，
        直到他点一次暂停才动。那是摆了个框但没人喂。"""
        assert re.search(r"setInterval\(function \(\) \{\s*try \{\s*refreshAccountStatus\(\);?\s*\}"
                         r"\s*catch\s*\(e\)\s*\{\s*\}\s*\},\s*20000\)", HTML), \
            "没接上 20 秒那趟车（另开一个 setInterval 会留下两套刷新节奏，也不要那样做）"
        段 = HTML[HTML.index("setInterval(loadMetrics, 20000);"):]
        assert "try { refreshAccountStatus(); } catch (e) {}" in 段[:900], "开机没取第一次"


# ── 6. 面板看得见（节奏条 + 线上那批数） ──────────────


class Test面板看得见:
    def test_接口在(self):
        assert "@app.route(\"/api/pace\")" in APP
        assert "pace.现状()" in APP, "接口自己重算一套数字，就会和机器用的对不上"

    def test_节奏条在且随状态刷新(self):
        assert 'id="paceStrip"' in HTML
        assert "/api/pace" in HTML
        assert "function pace渲染" in HTML
        段 = HTML[HTML.index("function refreshAccountStatus"):]
        assert "/api/pace" in 段[:600], "没跟着状态轮询走，节奏条会停在第一次的数"

    def test_读不到时明说而不是留着旧数(self):
        段 = HTML[HTML.index("function refreshAccountStatus"):]
        assert "catch" in 段[:900] and "读不到" in 段[:1200]

    def test_节奏条是活区且不单靠颜色(self):
        assert 'aria-live="polite"' in HTML and 'aria-label="请求节奏状态"' in HTML
        # 类名是 JS 拼出来的（'pace-chip' + ' warn'），所以两头都要钉：CSS 里有这三档，
        # 渲染里确实会拼出这三档——只钉拼完的串就成了打不到源码的假门
        for 档 in [".pace-chip.warn", ".pace-chip.stop", ".pace-chip.calm"]:
            assert 档 in HTML, "%s 那档样式没了" % 档
        段 = HTML[HTML.index("function pace渲染"):HTML.index("function refreshAccountStatus")]
        for 拼 in ["' stop'", "' warn'", "' calm'"]:
            assert 拼 in 段, "渲染里不会拼出 %s 那一档" % 拼

    def test_线上配置真的按用户点名的数装好(self):
        rl = CFG["rate_limit"]
        assert 15 <= rl["search_loads_per_hour"] <= 20, "搜索页要压进 15~20/小时"
        assert rl["chat_rounds_per_hour"] <= 60, "会话页轮次要 ≤60/小时"
        assert rl["search_loads_per_day"] <= 200
        assert rl["jitter_ratio"] > 0, "抖动被关掉就是等间隔，那正是模式匹配的靶子"
        assert rl["active_hours_enabled"] is True
        assert (rl["active_hours_start"], rl["active_hours_end"]) == (9, 18)
        assert rl["risk_cooldown_ladder"] == "300,900,3600"
        assert CFG["reply"]["max_replies_per_day"] > 0, "回复要有日额度"


class Test出门那道不扣额度:
    """轮子开头那些动作（排空勾选队列要开详情页、启动全量同步要逐个点会话）
    不属于任何一族额度。把它们塞进 `请求闸` 就是让"能不能做"顺手扣一格——那是假账。"""

    def test_可以出门不记账(self):
        pace.装限额(小时={"详情页": 2})
        现在 = time.time()
        白天 = _时刻(12)
        assert pace.可以出门(白天)[0] is True
        assert pace.现状(白天)["每类"]["详情页"]["小时已用"] == 0, "只是问一句，不该扣掉一格"
        pace.请求闸("详情页", 白天)
        assert pace.现状(白天)["每类"]["详情页"]["小时已用"] == 1

    def test_窗口外与熔断都算出不了门(self):
        白天 = _时刻(12)
        assert pace.可以出门(_时刻(3))[:2] == (False, "活跃时段外")
        pace.清零(); pace.命中风控(白天)
        能, 为什么, 等多久 = pace.可以出门(白天 + 60)
        assert not 能 and 为什么 == "熔断冷却中" and 等多久 > 0

    def test_两条腿的轮子开头都问了(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        # 定义那行是 `def _能不能出门(self, 侧...)`，匹配不上 `self._能不能出门(`，
        # 所以这里数到的就应该是两个调用点本身
        assert src.count("self._能不能出门(") == 2, "一侧的轮子开头没问，或问点被改没了"
        assert 'self._能不能出门("回复")' in src and 'self._能不能出门("打招呼")' in src

    def test_回复轮是先在门口问再跑(self):
        """顺序错了等于白问：跑完一轮才看闸，那一轮照样把请求发出去了。"""
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        段 = src[src.index('self._能不能出门("回复")'):]
        assert 段.index("self._run_reply_round()") > 0, "出门那道问在 _run_reply_round 之后"


# ── 配置序列化 ───────────────────────────────────────

class Test配置不漏:
    KEYS = ["search_loads_per_hour", "chat_rounds_per_hour", "detail_loads_per_hour",
            "search_loads_per_day", "chat_rounds_per_day", "detail_loads_per_day",
            "jitter_ratio", "active_hours_enabled", "active_hours_start",
            "active_hours_end", "risk_cooldown_ladder", "risk_strike_window"]

    def test_to_dict全带上(self):
        """少一条 = 他在界面上点一次保存就把这条设置抹了（10-10 那天抹过三次）。"""
        份 = UnifiedConfig().to_dict()["rate_limit"]
        缺 = [k for k in self.KEYS if k not in 份]
        assert not 缺, "这几项没进序列化：%s" % 缺

    def test_写出去读回来一模一样(self, tmp_path):
        cfg = UnifiedConfig()
        路径 = tmp_path / "bot_config.json"
        路径.write_text("{}", encoding="utf-8")
        cfg.save(str(路径))
        cfg._apply_bot_config({"rate_limit": {"search_loads_per_hour": 11,
                                              "jitter_ratio": 50,
                                              "active_hours_start": 8,
                                              "active_hours_end": 20,
                                              "risk_cooldown_ladder": "120,600"}})
        cfg.save(str(路径))
        回 = UnifiedConfig.load(str(路径))
        assert 回.greet.rate_limit.search_loads_per_hour == 11
        assert 回.greet.rate_limit.jitter_ratio == 50
        assert (回.greet.rate_limit.active_hours_start,
                回.greet.rate_limit.active_hours_end) == (8, 20)
        assert 回.greet.rate_limit.risk_cooldown_ladder == "120,600"

    def test_坏值退回而不是变成0(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"rate_limit": {"search_loads_per_hour": "很多",
                                              "jitter_ratio": -5}})
        assert cfg.greet.rate_limit.search_loads_per_hour == 18, "坏值不能当成 0（0=不限量）"
        assert cfg.greet.rate_limit.jitter_ratio == 0, "越界夹到下限是明确的，坏值退回默认"
