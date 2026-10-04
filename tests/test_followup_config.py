# -*- coding: utf-8 -*-
"""新增的回复补漏/主动跟进配置：面板写进去要能被引擎读回来。

红线（2026-10-04 复述）：界面任何输入框都要真实生效，否则撤掉入口。
"""
from boss_bot.unified_config import UnifiedConfig

NEW_KEYS = {
    "owed_per_round": 5,
    "owed_max_age_hours": 48,
    "followup_enabled": False,
    "followup_after_hours": 6,
    "followup_gap_hours": 30,
    "followup_max_times": 3,
    "followup_every_minutes": 20,
}


def test_默认值就是面板上写的那几个():
    r = UnifiedConfig().reply
    assert (r.owed_per_round, r.owed_max_age_hours) == (8, 72)
    assert r.followup_enabled is True
    assert (r.followup_after_hours, r.followup_gap_hours, r.followup_max_times,
            r.followup_every_minutes) == (8, 24, 2, 15)


def test_to_dict_带着这些键出去():
    out = UnifiedConfig().to_dict()["reply"]
    for k in NEW_KEYS:
        assert k in out, f"reply.{k} 没进 to_dict，界面读不到就是空输入框"


def test_存成JSON再读回来一个不差():
    saved = UnifiedConfig().to_dict()
    saved["reply"].update(NEW_KEYS)
    cfg = UnifiedConfig()
    cfg._apply_bot_config(saved)
    for k, v in NEW_KEYS.items():
        assert getattr(cfg.reply, k) == v, f"reply.{k} 写进去读不回来，改了不生效"


def test_关掉开关读回来也是关():
    """followup_enabled=false 不能用 `or 默认值` 写，那样 False 会被吞成 True。"""
    cfg = UnifiedConfig()
    cfg._apply_bot_config({"reply": {"followup_enabled": False}})
    assert cfg.reply.followup_enabled is False


def test_按账号覆盖也认这些键():
    base = UnifiedConfig()
    base.greet.accounts[0].settings = {"reply": {"followup_max_times": 1,
                                                "followup_enabled": False}}
    got = base.apply_account(0).reply
    assert got.followup_max_times == 1
    assert got.followup_enabled is False
    assert base.reply.followup_max_times == 2, "覆盖不能漏到全局基准上"
