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
    '好的，我马上添加，添加后跟您说一声。',
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
        for r in recs:
            if r.get("reply_source") != "ai":
                continue
            text = r.get("reply_content") or ""
            if len(text) <= 160:
                assert _format_rejection(text) == "", f"误杀真实回复：{text[:60]}"
            else:
                assert _format_rejection(text), f"这条异常长却没被拦：{text[:60]}"

    def test_历史上替他说错的那几句现在拦得住(self):
        """防重犯：盘上真发出去过「我今年25岁」（他 21）、「我本科毕业」（本科在读）。
        事实闸门在真实数据里抓到 0 条，就说明判据是死的。

        00:00 会把当天记录归档进 data/archive/<日期>/，活文件只剩今天，
        所以这两句要去活文件和归档里一起找（10-10 凌晨归档后就只剩归档有）。
        """
        paths = [ROOT / "data" / "reply_records.json"]
        paths += sorted((ROOT / "data" / "archive").glob("*/reply_records.json"))
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
        return e

    def test_第一个接口回思考稿就换第二个(self):
        from unittest.mock import patch
        e = self._engine(3)
        seen = []

        def fake(client, model, msg, b, j, h, name):
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
