# -*- coding: utf-8 -*-
"""AI 接口这次没答上来，不能记成"AI 说这个岗位不合适"。

盘上取证（tools/audit_jobseeker_view.py 重放机器看过的 1774 个岗位）：
被记成 `AI判定不匹配: AI 分析异常: 请求超时（30s）` / `正文被截断（thinking 写了 2550 字，
max_tokens=1600 不够）` 的岗位有几十个，其中十几个按求职者视角是该投的。
记成"不匹配"有三处实打实的坏：

1. `classify_greet_skip` 把它们归到 ai_reject，而同一岗位同一天同一类别只判一次——
   接口 30 秒没答上，这单今天就再也不判了；
2. 面板上他看到的是"AI 判定不匹配"，分不清是岗位不合格还是接口挂了（稳定性看不见）；
3. 判分复盘那条追问链（_maybe_probe_rejection）会拿这条去问"你到底卡在哪一条"——
   模型压根没判过，问一次烧一次接口配额。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boss_bot.reply_record import ai_should_probe, ai_skip_text, classify_greet_skip


class 接口故障和岗位不合格分开Test:
    def test_接口异常写的不是不匹配(self):
        文 = ai_skip_text({"ai_error": True,
                           "reason": "AI 分析异常: 请求超时（30s），按配置跳过"})
        assert "AI判定不匹配" not in 文, 文
        assert "请求超时" in 文

    def test_接口异常归到接口那一类所以明天还会判(self):
        """ai_reject 是同岗位当天只判一次；接口故障必须落到 ai_unavailable。"""
        文 = ai_skip_text({"ai_error": True,
                           "reason": "AI 分析异常: 正文被截断（thinking 写了 2550 字）"})
        assert classify_greet_skip(文) == "ai_unavailable", 文

    def test_真判不匹配还是走原来那一类(self):
        文 = ai_skip_text({"reason": "命中硬性筛选条件「主播」，不予通过。"})
        assert 文.startswith("AI判定不匹配"), 文
        assert classify_greet_skip(文) == "ai_reject"

    def test_没给出判断就不许再追问(self):
        """追问链自己也要打 AI：模型没判过还去问"卡在哪一条"，纯属再烧一次配额。"""
        assert ai_should_probe({"ai_error": True, "reason": "AI 分析异常: 请求超时"}) is False
        assert ai_should_probe({"reason": "命中硬性筛选条件「主播」"}) is True
        assert ai_should_probe({}) is True       # 没带 ai_error 标记的照旧追问
