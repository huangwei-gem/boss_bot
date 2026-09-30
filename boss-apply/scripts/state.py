# boss-apply/scripts/state.py
# -*- coding: utf-8 -*-
"""boss-apply 的记录 / 去重 / 计数。纯标准库，绝不联网、绝不碰浏览器。

为什么上限计数在这里而不是 agent 的上下文里：一轮跑到第 20 个岗位时上下文可能
已经被压缩，agent"以为"自己只投了 3 个，而多投的代价是骚扰 HR。数错方向只能是
"少投"，不能是"多投"。
"""
import json
import os
import sys
import time
from pathlib import Path


def home() -> Path:
    """使用者的一切都在这里，skill 目录只放代码与 example。"""
    raw = os.environ.get("BOSS_APPLY_HOME") or str(Path.home() / ".boss-apply")
    return Path(raw).expanduser()


def state_dir() -> Path:
    d = home() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


SENT_CODE = "sent"
ROUND_KEEP_SECONDS = 3 * 86400      # rounds 文件保留 3 天，够跨会话查"这个岗位投过没"


def _rounds_file(round_id: str) -> Path:
    """round_id 形如 20260930-120000；按月分文件，一年 12 个文件而不是一个巨型 JSON。"""
    stamp = (round_id or "")[:6]
    month = f"{stamp[:4]}-{stamp[4:6]}" if len(stamp) == 6 else time.strftime("%Y-%m")
    return state_dir() / f"rounds-{month}.json"


def chatted_file() -> Path:
    return state_dir() / "chatted.json"


def _load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        # 坏文件不能静默当没有：那样会把"已经投过"的岗位重新投一遍
        raise


def _key(url: str, account: str) -> str:
    # account 不参与键：去重的对象是"岗位 + HR"，同一个人换个号再发一遍也是骚扰。
    # 账号信息仍然记在值里，便于排查是谁投的。
    return (url or "").strip()


def record(round_id: str, url: str, account: str, stage: str, failure_code: str,
           job_name: str = "", company: str = "", detail: str = "",
           score=None, duration_ms=None) -> dict:
    """一个岗位一条记录。发送成功即落库，不等一轮跑完再写。"""
    row = {"round": round_id, "url": url, "account": account, "stage": stage,
           "failure_code": failure_code, "job_name": job_name, "company": company,
           "detail": detail, "score": score, "duration_ms": duration_ms,
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    rows = _load_json(_rounds_file(round_id), [])
    rows.append(row)
    _dump(_rounds_file(round_id), rows)
    if failure_code == SENT_CODE:
        chatted = _load_json(chatted_file(), {})
        chatted[_key(url, account)] = row
        _dump(chatted_file(), chatted)
    return row


def _dump(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)          # 半截文件比崩溃更难查，写盘一律原子替换


def seen(url: str, account: str = "") -> bool:
    return _key(url, account) in _load_json(chatted_file(), {})
