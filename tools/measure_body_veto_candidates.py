# -*- coding: utf-8 -*-
"""只读：往"正文那一遍"（面板上「自定义筛选词」那张表）加词之前，量一遍会新杀几条、
杀掉的里面有没有他自己要点开的方向岗。

为什么这张表要单独量：岗位类型那张表只查标题、带职业白名单，而这一条**写在哪一句都算**
（"需坐班/包吃住"本来就是一句话的事）。所以「数据标注/AI训练师…无需押金」这种
一句带过押金形状的标注岗，加错一个词就被误杀——必须连否定式一起看，并把命中处的
上下文打出来，光数条数判不了。

跑法：python tools/measure_body_veto_candidates.py
"""
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.greet_engine import jd_gate
from boss_bot.unified_config import UnifiedConfig

候选 = ["押金", "保证金", "培训费", "服装费", "体检费", "垫付", "刷单", "拉人",
        "进群", "兼职群", "无底薪", "纯佣", "先付", "交钱", "收费", "会员费",
        "猎头", "代招", "招聘"]

cfg = UnifiedConfig.load()
类型表 = list(cfg.ai.title_veto_keywords or [])
正文表 = list(cfg.ai.custom_filter_keywords or [])
方向表 = list(cfg.ai.target_job_keywords or [])

行 = {}
for p in sorted(glob.glob("data/archive/*/greet_records.json")) + ["data/greet_records.json"]:
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    for r in d.get("records") or []:
        键 = (r.get("job_url") or "").strip() or (r.get("job_name", "") + r.get("company", ""))
        if 键 and 键 not in 行:
            行[键] = r


def 全文(r):
    return " ".join(str(r.get(x) or "") for x in
                    ("job_name", "job_description", "job_requirements",
                     "description", "requirements"))


def 放行(r, 额外=()):
    return jd_gate(正文表 + list(额外), r, title_keywords=类型表,
                   target_keywords=方向表) == ""


活池 = [r for r in 行.values() if 放行(r)]
# 真烧掉额度的那一把：已经投出去的。加词要看的是"这批里有多少本来就是来要钱的"，
# 光看还没投的池子会把"以后可能有用"当成"现在有用"。
投了 = [r for r in 行.values()
        if r.get("status") == "applied" or r.get("is_greeted")]
print("机器看过的岗位 %d 个，按今天的配置放行 %d 个，其中真投出去过 %d 个"
      % (len(行), len(活池), len(投了)))
print("\n词      新杀(未投池/已投池)   杀掉的岗位形状")
for 词 in 候选:
    if 词 in 正文表:
        print("%-6s 已在表里" % 词)
        continue
    杀 = [r for r in 活池 if not 放行(r, [词])]
    杀投 = [r for r in 投了 if not 放行(r, [词])]
    样本 = 杀投[:3] or 杀[:3]
    print("%-6s %3d / %3d        %s" % (词, len(杀), len(杀投),
          "；".join((r.get("job_name") or "")[:22] for r in 样本)))
    for r in (杀投[:2] or 杀[:2]):
        文 = 全文(r)
        i = 文.find(词)
        窗 = re.sub(r"\s+", " ", 文[max(0, i - 20):i + 24]) if i >= 0 else ""
        print("          └ …%s…" % 窗)

# 这批词真正的战场在聊天那一侧：HR 上门第一句就写"纯佣 无底薪 GSV 提成"，
# 而打招呼记录里的 JD 字段根本没这段话。只量投递池会得出"加了等于没加"的假结论。
from boss_bot.intent import veto_hit_anywhere

会话 = []
for p in glob.glob("messages/*.json"):
    if p.endswith(".meta.json"):
        continue
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        continue
    标题 = (d.get("job_name") or "").strip()
    正文 = " ".join((m.get("content") or m.get("text") or "")
                   for m in (d.get("messages") or []) if not m.get("is_mine"))
    if 标题:
        会话.append((标题, 正文))
放会话 = [(t, b) for t, b in 会话
           if veto_hit_anywhere(类型表, 正文表, title=t, text=b) == ""]
print("\n会话存档 %d 份，按今天的判据还开着的 %d 份" % (len(会话), len(放会话)))
print("\n词      聊天侧新拦   拦下来的形状")
for 词 in 候选:
    if 词 in 正文表:
        continue
    中 = [(t, b) for t, b in 放会话
          if veto_hit_anywhere(类型表, 正文表 + [词], title=t, text=b) != ""]
    print("%-6s %4d      %s" % (词, len(中), "；".join(t[:24] for t, _ in 中[:3])))
    for t, b in 中[:2]:
        文 = t + " " + b
        i = 文.find(词)
        print("          └ …%s…" % re.sub(r"\s+", " ", 文[max(0, i - 24):i + 30]))


