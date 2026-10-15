# -*- coding: utf-8 -*-
"""只读：按"这一类岗长什么样"独立打标，再看现在的词表把每一类拦住了几成。

为什么不直接拿否决词自己数：那等于用被测的尺子量自己，永远 100%。
这里的类判据（主播/销售/骗收费/普工/配送/家教）是另写的语义标记，和生产词表无关，
所以拦没拦住才是真分数。方向岗那一类反过来只量误杀。

口径照生产：标题带类字样 = 这份工本身是这类，走标题那一遍；
标题没交代岗位性质的，才由正文兜底（body_veto 只在标题不像方向岗时量正文）。

跑法：python tools/audit_class_coverage.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.intent import (title_is_target_shaped, title_veto_hit,
                             veto_hit_anywhere)
from boss_bot.unified_config import UnifiedConfig

# 类判据：只写"这类岗位的字面特征"，不抄生产词表
类判据 = {
    "主播/直播": ["主播", "团播", "娱乐主播", "语音主播", "出镜", "直播", "MCN", "公会", "带货"],
    "销售/地推": ["销售", "地推", "电话销售", "客户经理", "业务员", "市场推广", "招商",
               "房产", "置业顾问", "保险", "理财", "BD"],
    "骗钱收费": ["押金", "保证金", "培训费", "交钱", "缴费", "收费", "体检费", "服装费",
               "刷单", "垫付", "日入", "无门槛", "高佣金", "拉人", "合伙人", "股权", "分红",
               "招代理", "加盟", "贷款", "信贷"],
    "普工/工厂": ["普工", "操作工", "技工", "焊工", "电工", "钳工", "冲压", "注塑", "SMT",
               "车间", "工厂", "作业员", "组装", "质检", "仓管", "分拣", "打包", "装卸",
               "贴标", "电子厂", "进厂"],
    "跑腿/配送": ["快递", "配送", "外卖", "骑手", "送餐", "美团", "饿了么", "网约车",
               "代驾", "货运", "司机", "搬家"],
    "服务行业": ["保安", "保洁", "服务员", "收银", "传菜", "前厅", "客房", "洗车", "月嫂",
               "保姆", "护理", "养老"],
    "家教/培训": ["家教", "老师", "教师", "辅导", "督学", "课程顾问", "助教", "单词", "陪练"],
}


def 载全量():
    行 = []
    for p in glob.glob("messages/*.json"):
        if p.endswith(".meta.json"):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        标题 = (d.get("job_name") or "").strip()
        if not 标题:
            continue
        正文 = " ".join(
            (m.get("content") or m.get("text") or "")
            for m in (d.get("messages") or []) if not m.get("is_mine"))
        行.append({"title": 标题, "body": 正文})
    return 行


def 命中词(文本, 标记):
    return [k for k in 标记 if k in 文本]


def main():
    cfg = UnifiedConfig.load()
    标题表 = list(cfg.ai.title_veto_keywords) or []
    正文表 = list(cfg.ai.custom_filter_keywords or [])
    行 = 载全量()
    唯一 = sorted({r["title"] for r in 行})
    按标题 = {t: r for r in 行 for t in [r["title"]]}
    print("存档 %d 份 / 去重标题 %d 条；标题否决词 %d 条、正文否决词 %d 条"
          % (len(行), len(唯一), len(标题表), len(正文表)))

    print("\n类           标题带字样  被拦  拦住率   标题没交代、正文兜底拦住")
    总拦 = 总条 = 0
    for 类, 标记 in 类判据.items():
        标字样 = [t for t in 唯一 if 命中词(t, 标记)]
        拦标 = [t for t in 标字样 if title_veto_hit(标题表, t)]
        # 标题里没这类字样、但 HR 正文说了的：生产只在"标题不像方向岗"时才量正文
        兜底 = [t for t in 唯一
                if not 命中词(t, 标记) and 命中词(按标题[t]["body"], 标记)]
        拦兜 = [t for t in 兜底 if t not in 标字样
                and veto_hit_anywhere(标题表, 正文表, title=t,
                                      text=按标题[t]["body"])]
        总拦 += len(拦标)
        总条 += len(标字样)
        print("%-10s %6d    %5d   %5.1f%%   %d/%d"
              % (类, len(标字样), len(拦标),
                 100.0 * len(拦标) / (len(标字样) or 1), len(拦兜), len(兜底)))
        漏 = [t for t in 标字样 if t not in 拦标]
        if 漏:
            for t in 漏[:12]:
                词 = title_veto_hit(标题表, t)
                print("      × 漏网 %s%s" % (t[:52], ("（%s 放行）" % 词) if 词 else ""))
    print("\n标题带类字样合计 %d 条，标题那一遍拦住 %d 条（%.1f%%）"
          % (总条, 总拦, 100.0 * 总拦 / (总条 or 1)))

    # 误杀门：标题本身是方向岗形状，却被标题遍拦下的——按设计只该剩 NO_EXEMPT 那几条
    误杀 = [t for t in 唯一 if title_is_target_shaped(t) and title_veto_hit(标题表, t)]
    print("\n方向岗形状却仍被标题遍拦下 %d 条（这些必须都是硬否决词，如「合伙人」）：" % len(误杀))
    for t in 误杀[:12]:
        print("      %s →「%s」 %s" % (t[:46], title_veto_hit(标题表, t),
                                      "硬否决" if title_veto_hit(标题表, t) else ""))

    # 他点名的 5 个方向，一条都不许被拦
    点名 = {"数据": ["数据标注", "数据分析"], "线上运营": ["运营"], "电商客服": ["客服"],
            "游戏代练": ["代练"], "剪辑": ["剪辑"]}
    print("\n点名方向（标题带该字样、且不带任何类字样）被拦的：")
    for 方向, 词们 in 点名.items():
        候选 = [t for t in 唯一 if any(k in t for k in 词们)
                and not any(k in t for 标 in 类判据.values() for k in 标)]
        拦 = [t for t in 候选 if title_veto_hit(标题表, t)]
        print("  %-6s %3d 条候选，被拦 %d 条 %s"
              % (方向, len(候选), len(拦),
                 "；".join("%s→「%s」" % (t[:26], title_veto_hit(标题表, t))
                           for t in 拦[:3])))


if __name__ == "__main__":
    main()
