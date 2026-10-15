# -*- coding: utf-8 -*-
"""只读：稳定性那一路（页面加载失败 / 抽屉不出 / 页面断开）到底有没有在同一批岗位 URL
上反复重试——按岗位 URL 数，不是按原因数。

为什么要按 URL 数：轮次每轮都重扫搜索页，一个没投成的岗位下一轮还会回来。
"每轮再走一遍导航→读 JD→AI 判分→点沟通"是白烧时间+白烧判分预算，但要不要给它
加"连败两次就放一放"，得先看历史上同一个 URL 真的吃了几次。抽屉那一路已经有这道门
（NO_DRAWER_GIVE_UP=2，2026-10-08 实测同一 URL 连吃 5 次），导航这一路没量过。

跑法：python tools/audit_stability_retries.py
"""
import glob
import io
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.reply_record import _SKIP_KIND_MARKERS

# 只统计"这一单的投递腿没走到点沟通"的那些原因；判据本身杀的不在这里
稳定性 = ("页面加载失败", "页面断开", "抽屉", "聊天页与浏览器", "页面已跳走",
         "恢复失败", "重试")

行 = []
for p in sorted(glob.glob("data/archive/*/greet_records.json")) + ["data/greet_records.json"]:
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    for r in d.get("records") or []:
        行.append(r)

按原因 = Counter()
按url = defaultdict(list)
for r in 行:
    reason = str(r.get("skip_reason") or r.get("status") or "")
    if r.get("is_greeted") or r.get("status") == "applied":
        continue
    if not any(k in reason for k in 稳定性):
        continue
    类别 = next((name for name, marks in _SKIP_KIND_MARKERS
                 if any(m in reason for m in marks)), "其它")
    按原因[(类别, reason[:34])] += 1
    url = (r.get("job_url") or "").strip()
    if url:
        按url[url].append((reason[:26], r.get("timestamp") or r.get("created_at") or ""))

print("稳定性那一路的记录 %d 条，落在 %d 个岗位 URL 上"
      % (sum(按原因.values()), len(按url)))
分布 = Counter(len(v) for v in 按url.values())
print("\n同一个 URL 吃了几次：")
for 次数 in sorted(分布):
    print("  %2d 次 × %d 个 url" % (次数, 分布[次数]))
重复 = sorted(按url.items(), key=lambda kv: -len(kv[1]))[:12]
print("\n吃得最多的这些：")
for url, 次 in 重复:
    if len(次) < 2:
        break
    print("  %d 次  %s  %s" % (len(次), url[-18:], "；".join(x[0] for x in 次[:6])))
未投成 = [r for r in 行 if r.get("status") == "failed"]
print("\nstatus=failed 共 %d 条" % len(未投成))
print("failed 里同一 url 出现 ≥2 次的：%d 个"
      % sum(1 for _, c in Counter((r.get("job_url") or "") for r in 未投成).items() if c >= 2))

# 现在那道按 URL 的门只数抽屉（_note_drawer_fail）。这里要量的是它漏掉的那一半：
# 一个 URL 的失败里一次抽屉都没有、全是导航/断开这类，今天没有任何东西在拦它，
# 下一轮还会照样重走"导航→读 JD→判分→点沟通"。
纯导航 = []
for url, 次 in 按url.items():
    原由 = [x[0] for x in 次]
    if len(次) >= 2 and not any("抽屉" in x for x in 原由):
        纯导航.append((url, 次))
print("\n≥2 次且一次抽屉都没撞上的 url：%d 个，共 %d 条记录（现在这道门完全看不见）"
      % (len(纯导航), sum(len(v) for _, v in 纯导航)))
for url, 次 in sorted(纯导航, key=lambda kv: -len(kv[1]))[:8]:
    print("  %2d 次  %s  %s" % (len(次), url[-16:], "；".join(Counter(x[0] for x in 次))))
