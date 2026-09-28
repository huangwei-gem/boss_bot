# -*- coding: utf-8 -*-
"""诊断 AI 筛岗返回为什么解析不了（真实调用一次，打印原始响应）。

容灾链里不同的接口返回风格差别很大：有的输出纯 JSON，有的裹 markdown 代码块，
有的先输出一段中文再给 JSON，还有的是 reasoning 模型 content 为空。
解析一旦失败就落到"默认通过"，等于这个岗位没被 AI 筛过，但界面上看不出来。

用法：python tools/diagnose_ai_parse.py [--jobs N]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SAMPLE_JOBS = [
    {"job_name": "数据分析师（电商）", "salary": "9-14K", "company": "某电商公司",
     "url": "https://www.zhipin.com/job_detail/diagnose1.html",
     "description": "岗位职责：1.负责业务数据日常监控与看板搭建；2.用 SQL 取数并输出周报；"
                    "3.参与选品与库存分析。任职要求：本科及以上，统计学/数学相关专业优先，"
                    "熟悉 MySQL、Excel，会 Python 优先。",
     "requirements": "本科，1-3年经验，会 SQL 和 Excel"},
    {"job_name": "亚马逊运营专员", "salary": "8-12K", "company": "某跨境贸易公司",
     "url": "https://www.zhipin.com/job_detail/diagnose2.html",
     "description": "负责亚马逊店铺日常运营、上架、广告投放与售后处理，需英语读写流利。",
     "requirements": "英语四六级，有亚马逊实操经验"},
    {"job_name": "数据分析师实习生（线上）", "salary": "211-231元/天", "company": "某数据服务",
     "url": "https://www.zhipin.com/job_detail/diagnose3.html",
     "description": "协助整理与清洗数据，制作日报，参与数据看板需求讨论。可长期线上实习。",
     "requirements": "在校生，每周到岗4天以上"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=len(SAMPLE_JOBS))
    args = ap.parse_args()

    from boss_bot.greet_engine import AIAnalyzerChain
    from boss_bot.unified_config import UnifiedConfig

    cfg = UnifiedConfig.load()
    providers = [{"name": p.name, "api_key": p.api_key, "api_base": p.api_base,
                  "model": p.model, "timeout": p.timeout} for p in cfg.ai.providers]
    empty = [p["name"] for p in providers if not p["api_key"]]
    print(f"接口 {len(providers)} 个（其中空 Key {len(empty)} 个），"
          f"跳过不可用={cfg.ai.skip_unhealthy}，阈值={cfg.ai.match_threshold}")

    chain = AIAnalyzerChain(providers=providers, match_threshold=cfg.ai.match_threshold,
                            cache_enabled=False, skip_unhealthy=cfg.ai.skip_unhealthy)
    chain.set_resume({"school": cfg.resume.school, "major": cfg.resume.major,
                      "degree": cfg.resume.degree, "skills": cfg.resume.skills,
                      "experience": cfg.resume.experience,
                      "target_position": cfg.resume.target_position,
                      "self_intro": cfg.resume.self_intro})

    bad = 0
    for job in SAMPLE_JOBS[:args.jobs]:
        res = chain.analyze_job(job)
        raw = chain.last_raw_response or ""
        err = bool(res.get("ai_error"))
        bad += 1 if err else 0
        print("\n" + "=" * 62)
        print(f"岗位：{job['job_name']}   模型：{chain.last_model_name}")
        print(f"解析：{'失败/兜底' if err else '成功'}   "
              f"评分={res.get('score')} 匹配={res.get('is_match')}")
        print(f"理由：{str(res.get('reason'))[:120]}")
        print(f"原始响应（{len(raw)} 字符）：")
        print(raw[:900] if raw else "  <空：content 与 reasoning_content 都没有正文>")
    print("\n" + "=" * 62)
    print(f"结论：{bad}/{args.jobs} 个岗位未被 AI 真正判分（落到默认通过）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
