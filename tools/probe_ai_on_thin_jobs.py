# -*- coding: utf-8 -*-
"""真问一遍 AI：改了提示词和方向表之后，"标题说清岗但正文空着"这类还能不能被投出去。

只发判分请求，不碰 BOSS、不点任何沟通按钮、不发送招呼（消耗的是免费接口的额度）。

为什么要有这一条：今天把"JD 没写具体工作内容一律按疑似诈骗"这句从提示词里改了
（BOSS 上数据分析/标注这类岗正文经常就一句甚至空着，标题已经写明方向），
并把 客服/运营/剪辑/标注 加进方向表。这两处都只有配置和代码，改完到底 AI 还拦不拦、
招呼语还编不编得出来，得拿真接口量，不能靠读代码说"应该没问题"。

跑法：python tools/probe_ai_on_thin_jobs.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.greet_engine import GreetEngine, jd_gate  # noqa: E402
from boss_bot.unified_config import UnifiedConfig  # noqa: E402

# 每一条都是他这几天真见过的形状；"该投"按他自己写下的方向判
# （user_profile.position = 数据标注/AI数据标注、数据分析、线上运营助理）
样例 = [
    ("该投", "外贸数据分析（线上兼职）", ""),
    ("该投", "线上数据标注专员", ""),
    ("该投", "兼职·数据标注/AI训练师", "兼职"),
    ("该投", "视频数据标注", ""),
    ("不该投", "网络客服专员（线上兼职 接受无经验）", ""),
    ("不该投", "线上视频剪辑", ""),
    ("不该投", "普工 包吃住 需坐班", "厂区直招，流水线操作，能接受两班倒。"),
    ("不该投", "兼职·高薪日结岗位", "来看看再说"),
]


def main() -> int:
    cfg = UnifiedConfig.load()
    ai = cfg.ai
    类型表 = list(ai.title_veto_keywords or [])
    正文表 = list(ai.custom_filter_keywords or [])
    方向表 = list(ai.target_job_keywords or [])
    print(f"词表：类型 {len(类型表)} 条 / 正文 {len(正文表)} 条 / 方向 {len(方向表)} 条，"
          f"提示词 {len(ai.custom_scoring_prompt or '')} 字")

    # 链要由引擎自己建：这样简历、超时、容灾顺序、只看否决词这些都走生产那一份代码，
    # 脚本里手抄一遍参数就会量到"脚本以为的配置"而不是线上那份
    engine = GreetEngine(None, cfg, log_callback=lambda msg: None)
    engine.reload_runtime_settings()
    chain = engine._init_ai()
    print(f"接口：enabled={engine._ai_enabled} providers={len(engine._ai_providers)} "
          f"阈值={engine._ai_threshold} 只看否决词={engine._ai_veto_only}")
    if chain is None or not chain.providers:
        print("AI 没启用或没有带 Key 的接口，跑不了真判分")
        return 1
    # 这些 URL 是编的，判分结果不能落进生产那份 AI 缓存
    chain.cache_enabled = False

    错 = 0
    for 期望, 标题, 正文 in 样例:
        job = {"job_name": 标题, "description": 正文, "requirements": "",
               "company": "测试公司", "salary": "兼职",
               "url": f"https://www.zhipin.com/job_detail/probe_{abs(hash(标题))}.html"}
        拦 = jd_gate(正文表, job, title_keywords=类型表, target_keywords=方向表)
        阶段 = "代码闸门" if 拦 else "AI"
        判决 = 拦 or ""
        if not 拦:
            r = chain.analyze_job(job)
            if r.get("veto_hit"):
                判决 = f"AI 填了否决词「{r['veto_hit']}」：{r.get('reason', '')[:50]}"
            elif r.get("ai_error"):
                判决 = f"AI 没给出判断：{r.get('reason', '')[:50]}"
            else:
                判决 = (f"放行（score {r.get('score')}）｜招呼语："
                        f"{(r.get('suggested_greeting') or '（空）')[:44]}")
        实际 = "不该投" if 拦 or "否决词" in 判决 else "该投"
        ok = 实际 == 期望
        错 += 0 if ok else 1
        print(f"  [{'绿' if ok else '红'}] 期望{期望} 实际{实际}｜{阶段}｜{标题[:24]}"
              f"｜{判决}")
    print(f"\n判错 {错} 条")
    return 1 if 错 else 0


if __name__ == "__main__":
    raise SystemExit(main())
