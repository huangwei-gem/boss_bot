# -*- coding: utf-8 -*-
"""发出去的回复必须是一句人话，不能是模型的思考过程或 markdown 草稿。

2026-09-30 用户截图：BOSS 上收到一条
「用户是求职者，正在BOSS直聘上与招聘方"肖瑾"聊一个"金融数据助理"岗位……
 对话历史：1. …… 分析：- HR表示…… 根据要求：- 简洁」
盘上一查，data/reply_records.json 里这类 reply_source="ai" 的共 7 条，
最长 507 字，还有一条整段英文思考（"Let me analyze the conversation context:"）。

两条来路都要堵：
1. 推理型接口正文为空时，旧代码 `text = content or reasoning` 直接把 reasoning 发出去；
2. 模型把"先分析再回复"这条规则写进了正文本身（content 里就是分析稿）。

判据是现算的，不是拍的：data/reply_records.json 里 31 条 AI 回复，
24 条正常的最长 120 字（p75=68），7 条异常的最短 307 字——所以 160 字这条线
两边都碰不到。异常那 7 条还都带提示词脚手架字样（招聘方称呼/最近对话记录/对方最新消息）
或列表结构，正常回复里没有一条有。
"""
import json
from pathlib import Path

import pytest

from boss_bot.reply_engine import _format_rejection, ReplyEngine, reply_rejection

ROOT = Path(__file__).resolve().parent.parent

# 盘上真实发出去过的 7 条思考过程（2026-09-29 ~ 09-30）
LEAKED = [
    '我们只需要输出回复内容。根据对话历史，对方最初拒绝，我发送了自我介绍（但规则说如果之前'
    '已经发过自我介绍不要重复），然后我礼貌感谢结束。对方又发"我们现在招客服"，我回复"感兴趣不"',
    '我们正在模拟求职者与招聘方的对话。招聘方孙先生，岗位管培生，之前对方说看了简历想深入沟通，'
    '然后对方最新消息说"为什么不理me？？？"——这是对方在催',
    '用户是求职者，背景是数据分析方向，本科学历。HR发来消息说看到简历觉得合适，想聊一下。\n\n'
    '岗位是产品运营，底薪6K+可盖三方证明，薪资6-8K在长沙。\n\n我需要分析：\n1. 这是HR主动',
    "Let me analyze the conversation context:\n\n1. I previously sent an introduction "
    "for a data analyst role\n2. The HR replied",
    '首先，分析对话上下文：\n- 招聘方称呼：吴先生\n- 招聘岗位：视频剪辑实习生2-5K长沙查看职位\n'
    '- 最近对话记录：无历史消息\n- 对方最新消息：我这边仔细看了你的工作经历',
    '首先，分析对话上下文：\n- 招聘方称呼：贾先生\n- 招聘岗位：新手可做 美团骑手\n- 最近对话记录：\n'
    '  - 对方: 此Boss正在急招该职位',
    '用户是求职者，正在BOSS直聘上与招聘方"肖瑾"聊一个"金融数据助理"岗位，薪资100-170元/天，'
    '地点长沙。\n\n对话历史：\n1. 对方（系统）：此Boss正在急招该职位\n\n分析：\n- HR表示有兴趣\n'
    '根据要求：\n- 简洁',
]

# 盘上真实发出去且正常的回复（取最长的几条，防止校验器把长句误杀）
NORMAL = [
    '您好，感谢您的介绍。不过我的求职方向是数据分析岗位，跟这个主播岗位不太匹配。'
    '请问贵公司是否有数据分析相关岗位在招呢？',
    '您好，感谢您的介绍。不过我的求职方向是数据分析，这个岗位不太匹配。请问贵司是否有'
    '数据分析相关的岗位？如果没有的话，就不耽误您的时间了。祝您招聘顺利~',
    '好的，感谢您的反馈。我有数据分析实操经验，虽然未直接接触财务数据，但数据逻辑和'
    '分析方法是相通的。如果后续有合适的岗位，也欢迎再联系我。',
    '您好，简历我稍后整理一份发您。另外想确认下，这个岗位具体是数据/策略运营方向吗？'
    '我主要想找数据分析相关的岗位。',
    '感谢您的时间，理解您的考虑，祝您招聘顺利~',
]

# 盘上真发出去过、但按 10-21 定下来的口径现在该拦的句子。
# 「全量不误杀」那条测试要拿真数据过，这些是唯一允许的红——列出来写清原因，
# 以后再加判据时它们不会被顺手当成"误杀"删掉。
已知坏句 = [
    # 10-21 上午对王小川发出去的一句，韩文字符混进中文里（模型串了语种）。
    "嗯，这个 주로做哪类标注，任务怎么领取，是按小时还是按量结算呀？",
    # 加微账在黄维自己手机上，机器点不了；这句和「我这就加您」是同一个动作的
    # 另一种写法，10-21 把这族收进闸门，原来放在"正常回复"里是要翻面的旧账。
    "好的，我马上添加，添加后跟您说一声。",
    # 2026-10-23 12:56:40 对朱微真发出去的一句（logs/boss_bot.log.2026-10-23:23721
    # 「已发送文字」）：模型把"它的决定"写成了正文，人不会这么打字，
    # HR 回的那句「是用ai插件主动投递的吗老师？能教教我么」就是这么来的。
    "（无需回复，对话已自然结束）",
]


class GuardTest:
    @pytest.mark.parametrize("text", LEAKED)
    def test_思考过程一律不许发(self, text):
        assert reply_rejection(text), "这条被当成可发送的回复了"

    @pytest.mark.parametrize("text", NORMAL)
    def test_正常回复不许误杀(self, text):
        assert reply_rejection(text) == "", reply_rejection(text)

    def test_盘上所有正常长度的AI回复都过得去(self):
        """校验器最大的风险是把能发的拦下来——拿真实数据全量过一遍。

        这里只过形式那一段（长度/思考痕迹/markdown）。事实那一段本来就打算拦历史里
        那些替他说错年龄学历的句子，混在一起测就成了"新闸门误杀"的假红。
        """
        path = ROOT / "data" / "reply_records.json"
        if not path.exists():
            pytest.skip("没有真实记录")
        recs = json.loads(path.read_text(encoding="utf-8"))
        recs = recs if isinstance(recs, list) else recs.get("records") or []
        坏 = set(已知坏句)
        for r in recs:
            if r.get("reply_source") != "ai":
                continue
            text = r.get("reply_content") or ""
            if text in 坏:
                continue
            if len(text) <= 160:
                assert _format_rejection(text) == "", f"误杀真实回复：{text[:60]}"
            else:
                assert _format_rejection(text), f"这条异常长却没被拦：{text[:60]}"

    def test_已知坏句确实是被闸门抓住的(self):
        """豁免名单不能只当遮羞布——每一条都得真的拦得住，不然就是藏着一条过期的账。

        过整道闸（reply_rejection），不只过形式那一段：「我马上添加」那一族是
        站外承诺那张词表抓的，形式检查本来就管不着它。
        """
        for 句 in 已知坏句:
            assert reply_rejection(句), "这句现在拦不住，豁免名单要撤: %s" % 句[:40]

    def test_历史上替他说错的那几句现在拦得住(self):
        """防重犯：盘上真发出去过「我今年25岁」（他 21）、「我本科毕业」（本科在读）。
        事实闸门在真实数据里抓到 0 条，就说明判据是死的。

        00:00 会把当天记录归档进 data/archive/<日期>/，活文件只剩今天，
        所以这两句要去活文件和归档里一起找（10-10 凌晨归档后就只剩归档有）。
        过窗的那盒现在挪在 data/archive_kept/ 而不是删掉（10-20 定的），
        两处都得读——只认 archive/ 会让这条闸门因为搬家而假红。
        """
        paths = [ROOT / "data" / "reply_records.json"]
        for 根 in ("archive", "archive_kept"):
            paths += sorted((ROOT / "data" / 根).glob("*/reply_records.json"))
        recs = []
        for path in paths:
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            recs += data if isinstance(data, list) else (data.get("records") or [])
        if not recs:
            pytest.skip("没有真实记录")
        拦下来的 = [reply_rejection(r.get("reply_content") or "")
                    for r in recs if r.get("reply_source") == "ai"]
        事实类 = [x for x in 拦下来的 if "画像" in x]
        assert 事实类, "真实记录里那些不实个人信息要能被现读的画像比出来"

    def test_markdown结构被拦(self):
        assert reply_rejection("**您好**，我对岗位很感兴趣")
        assert reply_rejection("```\n好的，我稍后发送\n```")
        assert reply_rejection("### 回复\n您好，方便面谈吗")

    def test_韩文假名混进中文要拦(self):
        """10-21 10:57:07 对王小川真发出去过一句「嗯，这个 주로做哪类标注……」。
        他面的是中文标注岗，回复里冒出谚文就是模型串了语种，HR 一眼看出是机器人。
        历史 4249 条里就这一条（0.02%），放行它没有代价。"""
        assert reply_rejection("嗯，这个 주로做哪类标注，任务怎么领取，是按小时还是按量结算呀？")
        assert reply_rejection("好的，こちらから送ります")
        for 句 in ("好嘞，这个岗是按小时还是按量结算呀？",
                   "OK，那我先看培训文档，有不懂的再问您"):
            assert reply_rejection(句) == "", reply_rejection(句)

    def test_空回复也算不可发送(self):
        assert reply_rejection("   ")


class CallChatTest:
    """`_call_chat` 是唯一出口：正文为空绝不退回 reasoning，不合格就抛给容灾链。"""

    def _engine(self):
        e = ReplyEngine.__new__(ReplyEngine)
        e._ai_max_tokens = 200
        e._last_ai_system_prompt = None
        e._last_ai_user_prompt = None
        e._last_ai_model = ""
        e._last_ai_raw_response = None
        e._self_evolve = None
        # __new__ 绕过了 __init__，这两样得自己补：正文要按这一号的求职意向出提示词
        e._account_index = 0
        return e

    def _client(self, content, reasoning=""):
        msg = type("M", (), {"content": content, "reasoning_content": reasoning})()
        resp = type("R", (), {"choices": [type("C", (), {"message": msg})()]})()
        return type("Cl", (), {"chat": type("Ch", (), {
            "completions": type("Co", (), {"create": staticmethod(lambda **kw: resp)})()})(
        )})()

    def test_正文为空时抛错而不是发思考过程(self):
        from unittest.mock import patch
        e = self._engine()
        client = self._client(None, LEAKED[6])
        with patch("boss_bot.reply_engine.build_user_prompt", return_value="up"), \
             patch("boss_bot.reply_engine.build_system_prompt", return_value="sp"):
            with pytest.raises(Exception) as ei:
                e._call_chat(client, "m", "在吗", "肖瑾", "金融数据助理", [])
        assert "思考" in str(ei.value) or "正文" in str(ei.value)

    def test_正文里是分析稿也抛错(self):
        from unittest.mock import patch
        e = self._engine()
        client = self._client(LEAKED[4])
        with patch("boss_bot.reply_engine.build_user_prompt", return_value="up"), \
             patch("boss_bot.reply_engine.build_system_prompt", return_value="sp"):
            with pytest.raises(Exception) as ei:
                e._call_chat(client, "m", "在吗", "吴先生", "视频剪辑实习生", [])
        msg = str(ei.value)
        assert "换下一个接口" in msg, f"不合格必须抛错走容灾链：{msg}"
        assert "不能发" in msg, f"报错要说清这句发不出去：{msg}"

    def test_正常正文照常返回(self):
        from unittest.mock import patch
        e = self._engine()
        client = self._client("好的，我稍后把简历发您，方便问一下面试形式吗？")
        with patch("boss_bot.reply_engine.build_user_prompt", return_value="up"), \
             patch("boss_bot.reply_engine.build_system_prompt", return_value="sp"):
            assert e._call_chat(client, "m", "在吗", "王经理", "数据分析", []).startswith("好的")


class FailoverTest:
    """被拦下之后必须换接口，而不是把这条发出去。"""

    def _engine(self, n):
        e = ReplyEngine.__new__(ReplyEngine)
        e._cache = type("C", (), {"get": lambda *a: None, "set": lambda *a: None})()
        e._ai_providers = [{"key": "k", "model": f"m{i}", "url": "https://h/v1"}
                           for i in range(n)]
        e._ai_max_tokens = 200
        e._ai_rate_limit_wait = 0
        e._last_ai_system_prompt = None
        e._last_ai_user_prompt = None
        e._last_ai_model = ""
        e._last_ai_raw_response = None
        e._self_evolve = None
        e._account_index = 0
        return e

    def test_第一个接口回思考稿就换第二个(self):
        from unittest.mock import patch
        e = self._engine(3)
        seen = []

        def fake(client, model, msg, b, j, h, name, 指令=""):
            seen.append(name)
            if len(seen) == 1:
                raise RuntimeError("回复内容不像一句回复（分析稿/思考过程）")
            return "您好，方便约个时间详聊吗？"

        with patch.object(e, "_call_with_rate_limit_retry", side_effect=fake):
            reply = e._ask_ai("我们聊聊", "肖瑾", "金融数据助理")
        assert reply == "您好，方便约个时间详聊吗？"
        assert len(seen) == 2

    def test_全部接口都不合格时返回空让上层决定(self):
        from unittest.mock import patch
        e = self._engine(2)
        with patch.object(e, "_call_with_rate_limit_retry", return_value=None):
            assert e._ask_ai("我们聊聊", "肖瑾", "金融数据助理") is None


class 替用户许兑现不了的话要拦Test:
    """这三句是 2026-10-17 15:26~15:28 线上跟进真发出去的原文（改成现编之后）。

    系统提示词第 9 条早就写了"不许声称已完成无法确认的事情"，只写在提示词里拦不住：
    六个接口里有两个就这么回了。机器能做的只有发消息，发简历、加好友、交课程各有自己的
    闸门，话里替使用者说"已发送/已发您/微信加也可以"就是许一个兑现不了的结果——
    和被拦了的那些病句是同一类，所以拦在发送闸门上，不合格就换接口。
    """

    def test_声称已经发过好友申请要拦(self):
        from boss_bot.reply_engine import reply_rejection
        原 = "好的，已发送好友申请，麻烦通过一下，方便约面试时间~"
        assert "已发送" in reply_rejection(原), "这句真发出去了，机器没发过好友申请"

    def test_声称简历已经发过去要拦(self):
        from boss_bot.reply_engine import reply_rejection
        assert reply_rejection("好的，简历已发您，方便时想约个线上沟通，进一步了解数据类岗位机会。")

    def test_主动提出走微信要拦(self):
        from boss_bot.reply_engine import reply_rejection
        assert reply_rejection("您有空回我一下就行，微信加也可以，想细聊下采集任务的具体安排。")

    def test_声称已经同意交换联系方式要拦(self):
        """2026-10-21 12:43:05 线上真发出去的一句（HR：是感兴趣可以点交换 → 回「已同意交换」）。

        这一轮走的是 `action=text`，`accept_contact_exchange()` 一次都没调（同一时间窗
        日志里没有「已同意交换联系方式」，会话存档里也没有 `[已同意交换联系方式]` 那一行），
        话却说成"已同意"——HR 等着看交换过来，什么都没有。和「已发送好友申请」同一类：
        机器做过的动作自己有留痕，没做过的不许替使用者认领。
        """
        from boss_bot.reply_engine import reply_rejection
        原 = "好嘞，已同意交换，稍后把简历发您"
        assert "已同意" in reply_rejection(原), reply_rejection(原)
        assert reply_rejection("同意啦，您那边通过一下就行"), "「同意啦」也是既成事实的说法"
        assert reply_rejection("好的，已经点过交换了")

    def test_说待会儿去点交换不误伤(self):
        """拦的是"已经做了"，不是"我去做"——后者是正常接话，卡片那一轮真会去点。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("好嘞，我这就点一下交换，您那边通过下",
                   "行，那我点交换，简历我稍后发您看下"):
            assert reply_rejection(句) == "", reply_rejection(句)

    def test_答应去加微信这一族全要拦(self):
        """2026-10-21 一天里真发出去的四句，全是同一个形状。

        10:21:48 吕女士「好嘞，我这就加你～」
        11:10:37 杨丽  「好嘞，我这就加您，下午培训我时间都可以。」
        13:16:19 张女士 「好嘞，微信号收到了，我这就加您，培训文档和试标的事咱微信上聊哈」
        13:24:04 白艺竹 「好嘞，我这就加哈」
        旧词表只写了「我加您微信」「稍后加您微信」这种带"微信"二字的整串，
        换成「我这就加您」「我这就加哈」就从闸门过去了——加微账号码在黄维自己手机上，
        机器一次也做不到，和「我来添加您」是同一句谎。
        """
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("好嘞，我这就加你～",
                   "好嘞，我这就加您，下午培训我时间都可以。",
                   "好嘞，微信号收到了，我这就加您，培训文档和试标的事咱微信上聊哈",
                   "好嘞，我这就加哈",
                   "行，那我马上加您",
                   "好的，稍后加您，麻烦通过一下"):
            assert reply_rejection(句), "这句该被拦下：%s" % 句

    def test_收下微信号但不替他去加不误伤(self):
        """拦的是"我去加您"这个动作，不是"号我收下了"——后者是真的，可以照说。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("好嘞，微信号我记下了，那咱们这边先聊着",
                   "收到了，方便的话先把岗位的具体内容发我看看"):
            assert reply_rejection(句) == "", reply_rejection(句)

    def test_换话说去加微信也要拦(self):
        """旧词表按整串字面比，换个说法就从闸门过去了——今天线上真发出去的这些：

        11:22:36 王飞「好的，我这就去加，通过时备注数据标注专员」
        11:24:58 王飞「我这就去加您微信哈，通过后咱们详细沟通~」
        10:50:42 王女士「好的，那我直接加您微信，麻烦通过一下」
        10:42:59 朱女士「好的，已添加您微信，期待进一步沟通。」（一次都没加过）
        13:48:26 罗先生「刚去填了哈」（内推链接那张表根本没打开过）
        历史 4242 条 AI 回复里这一族占 3.9%，全是一个意思：替黄维答应/认领站外的动作。
        """
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("好的，我这就去加，通过时备注数据标注专员",
                   "我这就去加您微信哈，通过后咱们详细沟通~",
                   "好的，那我直接加您微信，麻烦通过一下",
                   "好的，已添加您微信，期待进一步沟通。",
                   "已添加，稍后请您发岗位介绍过来哈",
                   "刚去填了哈，可能是系统延迟没同步过来，稍后处理完我跟您说一声。"):
            assert reply_rejection(句), "这句该被拦下：%s" % 句

    def test_问清流程与复述对方的话不误伤(self):
        """闸门只拦"我去做/我已做"，问对方怎么安排、复述岗位内容都不在其中。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("好嘞，那这个岗是纯线上还是要到现场？",
                   "培训是看文档还是要开会？大概几天能上手？",
                   "嗯嗯，那您这边什么时候方便看简历？"):
            assert reply_rejection(句) == "", reply_rejection(句)

    def test_换到飞书这种站外平台也要拦(self):
        """15:16 线上真发出去的一句：HR 说"加一下飞书联系吧"，机器回"我来添加您"。
        加飞书、加 QQ 都是机器做不到的动作，方向和"我加您微信"一模一样。"""
        from boss_bot.reply_engine import reply_rejection
        原 = "好的，没问题。请问您的飞书账号是？我来添加您。"
        assert "我来添加您" in reply_rejection(原), reply_rejection(原)
        assert reply_rejection("方便的话留个飞书也行，咱们那边细聊。")

    def test_要求对方加自己好友也要拦(self):
        from boss_bot.reply_engine import reply_rejection
        assert reply_rejection("您加我微信吧，我平常不看 BOSS。")

    def test_稍后发送这种说法仍然放行(self):
        """反向哨兵：提示词明确让说"稍后完成/马上处理"，不能一起拦掉"""
        from boss_bot.reply_engine import reply_rejection
        assert reply_rejection("好的，我稍后把简历发给您，方便的话我们约个线上沟通？") == ""
        assert reply_rejection("麻烦您有空通过一下好友申请，我这边随时可以面试。") == ""

    def test_声称已经通过好友申请要拦(self):
        """10-21 上午连着四句，全在站外平台上替黄维认领"已经点了通过"：

        10:44:13 陈先生「通过了，钉钉这边已经通过您的好友申请了，您看下能收到吗？」
        10:58:07 陈先生「好嘞，已通过啦，随时可以开始学习测试~」
        11:01:54 陈先生「刷新下钉钉看看，我这边显示已通过了，不行我再发一次邀请。」
        11:08 之前那句「我已经通过您的微信添加请求了」也是同一句谎。
        钉钉/微信里的"通过好友申请"是在黄维自己手机上做的动作，机器一次也点不了；
        历史 4249 条 AI 回复里这一族 11 条（0.26%），逐条看过没有一条是真做过的。
        """
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("通过了，钉钉这边已经通过您的好友申请了，您看下能收到吗？",
                   "好嘞，已通过啦，随时可以开始学习测试~",
                   "刷新下钉钉看看，我这边显示已通过了，不行我再发一次邀请。",
                   "好的，我已经通过您的微信添加请求了，您再确认一下就好。",
                   "已通过内推链接投递数据分析师岗，简历匹配度高，请您查收~"):
            assert reply_rejection(句), "这句该被拦下：%s" % 句

    def test_让HR拉自己进群不误伤(self):
        """"已通过"是机器替人认领动作；"麻烦您拉我进群"是请对方做事，是真的能说的话。
        这两个挨在一起，判据只准打前一个。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("嗯看到了，麻烦拉我进考核群吧，随时准备开始学习测试~",
                   "好嘞，您拉我进群就行，我先看培训，定好能做哪个我在群里说。"):
            assert reply_rejection(句) == "", reply_rejection(句)

    def test_裸一句我加您也要拦(self):
        """词表写的是"我加您微信"，线上说的却是「您发下微信号我加您」——
        10:46:51 冯先生、11:05:46 谭珍、11:19:07 王飞三句都是这个形状，
        历史里去掉"微信"两个字的裸说法还有 6 条（0.14%），一条都没真加过。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("好嘞，您发下微信号我加您",
                   "好嘞，那您微信号发我一下，我加您细聊。",
                   "方便的话您发下微信，我加您约面谈时间。",
                   "好嘞，加上啦~方便聊聊这个岗的具体工作要求吗"):
            assert reply_rejection(句), "这句该被拦下：%s" % 句

    def test_答应去做机器做不了的活要拦(self):
        """11:43:58 刘先生「好嘞，我来填一下个人信息。」、11:29:29 田先生「投完跟您说一声」、
        10:15:57 鲍女士「我用电脑打开看看，剪好发您哈」——填表、投别的岗、剪视频，
        机器一件都不会做（发简历有它自己的动作闸门，不靠这句话认领）。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("好嘞，我来填一下个人信息。",
                   "好嘞，那我看看产品/运营相关的岗位，投完跟您说一声。",
                   "好嘞，我用电脑打开看看，剪好发您哈",
                   "嗯，剪完我发给您",
                   "好的，我马上添加，添加后跟您说一声。"):
            assert reply_rejection(句), "这句该被拦下：%s" % 句



class Test不许替他否认投递方式:
    """朱微 10-24 那句「是用ai插件主动投递的吗老师？能教教我么」之后，
    机器当场回了「没用插件哈，就是自己发的。」——号是机器在登、招呼语是机器在发，
    这句每一个字都是假的；HR 已经看出来了，撒谎只会把"用了工具"变成"用了工具还骗人"，
    最后他本人还得自己出来圆（当天那句「这个不能外传的，我还得优化优化」）。"""

    def test_真存档那一句拦得下来(self):
        from boss_bot.reply_engine import reply_rejection
        拒 = reply_rejection("没用插件哈，就是自己发的。主要想问下您那边有没有线上数据标注"
                        "或者数据分析这类的兼职？")
        assert 拒, "朱微案发现场那句谎话现在还能自动发出去"
        assert "投递方式" in 拒, 拒

    def test_同一族换说法也拦(self):
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("不是插件，都是自己发的哈",
                   "没用什么工具的，我一条条自己发的",
                   "都是手动投的，不是机器投",
                   "没有用AI投哈，就是我自己一个个聊的"):
            assert reply_rejection(句), "这句该拦下：%s" % 句

    def test_实话不许被误伤(self):
        """反向哨兵：谈"AI 方向"、说"我自己发简历给您"这类正常句子不许进这一族，
        否则回复腿会被自己的闸门堵死，看着像 AI 挂了。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("我找的是 AI 产品方向的岗，不是 AI 训练数据那类",
                   "简历我自己发给您，稍等我传一份 PDF",
                   "这个岗位需要会用 AI 工具，我平时用得多"):
            assert reply_rejection(句) == "", "误伤：%s ← %s" % (句, reply_rejection(句))
