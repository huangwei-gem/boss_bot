# -*- coding: utf-8 -*-
"""2026-11-04 被 BOSS 按 IP 关进「访问受限 · 24 小时」之后装的四道闸。

现场（都是盘上现数，不是复盘时编的）：
· 10:00~14:59 每小时 **57~61 次**搜索整页加载 + 每小时 **255~316 轮**会话页
  （12 点 316 轮 = 平均 11 秒一趟，其中绝大多数是空轮）；
· 17 点开始 11 次风控命中，18 点归零——BOSS 回的那句是
  「您的 IP 存在异常行为，请登录使用 / 将于 24 小时后恢复正常 / 请勿频繁提交刷新请求」；
· 当天我还把搜索词 4→7、翻页 5→10，是在一个已经过载的节奏上又加了一层。

四道闸各自的判据只住一处，界面/日志/机器问的都是同一句：
① `page_handler.ip_blocked_evidence` —— 这一族**不是** captcha：页面里没有滑块，
   走 `_captcha_gate()` 就是每 2 秒复查一次去等"有人滑完"，而每趟复查都是一次新请求；
② `browser_launcher.准整页加载` 里的 IP 级小时账（进程共用一份，两号一起算）；
③ `main_loop._整页加载额度收手` —— 用完就一个词一个词地收手（不是停摆整轮）；
④ `main_loop._回复间隔秒` —— 空轮按 2 倍拉长，有活立刻回到基准。

每条都配反向哨兵：判据拆掉要红；写过头（把 ip_blocked 折成 unknown、
把退避写成永远回不到快档、0 被 `|| 默认` 吃掉）也要红。
"""
import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import browser_launcher as BL, page_handler as PH  # noqa: E402
from boss_bot.main_loop import UnifiedBotLoop  # noqa: E402
from boss_bot.unified_config import UnifiedConfig  # noqa: E402

HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
CFG = json.loads((ROOT / "bot_config.json").read_text(encoding="utf-8"))


# ── ① 这一族不是滑块 ──────────────────────────────────

class Test访问受限不是验证码:
    def test_两个词以上才算封(self):
        中, 为什么 = PH.ip_blocked_evidence(json.dumps({
            "boxes": [], "words": [], "blocked": ["访问受限", "存在异常行为"]}))
        assert 中 and "访问受限" in 为什么
        # 单一个词不算：「访问受限」这四个字在别的临时页上也会出现
        assert PH.ip_blocked_evidence(json.dumps(
            {"boxes": [], "words": [], "blocked": ["访问受限"]})) == (False, "")

    def test_探针真的把这几个词带回来了(self):
        """判据要能在线上响：探针没把 blocked 带回来，python 这一头永远拿不到。"""
        assert "blocked" in PH.CAPTCHA_PROBE_JS
        for 词 in PH.IP_BLOCK_WORDS:
            assert json.dumps(词, ensure_ascii=False) in PH.CAPTCHA_PROBE_JS, 词
        中, _ = PH.ip_blocked_evidence(PH.CAPTCHA_PROBE_JS and json.dumps(
            {"boxes": [], "words": [], "blocked": [PH.IP_BLOCK_WORDS[0], PH.IP_BLOCK_WORDS[2]]}))
        assert 中

    def test_封禁优先于验证码(self):
        """这一族页面上常同时印着「请登录使用」，还会盖一层验证框。
        判成 captcha 就会去等人工滑，而它根本没有可滑的东西。"""
        探针 = json.dumps({"boxes": [{"sel": "#tcaptcha", "w": 300, "h": 200,
                                     "display": "block", "visibility": "visible"}],
                          "words": ["安全验证"],
                          "blocked": ["访问受限", "请勿频繁提交刷新请求"]})
        assert PH.classify_health("https://www.zhipin.com/web/geek/jobs", 探针) == "ip_blocked"

    def test_只有滑块时仍判captcha(self):
        探针 = json.dumps({"boxes": [{"sel": "#tcaptcha", "w": 300, "h": 200,
                                     "display": "block", "visibility": "visible"}],
                          "words": ["安全验证"], "blocked": []})
        assert PH.classify_health("https://www.zhipin.com/web/geek/jobs", 探针) == "captcha"

    def test_读不到探针不兜底成健康(self):
        assert PH.ip_blocked_evidence(None) == (False, "")
        assert PH.ip_blocked_evidence("不是 JSON") == (False, "")


# ── ② IP 级小时账 ────────────────────────────────────

@pytest.fixture(autouse=True)
def _清账():
    BL.reset_page_load_throttle()
    BL._SHARED_PAGE_LOAD_STATE["小时表"] = []
    BL._SHARED_PAGE_LOAD_STATE["小时上限"] = 0
    yield
    BL._SHARED_PAGE_LOAD_STATE["小时表"] = []
    BL._SHARED_PAGE_LOAD_STATE["小时上限"] = 0


class Test小时账:
    def test_装上限与夹范围(self):
        assert BL.设置整页加载小时上限(20) == 20
        assert BL.设置整页加载小时上限("abc") == 0
        assert BL.设置整页加载小时上限(-5) == 0
        assert BL.设置整页加载小时上限(9999) == 200, "再高就不是限流，是个摆设"

    def test_到量就报超(self):
        BL.设置整页加载小时上限(3)
        for i in range(3):
            BL.准整页加载("词%d" % i)
        超, 数, 等多久 = BL.整页加载额度用完了()
        assert 超 and 数 == 3 and 等多久 > 0

    def test_没装上限永远不超(self):
        BL.设置整页加载小时上限(0)
        for i in range(50):
            BL.准整页加载("词%d" % i)
        assert BL.整页加载额度用完了()[0] is False, "填 0 = 不限量，不该拦任何一发"

    def test_两号共用一份账(self):
        """风控记的是出口 IP，不是账号：按号分两份账就等于没装。"""
        BL.设置整页加载小时上限(2)
        BL.准整页加载("号0 的搜索")
        BL.准整页加载("号1 的搜索")
        assert BL.整页加载额度用完了()[0] is True

    def test_一小时前的记录滑出去(self):
        BL.设置整页加载小时上限(2)
        BL.准整页加载("a"); BL.准整页加载("b")
        assert BL.整页加载额度用完了()[0] is True
        未来 = __import__("time").time() + 3601
        超, 数, _ = BL.整页加载额度用完了(现在=未来)
        assert 超 is False and 数 == 0, "窗口不滑走的话，一天到晚都在说自己超额"

    def test_记账不改导航结果(self):
        """这一族以前有过一次事故：记账顺手把地板改成 0，两条腿当场冻死。"""
        BL.设置整页加载地板(3)
        BL.设置整页加载小时上限(5)
        BL.准整页加载("a")
        assert BL.整页加载地板秒() == 3


# ── ④ main_loop 那两道收手 ──────────────────────────

def _桩loop(**盖):
    cfg = UnifiedConfig()
    cfg.greet.rate_limit.page_loads_per_hour = 盖.get("小时上限", 20)
    cfg.reply.check_interval = 盖.get("基准", 15)
    cfg.reply.idle_backoff_max = 盖.get("退避顶", 120)
    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp.config = cfg
    lp._logs = []
    lp._log = lambda 级别, 话: lp._logs.append((级别, str(话)))
    lp._notifier = 盖.get("通知", None)
    lp._greet_paused = False
    lp._reply_paused = False
    lp._greet_paused_by_cap = False
    lp._greet_auto_resume_at = 0.0
    lp._greet_engine = None
    lp._state_store = None
    lp._ip封着 = False
    lp._额度说过 = False
    lp._空轮连击 = 0
    lp._本轮回复有活 = True
    lp._login_reason = ""
    lp._stop_event = __import__("threading").Event()
    lp._健康判据 = ""
    return lp


class Test额度收手:
    def test_用完就收手并且只说一遍(self):
        BL.设置整页加载小时上限(2)
        BL.准整页加载("a"); BL.准整页加载("b")
        lp = _桩loop()

        def 记(自我, 级别, 话):
            lp._logs.append((级别, str(话)))

        with patch.object(UnifiedBotLoop, "_log", 记),                 patch.object(lp._stop_event, "wait", lambda timeout=0: False):
            assert lp._整页加载额度收手("打招呼") is True
            第一次 = len([1 for 别, 话 in lp._logs if "额度用完" in 话])
            assert lp._整页加载额度收手("打招呼") is True
        说过的 = [话 for 别, 话 in lp._logs if "额度用完" in 话]
        assert len(说过的) == 第一次 == 1, "一分钟一趟、每趟两号，不说就会淹掉日志"

    def test_没到量就不收手(self):
        BL.设置整页加载小时上限(5)
        BL.准整页加载("a")
        lp = _桩loop()
        with patch.object(lp._stop_event, "wait", lambda timeout=0: False):
            assert lp._整页加载额度收手("打招呼") is False

    def test_额度从配置装进去(self):
        """配置改了不装闸 = 改了没生效（这条项目里为地板踩过一次）。"""
        lp = _桩loop(小时上限=12)
        from boss_bot.main_loop import 设置整页加载小时上限
        assert 设置整页加载小时上限(
            lp.config.greet.rate_limit.page_loads_per_hour) == 12


class Test封了就停手:
    def test_两侧一起停并通知(self):
        记 = []
        通知 = type("N", (), {"send_notification":
                             lambda 自我, a, b, level="info": 记.append((a, level)) or True})()
        lp = _桩loop(通知=通知)
        lp._ip封了停下("打招呼", "命中：访问受限、请勿频繁提交刷新请求")
        assert lp._greet_paused is True and lp._reply_paused is True
        assert lp._ip封着 is True and lp._login_reason == "ip_blocked"
        assert 记 and 记[0][1] == "error"
        assert any("🚫" in 话 for 别, 话 in lp._logs)

    def test_不装自动恢复时刻(self):
        """这一族的恢复时间只有 BOSS 知道；机器到点自己冲回去等于重开一次封禁周期。"""
        lp = _桩loop()
        lp._ip封了停下("回复", "访问受限")
        assert lp._greet_auto_resume_at == 0.0

    def test_重复调用只停一次(self):
        记 = []
        通知 = type("N", (), {"send_notification":
                             lambda 自我, a, b, level="info": 记.append(a) or True})()
        lp = _桩loop(通知=通知)
        lp._ip封了停下("打招呼", "x")
        lp._ip封了停下("回复", "x")
        assert len(记) == 1, "两条腿都读到封禁时不该发两遍通知"

    def test_人工恢复才算解开(self):
        lp = _桩loop()
        lp._ip封了停下("打招呼", "x")
        lp.resume_greet()
        assert lp._ip封着 is False and lp._login_reason == ""
        lp._ip封了停下("回复", "x")
        lp.resume_reply()
        assert lp._ip封着 is False

    def test_界面有这个原因的说法(self):
        """tests/test_login_uncertain_visible.py 逐个数过：后端新增一个 login_reason，
        界面上少了说法，前端就只剩一个黄点说不清要他做什么。"""
        assert "ip_blocked" in HTML
        段 = HTML[HTML.index("var LOGIN_REASON_LABEL"):]
        assert "ip_blocked" in 段[:800], "说法没写进那张表"


class Test空轮退避:
    def test_有活就是基准秒(self):
        lp = _桩loop(基准=15)
        lp._本轮回复有活 = True
        assert lp._回复间隔秒() == 15

    def test_空轮按2倍拉长到顶(self):
        lp = _桩loop(基准=15, 退避顶=120)
        lp._本轮回复有活 = False
        串 = [lp._回复间隔秒() for _ in range(6)]
        assert 串[:4] == [30, 60, 120, 120], 串
        assert max(串) <= 120, "退避不能把回复拖到几分钟以上——真提问要接得住"

    def test_来活立刻回到基准(self):
        lp = _桩loop(基准=15, 退避顶=120)
        lp._本轮回复有活 = False
        lp._回复间隔秒(); lp._回复间隔秒()
        lp._本轮回复有活 = True
        assert lp._回复间隔秒() == 15, "退到 60 秒后 HR 说话了还要等一分钟，那就是漏回复"
        lp._本轮回复有活 = False
        assert lp._回复间隔秒() == 30, "计数器没清零，下一次直接跳到 120"

    def test_填0就是关掉退避(self):
        lp = _桩loop(基准=15, 退避顶=0)
        lp._本轮回复有活 = False
        assert lp._回复间隔秒() == 15, "0 是有效值，不能被当成没填再兜回 120"

    def test_坏配置不炸(self):
        lp = _桩loop()
        lp.config.reply.check_interval = "很快"
        assert lp._回复间隔秒() >= 1.0


# ── 配置与界面接线 ────────────────────────────────────

class Test配置与界面:
    def test_两个键解析与序列化(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"rate_limit": {"page_loads_per_hour": 35},
                               "reply": {"idle_backoff_max": 0}})
        assert cfg.greet.rate_limit.page_loads_per_hour == 35
        assert cfg.reply.idle_backoff_max == 0, "0 被 `or 默认` 吃掉就是关掉退避没生效"
        份 = cfg.to_dict()
        assert 份["rate_limit"]["page_loads_per_hour"] == 35
        assert 份["reply"]["idle_backoff_max"] == 0

    def test_越界被夹住(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"rate_limit": {"page_loads_per_hour": 5000},
                               "reply": {"idle_backoff_max": -10}})
        assert cfg.greet.rate_limit.page_loads_per_hour == 200
        assert cfg.reply.idle_backoff_max == 0

    @pytest.mark.parametrize("id_,键", [("pageLoadsPerHour", "page_loads_per_hour"),
                                        ("advIdleBackoff", "idle_backoff_max")])
    def test_控件三端都接上了(self, id_, 键):
        assert 'id="%s"' % id_ in HTML, "输入框没摆出来"
        assert ("setVal('%s'" % id_) in HTML, "只写不读：保存后重开看着像没生效"
        assert (键 in HTML), "保存时没写回配置"

    def test_线上配置已经把两道闸装上(self):
        # 15 不是 20：同一天稍后把天花板又压了一档（用户点名"搜索页控制在每小时 15~20 以内"），
        # 分类别的那几道闸在 tests/test_pace_governor.py 里钉
        assert CFG["rate_limit"]["page_loads_per_hour"] == 15
        assert CFG["rate_limit"]["page_load_min_interval"] == 5
        assert CFG["reply"]["idle_backoff_max"] == 120

    def test_搜索词按当天产量收过(self):
        """11-04 现数：号1「数据产品经理」出 8 条 ≥90，「商业数据分析/数据分析专家」0 条；
        号0「AI运营/AI产品」各 1 条且与「AI产品经理」重复。收词这个决定要留得住。"""
        for a in CFG["accounts"]:
            词 = [j["query"] for j in a["jobs"]]
            assert len(词) == 5, "%s 的搜索词是 %s" % (a["name"], 词)
            assert len(set(词)) == 5, "有重复的词"
            for j in a["jobs"]:
                assert j["scroll_pages"] == 6, "翻页 10→6 被改回去了（今天 1810 次滚动一半是空滚）"
                assert j["job_type"] == "全职"
        assert [j["query"] for j in CFG["accounts"][1]["jobs"]][0] == "数据产品经理"
