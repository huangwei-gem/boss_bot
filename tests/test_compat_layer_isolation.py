# -*- coding: utf-8 -*-
"""热重载动的 boss_bot.config 那 10 个常量，测试跑完要还回去。

`config.刷新兼容层(cfg)` 是生产里热重载的一步（main_loop.py:4137）：它用
`globals().update(...)` 把 SALARY_REPLY / REPLY_RULES / USER_PROFILE 这一批模块级
常量换成传进来那份 cfg 的值。测试里随便造一份 loop 跑一次 `_hot_reload_config()`，
这些名字就留在了桩配置上——实测：

    pytest tests/test_unified.py tests/test_greeting_rule_overfire.py
    → AssertionError: assert ('您好' in {})      # 单跑 test_greeting_rule_overfire 是绿的

跨文件的假绿/假红都从这个口子进来。这两条测试按定义顺序跑：第一条把桩配置塞进去并
确认它真的改得动，第二条确认这份账没有留给下一条测试。conftest 里那个 autouse
fixture 的 finally 一旦撤掉，第二条就红。
"""
import boss_bot.config as 兼容层
from boss_bot.unified_config import UnifiedConfig

常量们 = ("SALARY_REPLY", "INTERVIEW_TIME_REPLY", "JOB_CONTENT_REPLY",
          "GREETING_REPLY", "DEFAULT_REPLY", "RESUME_DUPLICATE_REPLY",
          "RESUME_UNAVAILABLE_REPLY", "USER_PROFILE", "REPLY_RULES",
          "IMPORTANCE_KEYWORDS")


def _桩配置():
    cfg = UnifiedConfig.load()
    cfg.rules.reply_rules = {}
    cfg.templates.salary_reply = "桩话术"
    return cfg


def test_a_桩配置热重载真的改得动兼容层():
    """先把危害本身钉住：刷新一次，模块常量立刻变成桩配置的值。

    这条不是在要求"能改乱"，是证明下一条测的是真机制——如果哪天刷新兼容层
    改成不动 globals，下一条就成了白跑的绿。
    """
    原 = 兼容层.刷新兼容层(_桩配置())
    assert 原 == 10
    assert 兼容层.REPLY_RULES == {}, "桩配置没把 REPLY_RULES 顶掉，下一条款子测不到东西"
    assert 兼容层.SALARY_REPLY == "桩话术"


def test_b_上一条的桩配置没有留给这一条():
    assert 兼容层.REPLY_RULES, "conftest 没把兼容层还回去：桩配置的账漏给了后面的测试"
    assert "您好" in 兼容层.REPLY_RULES
    assert 兼容层.SALARY_REPLY != "桩话术"
    for 名 in 常量们:
        assert getattr(兼容层, 名, None) is not None, "%s 还回到常量表里来路查" % 名
