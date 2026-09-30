# boss-apply/scripts/state.py
# -*- coding: utf-8 -*-
"""boss-apply 的记录 / 去重 / 计数。纯标准库，绝不联网、绝不碰浏览器。

为什么上限计数在这里而不是 agent 的上下文里：一轮跑到第 20 个岗位时上下文可能
已经被压缩，agent"以为"自己只投了 3 个，而多投的代价是骚扰 HR。数错方向只能是
"少投"，不能是"多投"。
"""
import os
from pathlib import Path


def home() -> Path:
    """使用者的一切都在这里，skill 目录只放代码与 example。"""
    raw = os.environ.get("BOSS_APPLY_HOME") or str(Path.home() / ".boss-apply")
    return Path(raw).expanduser()


def state_dir() -> Path:
    d = home() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d
