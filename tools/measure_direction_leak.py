# -*- coding: utf-8 -*-
"""只读：方向闸现在把"任职要求里的技能句"当成方向命中，量一遍这漏掉多少名额、
收窄之后会连带杀掉几条真要的岗。

为什么要单独量：10-10 就查出 5 条（线上推广专员、线上小红书运营、预算安装工程师…），
逐条跑 target_job_hit 验过命中的词都是「数据分析」、出现位置全在"任职要求"之后
（原句是"具备较强的数据分析能力"）。这条漏一直没人收，因为它不会报错——
标题不含数据字样、却混在"数据类占比 97%"里数不出来。

收窄的代价也要一起量：有些岗真的把工作内容写在任职要求那一栏（jd_description 空着），
一刀切"只认标题+工作内容段"会把这类杀掉。所以判据要留一条退路，
退路值多少条、漏洞还剩多少条，都要现算。

跑法：python tools/measure_direction_leak.py
"""
import glob
import io
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.unified_config import UnifiedConfig

方向字段 = ("job_name", "job_description", "job_requirements", "description", "requirements")
# BOSS 详情页把"工作内容"和"任职要求"塞在同一段正文里（.job-sec-text），
# 所以按字段分不开——10-10 那 5 条就是这么漏计的：字段判会说它在 job_description，
# 其实位置在正文里"任职要求："之后。要分段量就得先找出那个小标题。
要求标题 = ("任职要求", "职位要求", "任职资格", "岗位要求", "任职条件", "录用条件")
工作标题 = ("工作内容", "岗位职责", "职位描述", "工作职责", "职位描述与")


def 取记录():
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
    return list(行.values())


def 要求段起点(文):
    """正文里第一个"任职要求"类小标题的位置；没有就返回 -1。"""
    位 = -1
    for 标 in 要求标题:
        i = 文.find(标)
        if i >= 0 and (位 < 0 or i < 位):
            位 = i
    return 位


def 命中位置(job, 表):
    """返回 (命中的词, 在哪一段)。段按"任职要求小标题之前=工作内容"切，
    标题永远算工作内容——这就是收窄之后真要的判据。"""
    for 字段 in 方向字段:
        文 = str(job.get(字段) or "")
        if not 文:
            continue
        切 = 要求段起点(文) if 字段 != "job_name" else -1
        for 词 in 表:
            k = str(词 or "").strip().lower()
            if not k:
                continue
            i = 文.lower().find(k)
            if i < 0:
                continue
            if 字段 == "job_name":
                return str(词), "标题"
            if 切 >= 0 and i >= 切:
                return str(词), "任职要求段"
            return str(词), "工作内容段"
    return "", ""


cfg = UnifiedConfig.load()
表 = list(cfg.ai.target_job_keywords or [])
print("方向表 %d 条：%s" % (len(表), "、".join(map(str, 表))))

投了 = [r for r in 取记录() if r.get("is_greeted") or r.get("status") == "applied"]

漏 = []
正 = []
认退路 = []
for r in 投了:
    job = {"job_name": r.get("job_name") or "",
           "job_description": r.get("job_description") or r.get("description") or "",
           "jd_description": r.get("job_description") or r.get("description") or "",
           "jd_requirements": r.get("job_requirements") or r.get("requirements") or "",
           "requirements": r.get("requirements") or r.get("job_requirements") or ""}
    词, 段 = 命中位置(job, 表)
    if not 词:
        continue
    if 段 in ("标题", "工作内容段"):
        正.append((r, 词, 段))
    else:
        漏.append((r, 词, 段))
        if not job["jd_description"]:
            认退路.append((r, 词))

print("\n投出去过 %d 单，其中按新口径还能对上方向的 %d 单" % (len(投了), len(正)))
print("命中只落在任职要求段（= 现在漏掉的名额）%d 单" % len(漏))
print("  里面工作内容段是空的（一刀切会连带杀掉的）%d 单" % len(认退路))
print("  命中词分布：%s" % dict(Counter(w for _, w, _ in 漏).most_common(6)))
for r, 词, 字段 in 漏[:12]:
    print("    × %s｜%s｜命中的是「%s」，位置=%s"
          % ((r.get("job_name") or "")[:26], (r.get("company") or "")[:12], 词, 字段))
print("\n  退路那些长这样（工作内容空着、只写在要求栏）：")
for r, 词 in 认退路[:6]:
    print("    · %s｜%s｜靠「%s」" % ((r.get("job_name") or "")[:26],
                                    (r.get("company") or "")[:12], 词))

# 收窄要分两半算：标题自己就写着"数据/分析"的那些（数据采集专员、数据标记员），
# 任职要求段命中其实是佐证，杀掉就是误杀；标题一个数据字样都没有的（线上运营专员、
# 服装店线上运营），命中只是"要求你会用 Excel 做数据分析"这种技能句，才是真漏。
带数据 = [x for x in 漏 if any(w in (x[0].get("job_name") or "") for w in ("数据", "分析", "标注", "标记"))]
print("\n按标题形状再分：%d 条标题里本来就写着 数据/分析/标注/标记（收窄会误杀）"
      "｜%d 条标题一个字样都没有（这才是真漏）"
      % (len(带数据), len(漏) - len(带数据)))
for r, 词, 段 in 带数据[:8]:
    print("    杀不得 × %s｜%s｜靠「%s」" % ((r.get("job_name") or "")[:26],
                                          (r.get("company") or "")[:12], 词))
