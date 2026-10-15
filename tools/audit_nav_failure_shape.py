# -*- coding: utf-8 -*-
"""只读：那 408 条"页面加载失败"到底是一个岗位老抽风，还是整条投递腿一起坏。

这个区别决定修哪边：按岗位 URL 放一放（已有抽屉那道门的形式）还是账号层面的
时间窗熔断（抽屉额度那道的形式）。用记录的 timestamp 排一遍时间线，看相邻两条
"页面加载失败"之间隔了几分钟、中间有没有投成功过。

跑法：python tools/audit_nav_failure_shape.py
"""
import glob
import io
import json
import os
import sys
from datetime import datetime
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

行 = []
for p in sorted(glob.glob("data/archive/*/greet_records.json")) + ["data/greet_records.json"]:
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    for r in d.get("records") or []:
        行.append(r)


def 时刻(r):
    s = str(r.get("timestamp") or r.get("created_at") or "")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


带时 = [(时刻(r), r) for r in 行 if 时刻(r)]
带时.sort(key=lambda x: x[0])
坏 = [(t, r) for t, r in 带时 if "页面加载失败" in str(r.get("skip_reason") or "")]
好 = [(t, r) for t, r in 带时
      if r.get("is_greeted") or r.get("status") == "applied"]
print("带时间戳的记录 %d 条；其中页面加载失败 %d 条，投成功 %d 条"
      % (len(带时), len(坏), len(好)))

# 按天分组：坏是集中在某几天，还是每天都有
print("\n按天：失败/当天投成功")
坏天 = Counter(t.date() for t, _ in 坏)
好天 = Counter(t.date() for t, _ in 好)
for d in sorted(set(坏天) | set(好天)):
    print("  %s  失败 %3d  成功 %3d  %s"
          % (d, 坏天.get(d, 0), 好天.get(d, 0),
             "← 当天一条没投成" if not 好天.get(d) else ""))

# 连击长度：相邻两条失败之间隔多久、中间夹没夹成功
连击 = []
当前 = []
for t, r in 坏:
    if 当前 and (t - 当前[-1][0]).total_seconds() > 900:
        连击.append(当前)
        当前 = []
    当前.append((t, r))
if 当前:
    连击.append(当前)
长 = sorted(连击, key=len, reverse=True)[:8]
print("\n15 分钟内算同一波，共 %d 波；最长的几波：" % len(连击))
for 波 in 长:
    中间好 = sum(1 for t, _ in 好 if 波[0][0] <= t <= 波[-1][0])
    print("  %s→%s  %2d 条，波内投成功 %d 单，涉及 %d 个 url"
          % (波[0][0].strftime("%m-%d %H:%M"), 波[-1][0].strftime("%H:%M"),
             len(波), 中间好, len({(r.get("job_url") or "")[-12:] for _, r in 波})))
分布 = Counter(len(波) for 波 in 连击)
print("\n每波条数分布：" + "  ".join("%d条×%d波" % (k, v) for k, v in sorted(分布.items())))
