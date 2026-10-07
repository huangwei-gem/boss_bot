# -*- coding: utf-8 -*-
"""自进化的"待评估"不能只等下一次 get_reply 才消费。

2026-10-07 复盘投递时看到：evolution/status 里 pending_evaluations=48，
而 lessons/refine 是"看到 7 类模式、落库 0 条经验"——经验攒不到支撑量的原因不在门槛，
在喂进去的料：evaluate_previous_replies 只在**同一个会话再次进 get_reply**时才按
chat_name 找未评估记录（reply_engine:482），可 HR 回了之后我们这轮常走跳过分支
（防重复、已处理、冷却），根本不再进 get_reply，那条回复就永远挂着没评估。
sweep_ignored_replies（24 小时没回判 ignored）也一样，只在触发优化时才跑。

这组测试锁的是：排空要能按会话回头消费、要限量别把一轮拖长、超时未答要顺手判掉，
以及实现了就得真接进回复轮——项目里栽过好几回"写了没人调"。
"""
import inspect
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.self_evolve import (EFFECT_IGNORED, EFFECT_NEGATIVE, EFFECT_POSITIVE,  # noqa: E402
                                  SelfEvolveEngine)


def _引擎(tmp_path):
    return SelfEvolveEngine(config={"enabled": True},
                            data_file=str(tmp_path / "evolution_data.json"))


def _塞记录(eng, chat, 文本="您好，我对这个岗位很感兴趣", 几小时前=5.0):
    eng._reply_records.append({
        "id": len(eng._reply_records) + 1,
        "reply_text": 文本, "chat_name": chat, "job_name": "兼职·数据标注",
        "source": "ai", "intent": "other",
        "timestamp": (datetime.now() - timedelta(hours=几小时前)).isoformat(),
        "effect": None, "hr_response": None, "evaluated": False,
    })


def _会话(name, *hr文本):
    msgs = [{"text": "您好，我对这个岗位很感兴趣", "is_mine": True, "time": "10:00"}]
    for t in hr文本:
        msgs.append({"text": t, "is_mine": False, "time": "10:30"})
    return {"chat_name": name, "account_index": 0, "messages": msgs}


def test_待评估要按会话回头消费(tmp_path):
    eng = _引擎(tmp_path)
    _塞记录(eng, "李女士")
    _塞记录(eng, "王先生")
    assert eng.get_pending_evaluations() == 2
    chats = [_会话("李女士", "方便的话加个微信细聊？"),
             _会话("王先生", "抱歉，我们这边不考虑实习生哦")]   # 盘上真出现过的拒绝原话
    n = eng.drain_pending_evaluations(chats)
    assert n == 2, f"排空函数报称处理 {n} 条"
    assert eng.get_pending_evaluations() == 0, "HR 都回过话了，记录还挂着未评估"
    效果 = {r["chat_name"]: r["effect"] for r in eng._reply_records}
    assert 效果["李女士"] == EFFECT_POSITIVE and 效果["王先生"] == EFFECT_NEGATIVE, 效果


def test_一次别排太多把轮拖长(tmp_path):
    eng = _引擎(tmp_path)
    for i in range(6):
        _塞记录(eng, f"HR{i}")
    chats = [_会话(f"HR{i}", "好的") for i in range(6)]
    assert eng.drain_pending_evaluations(chats, limit=2) == 2
    assert eng.get_pending_evaluations() == 4, "限量没生效就会一次全跑，回复轮时间被吃掉"


def test_超时未答顺手判掉(tmp_path):
    eng = _引擎(tmp_path)
    _塞记录(eng, "张先生", 几小时前=80.0)          # 三天没人回
    eng.drain_pending_evaluations([_会话("张先生")])
    r = eng._reply_records[0]
    assert r["evaluated"] and r["effect"] == EFFECT_IGNORED, r


def test_排空接进回复轮():
    """实现了没人调 = 没实现。这条锁调用点。"""
    import boss_bot.main_loop as ml
    src = inspect.getsource(ml)
    assert "drain_pending_evaluations(" in src, "回复轮里没有排空待评估的调用"
