# -*- coding: utf-8 -*-
"""普工/进厂、主播、快递、保洁这一类：每一端都要拒掉，不许点同意。

用户原话（2026-10-07 上午）：「这种普工进厂的岗位为啥没还统一啊，还有主播，
快递保洁这些你别同意，直接拒绝就行。」

盘上取证（data/reply_records.json，全是今天真实发生的）：
  04:19 同意交换  暑假短期过渡  桶装水配送 无需装卸8-9K
  04:33 同意交换  店员直招/可短期过渡/可住宿/可短期6-7K
  07:56 同意交换  kfc配送提供车住吃可周结7-9K
  08:36 同意交换  主包|语音互动播|居家不露脸| 小白可带5-10K
  09:25 同意交换  快手居家线上不露脸直播兼职8-12K
  09:54 同意交换  长白班普工包吃住18元一小时（代招职位）
  08:10 AI 回复  长沙蓝思直招普工：「好的，我来添加您的微信号，关于两班倒…」
  04:40 AI 回复  桶装水配送：「好的，稍后我加您微信，期待进一步沟通。」
这些岗位在 greet_records 里一条都没有——不是我们投的，是 HR 主动找上门的；
而回复侧和卡片侧从前一道闸门都没有：HR 发卡片就点同意，HR 说话 AI 就接着聊。

岗位类型词必须**只查标题**：这些词写进 JD 正文到处都是（"标注快递物流场景的录音"
"直播间语音切片"），照着正文杀就误杀他真正要的方向；而标题里带"直播/快递"也常常
仍是他要的（"直播运营助理""电商客服"），所以还要一张职业白名单。
"""
import inspect
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from boss_bot.greet_engine import (AIAnalyzerChain, jd_gate, title_veto_hit,
                                   veto_keyword_hit)
from boss_bot.intent import is_contact_exchange_card
from boss_bot.unified_config import (TITLE_VETO_EXEMPT_KEYWORDS,
                                     TITLE_VETO_KEYWORDS_DEFAULT, UnifiedConfig)

ROOT = Path(__file__).resolve().parent.parent

# 今天真在盘上出现过的岗，一律不许投、不许交换联系方式
蓝领标题 = [
    "长白班普工包吃住18元一小时（代招职位）5500-6500元/月长沙查看职位",
    "长沙蓝思直招普工7.5k（岗位不限经验）5500-7500元/月长沙查看职位",
    "暑假短期过渡  桶装水配送 无需装卸8-9K长沙查看职位",
    "kfc配送提供车住吃可周结7-9K长沙查看职位",
    "店员直招/可短期过渡/可预知/可住宿/可短期6-7K长沙查看职位",
    "主包|语音互动播|居家不露脸| 小白可带5-10K长沙查看职位",
    "快手居家线上不露脸直播兼职8-12K长沙查看职位",
    "急招（在家就行）哔哩哔哩兼职不露脸主播9-10K长沙查看职位",
    "足疗按摩师11-18K长沙查看职位",
    "小区保洁主管（可短期）4-6K长沙查看职位",
    "快递分拣打包日结200/天长沙查看职位",
    "美团骑手站点直招+提供电动车+新人奖励5-15元/单长沙查看职位",
]

# 他点名要的方向：数据标注/数据分析/线上运营/电商客服/游戏代练，一个都不许误伤
要的标题 = [
    "兼职·数据标注专员20-30元/时徐州查看职位",
    "兼职·数据分析师（可线上工作）1000-6000元/月临沂查看职位",
    "直播运营助理（线上远程）3-6K长沙查看职位",
    "电商客服（兼职可居家办公）5-8K长沙查看职位",
    "游戏代练/打手线上接单日结100-300元长沙查看职位",
    "短视频剪辑线上新手可学150-300元/天长沙查看职位",
]

CARD_WECHAT = "我想要和您交换微信，您是否同意 拒绝 同意"
DECLINED_MARK = "[已拒绝交换联系方式]"
OUR_DECLINE = ("您好，感谢您的介绍。我这边只找线上远程就能做的兼职，"
               "这类岗位不考虑，就不耽误您时间了~")


class 岗位类型判据Test:
    """判据只查标题，并且要让开他点名的职业。"""

    @pytest.mark.parametrize("title", 蓝领标题)
    def test_这一类标题全部命中(self, title):
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, title), title

    @pytest.mark.parametrize("title", 要的标题)
    def test_要的岗位一个都不误伤(self, title):
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, title) == "", title

    def test_白名单里不许放线上居家这类修饰词(self):
        """每个兼职标题都写"线上/居家/兼职"，把它们当白名单等于白名单失效。"""
        for word in ("线上", "居家", "远程", "兼职", "实习", "可短期"):
            assert word not in TITLE_VETO_EXEMPT_KEYWORDS, word

    def test_只查标题不查正文(self):
        """JD 正文里"快递/直播"到处都是（标注快递场景、直播间素材），不能顺手杀掉。"""
        job = {"job_name": "数据标注专员", "description": "标注快递物流场景的录音",
               "jd_description": "包含直播间的语音切片"}
        assert veto_keyword_hit(["主播"], job,
                                title_keywords=TITLE_VETO_KEYWORDS_DEFAULT) == ""

    def test_标题命中时veto_keyword_hit也要拦(self):
        job = {"job_name": "长白班普工包吃住18元一小时"}
        assert veto_keyword_hit([], job,
                                title_keywords=TITLE_VETO_KEYWORDS_DEFAULT) == "普工"


class 投递侧Test:
    def _链(self, monkeypatch, title_veto=None):
        chain = AIAnalyzerChain(
            providers=[{"name": "测试家-投递", "api_key": "k",
                        "api_base": "https://x.invalid/v1", "model": "m"}],
            title_veto_keywords=title_veto,
        )
        calls = []

        def _probe(req, timeout=None):
            calls.append(timeout)
            raise OSError("故意断开，只数有没有真的发请求")

        monkeypatch.setattr("boss_bot.greet_engine.urlopen", _probe)
        return chain, calls

    def test_命中岗位类型词就一次请求都不发(self, monkeypatch):
        chain, calls = self._链(monkeypatch)
        got = chain.analyze_job({"job_name": "长白班普工包吃住18元一小时",
                                 "description": "流水线包装", "url": "u"})
        assert got["veto_hit"] == "普工", got
        assert got["is_match"] is False
        assert calls == [], "命中了还去问模型，既花钱又慢"

    def test_词表清空就不拦正常岗位(self, monkeypatch):
        chain, calls = self._链(monkeypatch, title_veto=[])
        got = chain.analyze_job({"job_name": "数据标注专员", "url": "u"})
        assert got.get("veto_hit", "") == ""
        assert calls != [], "清空了词表还拦着，就是反方向的改了不生效"

    def test_JD闸门同样认标题(self):
        """详情页拿回来之后的第二道闸门（点「立即沟通」之前）不能漏掉这一类。"""
        job = {"job_name": "小区保洁主管（可短期）", "jd_description": "负责排班" * 20}
        assert "保洁" in jd_gate([], job, title_keywords=TITLE_VETO_KEYWORDS_DEFAULT)

    def test_容灾链收这个参数(self):
        src = inspect.getsource(AIAnalyzerChain.__init__)
        assert "title_veto_keywords" in src

    def test_配置改动会重建容灾链(self):
        import boss_bot.greet_engine as ge
        src = inspect.getsource(ge.GreetEngine._apply_ai_config)
        assert "title_veto_keywords" in src, "面板改了岗位类型词，运行中的引擎要重建才听"


class 配置Test:
    def test_默认表覆盖用户点名的家族(self):
        joined = "".join(TITLE_VETO_KEYWORDS_DEFAULT)
        for word in ("普工", "进厂", "工厂", "车间", "流水线", "主播", "直播",
                     "快递", "骑手", "配送", "分拣", "保洁", "店员", "保安"):
            assert word in joined, word

    def test_配置里带着这个字段(self):
        cfg = UnifiedConfig()
        assert cfg.ai.title_veto_keywords == TITLE_VETO_KEYWORDS_DEFAULT
        assert "title_veto_keywords" in cfg.to_dict()["ai"]

    def test_面板改过的值读得回来(self):
        saved = UnifiedConfig().to_dict()
        saved["ai"]["title_veto_keywords"] = ["主播"]
        cfg = UnifiedConfig()
        cfg._apply_bot_config(saved)
        assert cfg.ai.title_veto_keywords == ["主播"], "被默认值吞掉就是改了不生效"

    def test_按号覆盖也认这个键(self):
        base = UnifiedConfig()
        base.greet.accounts[0].settings = {"ai": {"title_veto_keywords": ["骑手"]}}
        assert base.apply_account(0).ai.title_veto_keywords == ["骑手"]
        assert base.ai.title_veto_keywords == TITLE_VETO_KEYWORDS_DEFAULT, "覆盖不能漏到全局基准"

    def test_中文逗号一格也要拆开(self):
        base = UnifiedConfig()
        base._apply_bot_config({"ai": {"title_veto_keywords": "主播，保洁"}})
        assert base.ai.title_veto_keywords == ["主播", "保洁"]


class 面板Test:
    @pytest.fixture(scope="class")
    def html(self):
        return (ROOT / "flask-version" / "templates" / "index.html").read_text(
            encoding="utf-8")

    def test_控件回填收集三处都在(self, html):
        assert 'id="aiTitleVetoKeywords"' in html
        assert "setVal('aiTitleVetoKeywords'" in html
        assert "title_veto_keywords" in html

    def test_默认表只有一份(self, html):
        """默认值只能有一份：回填处不许写 `|| ["普工", …]` 这种副本，
        否则盘上的真值和页面各说一套，「恢复默认」还会把削弱版写回配置（这条踩过）。
        输入框的示例文字（placeholder）不算副本——它不写进配置。"""
        flat = html.replace(" ", "").replace("\n", "")
        # 回填时 || 后面只许是空数组：写一份词表进去就是第二套默认值
        for bad in ('title_veto_keywords||["', "title_veto_keywords||['"):
            assert bad not in flat, f"页面里抄了一份默认表：{bad}"
        assert "setVal('aiTitleVetoKeywords',(config.ai.title_veto_keywords||[]).join" in flat


    def test_盘上出现过的来源都得有中文名(self, html):
        """用户在界面上看到的必须是中文标签，不是 scam_filter / policy 这种内部键。

        2026-10-07 发现 policy（线下面试拒绝，盘上 22 条）没有登记；这条锁把整类问题
        钉住：以后新增 reply_source 忘了配中文名，测试就红。
        """
        import json
        rows = json.loads((ROOT / "data" / "reply_records.json").read_text(encoding="utf-8"))
        rows = rows if isinstance(rows, list) else (rows.get("records") or [])
        for src in sorted({r.get("reply_source") for r in rows if r.get("reply_source")}):
            assert f"replySource === '{src}'" in html, f"{src} 没有中文名，界面会印内部键"

    def test_拒绝类来源都在筛选下拉里(self, html):
        """筛不到就等于没显示：拒绝的三类都得是下拉里的一个选项。"""
        for src, label in (("family_filter", "岗位类型拒绝"),
                           ("reject_contact", "拒绝交换"),
                           ("policy", "线下面试拒绝")):
            assert f'<option value="{src}">{label}</option>' in html, f"{src} 不在筛选里"


class 回复侧Test:
    def _引擎(self):
        from boss_bot.reply_engine import ReplyEngine
        eng = ReplyEngine()
        eng._record_store = SimpleNamespace(add=lambda *a, **k: None)
        return eng

    def test_普工会话里HR再说话要拒绝而不是接着聊(self):
        eng = self._引擎()
        action, content, meta = eng.get_reply(
            "你好，有兴趣聊聊吗？", job_name="长白班普工包吃住18元一小时（代招职位）")
        assert action == "text", f"普工岗还走正常回复链：{action} {content}"
        assert "只找线上" in content, content
        assert meta["source"] == "family_filter"

    def test_AI不再替我们应下进厂的活(self):
        """08:10 那条「好的，我来添加您的微信号」就是 AI 自己接的话。"""
        eng = self._引擎()
        action, content, meta = eng.get_reply(
            "两班倒，一个月倒一次可以接受吗？",
            job_name="长沙蓝思直招普工7.5k（岗位不限经验）")
        assert action == "text" and meta["source"] == "family_filter"
        assert "加您" not in (content or "") and "添加您" not in (content or "")

    def test_要的岗位照常走原链(self):
        eng = self._引擎()
        action, content, meta = eng.get_reply(
            "方便加个微信聊吗？", job_name="兼职·数据标注专员20-30元/时徐州查看职位")
        assert meta["source"] != "family_filter"

    def test_已经拒绝过一次就别再复读(self):
        eng = self._引擎()
        history = [
            {"text": "你好，有兴趣聊聊吗？", "is_mine": False,
             "time": "2026-10-07 09:00:00"},
            {"text": OUR_DECLINE, "is_mine": True, "time": "2026-10-07 09:00:30"},
            {"text": "为什么不考虑？", "is_mine": False, "time": "2026-10-07 09:30:00"},
        ]
        action, content, meta = eng.get_reply(
            history, job_name="长白班普工包吃住18元一小时")
        assert action == "none", f"同一会话反复发拒绝话术就是骚扰：{content}"

    def test_岗位类型要让位给面试拒绝(self):
        """普工岗发线下面试邀请时，要走点「拒绝」那条——只回一句文字，面试还挂在 HR 那边。

        这一步排在面试策略之后是有原因的：见 tests/test_interview_invite_policy.py
        那条"策略要跑在关键词直通之前"的同一类顺序坑。
        """
        eng = self._引擎()
        action, content, meta = eng.get_reply(
            "邀请您现场面试，前往查看，确认是否接受",
            job_name="长白班普工包吃住18元一小时（代招职位）")
        assert action == "reject_interview", f"没去点平台上的「拒绝」：{action} {content}"

    def test_卡片不当成话去回(self):
        """卡片交给点「拒绝」那条路，文字回复会把两件事都办一遍。"""
        eng = self._引擎()
        assert is_contact_exchange_card(CARD_WECHAT)
        action, content, meta = eng.get_reply(
            CARD_WECHAT, job_name="长白班普工包吃住18元一小时")
        assert action == "contact", "卡片要留给点按钮那条分支"


class 点拒绝Test:
    def test_JS只点卡片内部那枚拒绝(self):
        from boss_bot.page_handler import BossChatHandler
        src = inspect.getsource(BossChatHandler.decline_contact_exchange)
        assert ".message-card-wrap" in src
        assert ".message-card-top-title" in src
        assert ".message-card-buttons .card-btn" in src
        assert '=== "拒绝"' in src, "按钮文字必须逐字对上，别模糊匹配点到「同意」"
        assert "getClientRects" in src
        assert 'indexOf("简历") >= 0' in src, "简历那张也带「是否同意」，不许顺手拒绝"

    def test_结果映射(self):
        from boss_bot.page_handler import BossChatHandler

        class _Page:
            def __init__(self, ret):
                self._ret = ret

            def run_js(self, *a, **k):
                return self._ret

        for ret, expect in [("clicked", True), ("no-card", False),
                            ("no-reject-btn", False), ("", False)]:
            assert BossChatHandler.decline_contact_exchange(
                SimpleNamespace(page=_Page(ret))) is expect, ret

    def test_JS抛异常当没点到(self):
        from boss_bot.page_handler import BossChatHandler

        class _Boom:
            def run_js(self, *a, **k):
                raise RuntimeError("与页面的连接已断开")

        assert BossChatHandler.decline_contact_exchange(
            SimpleNamespace(page=_Boom())) is False


class 卡片闸门Test:
    """命中岗位类型词的卡片：点拒绝，绝不点同意。"""

    def _loop(self, *, decline_ok=True, dry_run=False, accept_off=False):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp.config = SimpleNamespace(
            dry_run=dry_run,
            ai=SimpleNamespace(title_veto_keywords=list(TITLE_VETO_KEYWORDS_DEFAULT),
                               custom_filter_keywords=["包吃住"]),
            reply=SimpleNamespace(accept_contact_exchange=not accept_off))
        lp.records, lp.events, lp.bots, lp.logs = [], [], [], []
        lp.sent = []
        lp.accepted, lp.declined = 0, 0

        def _accept():
            lp.accepted += 1
            return True

        def _decline():
            lp.declined += 1
            return decline_ok

        lp._reply_engine = SimpleNamespace(
            wait_human_delay=lambda: None,
            _add_record=lambda **kw: lp.records.append(kw), _last_ai_model="",
            record_reply=lambda: None, _reply_count=0)
        lp._chat_handler = SimpleNamespace(
            accept_contact_exchange=_accept, decline_contact_exchange=_decline,
            send_text=lambda t: lp.sent.append(t) or True)
        lp._msg_store = SimpleNamespace(
            append_bot_message=lambda *a, **k: lp.bots.append(k))
        lp._stats = SimpleNamespace(record_reply=lambda **k: None)
        lp._emit_reply_event = lambda **kw: lp.events.append(kw)
        lp._log = lambda level, msg: lp.logs.append((level, msg))
        return lp

    def _call(self, lp, job_name="长白班普工包吃住18元一小时", msg=CARD_WECHAT):
        return lp._handle_reply_action(
            "contact", None, {"intent": "contact_request", "source": "intent"},
            "梁玲", job_name, msg, chat_company="某某人力资源")

    def test_普工岗点拒绝不点同意(self):
        lp = self._loop()
        assert self._call(lp) is True
        assert lp.declined == 1 and lp.accepted == 0

    def test_主播岗点拒绝(self):
        lp = self._loop()
        assert self._call(
            lp, job_name="主包|语音互动播|居家不露脸| 小白可带5-10K长沙") is True
        assert lp.declined == 1 and lp.accepted == 0

    def test_消息正文命中否决词也算(self):
        """HR 自己写"进厂包吃住"，标题再干净也别把号码交出去。"""
        lp = self._loop()
        assert self._call(
            lp, job_name="线上助理",
            msg="我想要和您交换微信，您是否同意 我们这边进厂包吃住 拒绝 同意") is True
        assert lp.declined == 1 and lp.accepted == 0

    def test_要的岗位照常点同意(self):
        lp = self._loop()
        assert self._call(lp, job_name="兼职·数据标注专员20-30元/时") is True
        assert lp.accepted == 1 and lp.declined == 0

    def test_拒绝成功要记成已拒绝(self):
        lp = self._loop()
        self._call(lp)
        rec = lp.records[-1]
        assert rec["reply_content"] == DECLINED_MARK
        assert rec["reply_source"] == "reject_contact"
        assert rec.get("is_skipped") is None
        assert lp.events[-1]["status"] == "replied"
        assert lp.sent == [], "点了按钮就别再发一句话"

    def test_点不到拒绝就只留痕不许追发文字(self):
        lp = self._loop(decline_ok=False)
        assert self._call(lp) is True
        assert lp.records[-1]["is_skipped"] is True
        assert lp.sent == []
        assert all(r["reply_content"] != DECLINED_MARK for r in lp.records)

    def test_演练模式一次都不点(self):
        lp = self._loop(dry_run=True)
        assert self._call(lp) is False
        assert lp.accepted == 0 and lp.declined == 0

    def test_总开关关着仍旧留给人工(self):
        lp = self._loop(accept_off=True)
        assert self._call(lp) is True
        assert lp.accepted == 0 and lp.declined == 0, "关了自动交换就不该碰任何按钮"

    def test_补点工具走同一道闸门(self):
        """tools/accept_pending_contacts.py 一跑就把这批号全交出去，必须同判据。"""
        src = (ROOT / "tools" / "accept_pending_contacts.py").read_text(encoding="utf-8")
        assert "veto_hit_anywhere" in src
        assert "decline_contact_exchange" in src


class 标题也过否决词Test:
    """11:57:47 真机上有一条「【白班坐岗】28/H包吃住+可预支+不体检（派遣职位」被主动追问。

    包吃住/需坐班/到店 这些原本只算"正文词"，可它们写进标题就是这份工本身的形状，
    比正文里一句顺带一提硬得多——所以回复侧、卡片侧、跟进侧对标题要过两遍表。
    打招呼那侧不用改：jd_gate 本来就查 job_name。
    """

    def test_判据函数命中标题里的正文词(self):
        from boss_bot.intent import veto_hit_anywhere
        hit = veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["包吃住", "需坐班"],
                                title="可预支+不体检+包吃住28/H（职位）")
        assert hit == "包吃住", hit

    def test_否决词命中HR这句话照样拦(self):
        """标题挂着"数据标注"，HR 自己说"这边要坐班包吃住"——号码不能交出去。

        这一遍查正文不是新加的：卡片闸门那条用例（HR 写"我们这边进厂包吃住"）
        一直就靠它，四个出口必须同一套判据，否则会出现"卡片拒了、文字还在聊"。
        """
        from boss_bot.intent import veto_hit_anywhere
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["包吃住", "需坐班"],
                                 title="数据标注专员",
                                 text="这个岗位需坐班，包吃住") == "包吃住"
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["包吃住"],
                                 title="数据标注专员", text="标注做好就行") == ""

    def test_跟进候选认标题里的正文词(self):
        from boss_bot.reply_queue import worth_following_up
        chat = {"chat_name": "先生", "company": "某某派遣",
                "job_name": "【白班坐岗】28/H包吃住+可预支+不体检（派遣职位）",
                "messages": [{"text": "可以来聊聊吗", "is_mine": False,
                              "time": "2026-10-07 09:00:00"}]}
        assert worth_following_up(chat, TITLE_VETO_KEYWORDS_DEFAULT, ["包吃住"]) is False

    def test_白班坐岗这种会话回复就是拒绝(self):
        from boss_bot.reply_engine import ReplyEngine
        eng = ReplyEngine()
        eng._record_store = SimpleNamespace(add=lambda *a, **k: None)
        eng._body_veto_keywords = ["包吃住"]
        action, content, meta = eng.get_reply(
            "可以做吗？", job_name="【白班坐岗】28/H包吃住+可预支（派遣职位）")
        assert action == "text" and meta["source"] == "family_filter", (action, content)


class 否定式Test:
    """HR 写"（线上）不坐班""无需坐班"是在保证没有那条限制，不是在提那条限制。

    盘上 1416 个标题里「坐班」命中 14 处，9 处是否定式，且这 9 个全是用户点名要的
    那一类（线上短视频剪辑、线上私域运营、远程办公/不坐班分析师、线上塔罗师、
    线上学伴师）。"非中介"不算否定式：那是派遣/工厂岗爱挂的卖点，照旧得拦。
    """

    要的标题 = [
        "兼职·短视频剪辑（线上不坐班）120-300元/天石家庄查看职位",
        "兼职·线上私域运营不坐班可兼职120-200元/天",
        "数据分析师(远程办公/不坐班)500-800元/天",
        "兼职·线上塔罗师（不坐班，可远程）71-211元/时",
    ]

    def test_判据函数认否定式(self):
        from boss_bot.intent import veto_hit_anywhere
        for title in self.要的标题:
            assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["坐班", "包吃住"],
                                     title=title, text="") == "", title

    def test_普通命中照旧(self):
        from boss_bot.intent import veto_hit_anywhere
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["坐班"],
                                 title="法定节假日带薪休 坐班审核岗9-12K") == "坐班"

    def test_非中介不当否定式(self):
        """派遣/工厂岗最爱写"直招非中介"，这条不能因为前面有个"非"就放过。"""
        from boss_bot.intent import veto_hit_anywhere
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["中介"],
                                 title="长沙蓝思直招非中介/五险一金+吃住7K") == "中介"

    def test_无需坐班这种写法也算否定(self):
        from boss_bot.intent import veto_hit_anywhere
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["需坐班", "坐班"],
                                 text="形式线上居家办公，无需坐班，每天三小时以上") == ""
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["坐班"],
                                 text="时间自主安排，不需要坐班，也不用打卡") == ""

    def test_小句开头的否定管到整句(self):
        """"全程远程协作，无需到公司坐班"——否定词离"坐班"隔了四个字，也得认。"""
        from boss_bot.intent import veto_hit_anywhere
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["坐班"],
                                 text="全程远程协作，无需到公司坐班。薪资按实际单量结算") == ""
        # 同一句里另一小句真的要坐班，照样命中
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["坐班"],
                                 text="无需打卡，但周末要坐班") == "坐班"

    def test_否定窗口不能宽到把主播放回来(self):
        """"不露脸主播"里的"不"隔了两个字——那还是要拒的那一类。"""
        from boss_bot.intent import veto_hit_anywhere
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["主播", "不露脸"],
                                 title="急招（在家就行）哔哩哔哩兼职不露脸主播") != ""
        assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, ["住宿"],
                                 title="直招·无押金提供住宿7-8K长沙查看职位") == "住宿"

    def test_投递侧同一口径(self):
        """否决词查岗位全文那一遍（jd_gate）也得认否定式，不然四端又漂开。"""
        远程JD = ("线上远程，按件结算，时间自主安排。工作内容是把公司给的素材剪成"
                  "竖版短视频，一条三到五分钟，剪完上传到我们的后台就行，不需要到岗，"
                  "也不用坐班，我们把素材发给你，一台电脑或手机都能做")
        for title in self.要的标题:
            assert jd_gate(["坐班", "包吃住"],
                           {"job_name": title, "jd_description": 远程JD}) == "", title
        坐班JD = ("周一至周五到岗坐班审核，早九晚六午休两小时，公司不提供住宿，"
                  "表现好可转正，需要自己解决通勤问题，周末单休轮班")
        assert "命中否决词" in jd_gate(["坐班"], {"job_name": "坐班审核岗",
                                                  "jd_description": 坐班JD})

    def test_拆字标题也要认(self):
        """BOSS 把敏感字拆开写（盘上真有「免 费 提 供电动车食住」「哈⁢啰⁢出⁢行」）。"""
        from boss_bot.intent import title_veto_hit
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT,
                              "全长沙W一 免 费 提 供电动车食住直招可短期8-9K") == "电动车"

    def test_团播陪聊情感互动是主播一族(self):
        """今天盘上被主动追过的三条：团播艺人、轻松聊天情感互动包住、无需露脸日结。"""
        from boss_bot.intent import veto_hit_anywhere
        for title in ("团播艺人 全程带播 高提成 上不封顶7-10K长沙查看职位",
                      "轻松聊天情感互动包住 15-20K工作自由顶10-15K长沙查看职位",
                      "兼职·日结/无需露脸/小白既可/线上居家/时间自由120-150元/时"):
            assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, [],
                                     title=title) != "", title

    def test_岗位类型表不看否定式(self):
        """"不露脸""无需露脸"都是主播——类型表看性质，不看有没有那个"不"字。"""
        from boss_bot.intent import title_veto_hit
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, "不露脸主播招5人") != ""
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, "无需露脸语音厅主播") != ""

    def test_职业白名单仍旧放行(self):
        from boss_bot.intent import title_veto_hit
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, "聊天室客服（线上）") == ""
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, "电动车数据标注员") == ""

    def test_盘上补的词拦得住今天漏的那几条(self):
        """2026-10-07 中午真被追过/聊过的三条：白班坐岗包吃住、汽配厂小时工、坐班临时工。"""
        from boss_bot.intent import veto_hit_anywhere
        for title in ("【白班坐岗】28/H包吃住+可预支+不体检（派遣职位）7500-8000元/月",
                      "免体检小时工月综合6000汽配厂可预支可周结（派遣职位）5500-6500元/月",
                      "岳麓区坐班临时工/包住宿/可周结（派遣职位）5000-6000元/月长沙"):
            assert veto_hit_anywhere(TITLE_VETO_KEYWORDS_DEFAULT, [], title=title) != "", title

    def test_跟进候选不再误杀这些会话(self):
        from boss_bot.reply_queue import worth_following_up
        for title in self.要的标题:
            chat = {"company": "某某传媒", "job_name": title,
                    "messages": [{"text": "在忙吗？方便聊聊~", "is_mine": False,
                                  "time": "2026-10-07 09:00:00"}]}
            assert worth_following_up(chat, TITLE_VETO_KEYWORDS_DEFAULT,
                                      ["坐班", "包吃住"]) is True, title


class 跟进Test:
    """已经当面拒过的会话，不能再追一句"之前聊的普工岗还在考虑吗"。

    盘上真发生过：09:54:58 账号2 对「急招（在家就行）哔哩哔哩兼职不露脸主播」
    发了一条 followup，前面那条正是我们自己的拒绝话术。
    """

    def _chats(self, our_last):
        from datetime import datetime
        now = datetime(2026, 10, 7, 12, 0, 0)
        old = (now - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
        chats = [{"account_index": 0, "chat_name": "梁玲", "company": "某某人力",
                  "job_name": "长白班普工包吃住18元一小时",
                  "updated_at": old,
                  "messages": [{"text": "有兴趣聊聊吗？", "is_mine": False, "time": old},
                               {"text": our_last, "is_mine": True, "time": old}]}]
        return chats, now

    def test_岗位类型拒绝过的不追(self):
        from boss_bot.reply_queue import followup_due
        chats, now = self._chats(OUR_DECLINE)
        assert followup_due(chats, {}, now=now) == [], "拒过还追问就是反悔骚扰"

    def test_线下面试拒绝过的也不追(self):
        from boss_bot.main_loop import OFFLINE_INTERVIEW_DECLINE
        from boss_bot.reply_queue import followup_due
        chats, now = self._chats(OFFLINE_INTERVIEW_DECLINE)
        assert followup_due(chats, {}, now=now) == []

    def test_主循环真的走这一条判据(self):
        import boss_bot.main_loop as ml
        src = inspect.getsource(ml.UnifiedBotLoop._run_followup_round)
        assert "worth_following_up" in src, "跟进轮没有按岗位类型筛候选"

    def test_普工会话不值得追标注会话值得(self):
        from boss_bot.reply_queue import worth_following_up
        def chat(title, company="某某人力", with_hr=True):
            msgs = [{"text": "有兴趣聊聊吗？", "is_mine": False,
                     "time": "2026-10-07 09:00:00"}] if with_hr else []
            return {"chat_name": "梁玲", "company": company, "job_name": title,
                    "messages": msgs}
        kw = TITLE_VETO_KEYWORDS_DEFAULT
        assert worth_following_up(chat("长白班普工包吃住18元一小时"), kw) is False
        assert worth_following_up(chat("兼职·数据标注专员20-30元/时"), kw) is True
        assert worth_following_up(chat("兼职·数据标注专员", company=""), kw) is False, \
            "连公司名都没有的孤儿存档从来就不该追"

    def test_正常聊过的照旧追(self):
        from boss_bot.reply_queue import followup_due
        chats, now = self._chats("您好，这个岗位的工作内容能再具体说说吗？")
        assert len(followup_due(chats, {}, now=now)) == 1

    def test_拒绝话术里真的带着那句标记(self):
        """三端同一个口径： reply_engine/main_loop 改了文案，这里就要跟着改。"""
        from boss_bot.main_loop import OFFLINE_INTERVIEW_DECLINE
        from boss_bot.reply_engine import FAMILY_DECLINE_REPLY
        from boss_bot.reply_queue import REFUSAL_ENDINGS
        for text in (FAMILY_DECLINE_REPLY, OFFLINE_INTERVIEW_DECLINE):
            assert any(mark in text for mark in REFUSAL_ENDINGS), text


class 台账Test:
    def test_默认标记是这一串(self):
        from boss_bot.contact_ledger import CONTACT_DECLINED_MARK
        assert CONTACT_DECLINED_MARK == DECLINED_MARK

    def test_拒绝过的卡片不再挂在待处理那一档(self):
        from boss_bot.contact_ledger import contact_rows
        chats = [{"account_index": 0, "chat_name": "梁玲", "company": "某某人力",
                  "job_name": "长白班普工包吃住18元一小时",
                  "updated_at": "2026-10-07 10:00:00",
                  "messages": [
                      {"text": CARD_WECHAT, "is_mine": False,
                       "time": "2026-10-07 09:54:00"},
                      {"text": DECLINED_MARK, "is_mine": True,
                       "time": "2026-10-07 09:54:20"},
                  ]}]
        rows = contact_rows(chats, [])
        assert rows and rows[0]["contact_kind"] == "卡片已拒绝", rows

    def test_面板来源标签认这一类(self):
        html = (ROOT / "flask-version" / "templates" / "index.html").read_text(
            encoding="utf-8")
        assert "reject_contact" in html, "回复来源里少了这一类，界面会显示成空白"
