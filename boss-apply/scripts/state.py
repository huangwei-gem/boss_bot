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


HARD_CAP = 50        # skill 内置天花板：BOSS 自己给单账号 150/天，不该把人往那个数推
DEFAULT_CAP = 20     # 每轮默认上限，可在 rules.json 调，但不能超过 HARD_CAP


def _rows_of(round_id: str):
    out = []
    for path in sorted(state_dir().glob("rounds-*.json")):
        for row in _load_json(path, []):
            if row.get("round") == round_id:
                out.append(row)
    return out


def count_sent(round_id: str) -> int:
    return sum(1 for r in _rows_of(round_id) if r.get("failure_code") == SENT_CODE)


def summary(round_id: str) -> dict:
    rows = _rows_of(round_id)
    by_code = {}
    for r in rows:
        code = str(r.get("failure_code") or "unknown")
        by_code[code] = by_code.get(code, 0) + 1
    return {"round": round_id, "total": len(rows), "by_code": by_code,
            "sent": by_code.get(SENT_CODE, 0)}


def remaining(round_id: str, cap: int, already=None) -> int:
    """还能投几个。cap 越界时夹到 HARD_CAP，而不是报错——报错会被忽略，夹住不会。"""
    try:
        cap_n = int(cap)
    except (TypeError, ValueError):
        cap_n = DEFAULT_CAP
    cap_n = max(0, min(HARD_CAP, cap_n))
    used = count_sent(round_id) if already is None else int(already)
    return max(0, cap_n - used)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="state.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seen"); s.add_argument("--url", required=True)
    s.add_argument("--account", default="")
    r = sub.add_parser("record"); r.add_argument("--json", required=True)
    c = sub.add_parser("count"); c.add_argument("--round", required=True)
    m = sub.add_parser("summary"); m.add_argument("--round", required=True)
    a = p.parse_args(argv)
    if a.cmd == "seen":
        return 0 if seen(a.url, a.account) else 1
    if a.cmd == "record":
        row = record(**json.loads(a.json))
        print(json.dumps(row, ensure_ascii=False))
        return 0
    if a.cmd == "count":
        print(count_sent(a.round)); return 0
    print(json.dumps(summary(a.round), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
