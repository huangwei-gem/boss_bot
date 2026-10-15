# -*- coding: utf-8 -*-
"""只读：候选否决词加进去之后，真会新拦下几条、会不会伤到他要的方向。

判据口径照生产：title_veto_hit 命中才算拦下；带职业白名单字样的（数据/标注/运营…）
本来就放行，所以"标题里含这个词"不等于"会被拦"。上一版按包含来数，把误杀算多了。
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.intent import title_is_target_shaped, title_veto_hit
from boss_bot.unified_config import TITLE_VETO_KEYWORDS_DEFAULT, UnifiedConfig

cfg = UnifiedConfig.load()
现表 = list(cfg.ai.title_veto_keywords) or list(TITLE_VETO_KEYWORDS_DEFAULT)

标题 = []
for p in glob.glob("messages/*.json"):
    if p.endswith(".meta.json"):
        continue
    try:
        t = (json.load(open(p, encoding="utf-8")).get("job_name") or "").strip()
    except Exception:
        continue
    if t:
        标题.append(t)
唯一 = sorted(set(标题))
已拦 = {t for t in 唯一 if title_veto_hit(现表, t)}
print("去重标题 %d 条，现词表（%d 条）已拦 %d 条" % (len(唯一), len(现表), len(已拦)))

候选 = ["教师", "导师", "辅导", "课程", "BD", "地推", "商务", "售前", "售后", "媒介",
        "达播", "客户经理", "保险", "理财", "顾问", "陪玩", "游戏陪玩", "招聘",
        "猎头", "学员", "试听课", "刷题", "作业", "学科", "英语", "雅思", "托福",
        "考研", "考公", "家教", "住家", "上门"]
print("\n词        真新拦  白名单放行  样例")
for 词 in 候选:
    if 词 in 现表:
        print("  %-8s 已在表里" % 词)
        continue
    命中 = [t for t in 唯一 if 词 in t and t not in 已拦]
    真拦 = [t for t in 命中 if title_veto_hit(现表 + [词], t)]
    放行 = [t for t in 命中 if not title_veto_hit(现表 + [词], t)]
    print("  %-8s %3d      %3d        %s"
          % (词, len(真拦), len(放行), "；".join(x[:34] for x in 真拦[:3])))
    for x in 放行[:2]:
        print("           └ 放行（白名单认它是方向岗）：%s" % x[:44])

# 组合口径：单个词的"真新拦"是相对现表算的，几个词互相重叠时不能相加。
# 这里一次加进去再总量，并点名方向岗里新被拦的那几条（必须为 0 才能落地）。
组合 = ["教师", "BD", "地推", "顾问", "学科", "商务", "英语"]
要的 = ["数据标注", "数据分析", "标注", "运营", "客服", "代练", "剪辑", "设计", "文案",
        "校对", "翻译", "助理"]
拦现 = {t for t in 唯一 if title_veto_hit(现表, t)}
新表 = 现表 + [w for w in 组合 if w not in 现表]
拦新 = {t for t in 唯一 if title_veto_hit(新表, t)}
print("\n组合加 %s" % "、".join(组合))
print("  拦住 %d → %d（+%d）" % (len(拦现), len(拦新), len(拦新) - len(拦现)))
伤 = [t for t in sorted(拦新 - 拦现) if any(k in t for k in 要的)]
print("  里面带他要的职业字样的 %d 条（白名单没救回的）：" % len(伤))
for t in 伤[:10]:
    print("      ！%s →「%s」" % (t[:50], title_veto_hit(新表, t)))

# 冗余检查：去掉某一个词之后总拦数掉没掉——没掉就说明它命中的标题别的词已经盖了
print("\n逐个抽掉某一个词（总拦 %d）：" % len(拦新))
for 词 in 组合:
    少它 = [w for w in 组合 if w != 词 and w not in 现表]
    表 = 现表 + 少它
    条 = {t for t in 唯一 if title_veto_hit(表, t)}
    print("  抽掉%-5s 总拦 %d %s" % (词, len(条), "（这个词冗余）" if len(条) == len(拦新) else ""))


