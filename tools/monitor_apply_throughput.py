# -*- coding: utf-8 -*-
"""投递监督：每 5 分钟往 _tmp_apply_monitor.log 追加一行摘要（两号今日已投/最近一轮产出/异常）。"""
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "boss_bot.log")
OUT = os.path.join(ROOT, "_tmp_apply_monitor.log")
GREET = os.path.join(ROOT, "data", "greet_records.json")


def today_applied():
    try:
        rec = json.load(io.open(GREET, encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(rec, dict):
        rec = rec.get("records") or rec.get("items") or []
    day = datetime.now().strftime("%Y-%m-%d")
    c = Counter()
    reasons = Counter()
    for r in rec:
        if str(r.get("timestamp") or "")[:10] != day:
            continue
        acc = r.get("account_index")
        if r.get("status") == "applied":
            c[acc] += 1
        elif r.get("status") == "failed":
            reasons[(acc, str(r.get("skip_reason") or "")[:24])] += 1
    return dict(applied=dict(c), failed=dict(reasons))


def tail_keys(minutes=5):
    since = datetime.now().timestamp() - minutes * 60
    day = datetime.now().strftime("%Y-%m-%d")
    pat = re.compile(r"已投递|开始打招呼轮次|打招呼轮次结束|到预算|冷却中|沟通次数|页面访问失败|登录失效|未找到输入框")
    out = []
    try:
        with io.open(LOG, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            lines = f.readlines()
    except Exception:
        return out
    for l in lines:
        if not l.startswith(day) or not pat.search(l) or "openai" in l:
            # 日志里有整段 JD 的续行（不带日期），只认日期开头的行，
            # 否则按字符串比时间会把前天 20:21 的旧行当成刚刚发生
            continue
        try:
            ts = datetime.strptime(l[:19], "%Y-%m-%d %H:%M:%S,%f").timestamp()
        except ValueError:
            continue
        if ts >= since:
            out.append(l.rstrip()[:160])
    return out


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 36
    for _ in range(rounds):
        d = today_applied()
        with io.open(OUT, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} 今日已投 {d.get('applied')} ===\n")
            if d.get("failed"):
                f.write(f"  失败原因: {d['failed']}\n")
            for l in tail_keys():
                f.write("  " + l + "\n")
        time.sleep(300)


if __name__ == "__main__":
    main()
