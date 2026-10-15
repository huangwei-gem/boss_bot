# -*- coding: utf-8 -*-
"""只读回看：按现在生效的否决词表，机器真发过言的那些单里有多少其实属于不要的那一类。"""
import json
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.intent import veto_hit_anywhere
from boss_bot.unified_config import UnifiedConfig

cfg = UnifiedConfig.load()
标题词 = list(cfg.ai.title_veto_keywords)
正文词 = list(cfg.ai.custom_filter_keywords)
print("生效词表：标题否决 %d 条 + 正文否决 %d 条" % (len(标题词), len(正文词)))

recs = json.load(open("data/reply_records.json", encoding="utf-8"))["records"]
发言 = [r for r in recs if not r.get("is_skipped") and (r.get("reply_content") or "").strip()]
print("\n回复记录 %d 条，其中机器真发过言 %d 条" % (len(recs), len(发言)))

拦 = collections.Counter()
漏 = []
for r in 发言:
    中 = veto_hit_anywhere(标题词, 正文词, title=r.get("job_name") or "",
                           text=r.get("received_message") or "")
    if 中:
        拦[中] += 1
    else:
        漏.append(r)
print("\n按现在的判据回看：命中 %d 条，未命中 %d 条" % (sum(拦.values()), len(漏)))
print("命中最多的 18 个词：")
for k, v in 拦.most_common(18):
    print("   %s: %d 条" % (k, v))

# 这两轮新加的 18 个词单独量一次贡献：把它们摘掉再回看同一批发言，
# 差值才是"自净化到底多拦了多少"，不然会把旧词表的功劳也算进来。
这两轮新加 = ["送餐", "管家", "中介", "小白", "日结", "保底", "免费", "推广",
              "单词", "督学", "急招", "无经验",
              "教师", "学科", "英语", "顾问", "BD", "地推"]
旧表 = [w for w in 标题词 if w not in 这两轮新加]
旧中 = sum(1 for r in 发言 if veto_hit_anywhere(旧表, 正文词, title=r.get("job_name") or "",
                                               text=r.get("received_message") or ""))
print("\n同一批发言换回旧表（%d 条词）只命中 %d 条；现表（%d 条）命中 %d 条，"
      "这两轮多拦 %d 条" % (len(旧表), 旧中, len(标题词), sum(拦.values()),
                            sum(拦.values()) - 旧中))


print("\n按日期看命中条数（否决词表是在这几轮一条条涨起来的）：")
按日 = collections.Counter((r.get("timestamp") or "")[:10] for r in 发言)
命中日 = collections.Counter((r.get("timestamp") or "")[:10] for r in 发言
                            if veto_hit_anywhere(标题词, 正文词, title=r.get("job_name") or "",
                                                 text=r.get("received_message") or ""))
for d in sorted(按日):
    print("   %s 发言 %4d 条，其中否决类 %4d 条" % (d, 按日[d], 命中日.get(d, 0)))

print("\n未命中的最近 18 条（机器还在聊、但不是否决类岗位）：")
for r in 漏[-18:]:
    print("   %s | %s | 回:%s | HR:%s" % (
        (r.get("timestamp") or "")[:16], (r.get("job_name") or "")[:30],
        (r.get("reply_content") or "")[:24],
        (r.get("received_message") or "")[:24].replace("\n", " ")))
