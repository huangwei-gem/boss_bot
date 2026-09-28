# -*- coding: utf-8 -*-
"""真实量一遍 AI 筛岗接口：谁真能给出判分、谁在拖后腿。

诊断的是"AI 筛岗到底有没有生效"背后的四个具体失败模式：
1. 推理模型把预算花在 thinking 上，正文被 max_tokens 截断（实测 AskDiandian-Dots3）
2. 返回裹 markdown 代码块 / 前后夹带中文说明
3. 只给理由不给 score —— 旧代码会当成判分成功，调用方按 50 分误判成"不匹配"
4. 单次耗时到底多少（决定 60s 岗位预算内能试几个接口）

解析判定走生产同一份代码（AIAnalyzerChain._parse_completion），
脚本自己抄一套"看起来能解析"就测不出真问题。

只发分析请求，不碰 BOSS、不发送任何招呼。

用法：
    python -X utf8 tools/measure_ai_quality.py                # 所有有 Key 的接口
    python -X utf8 tools/measure_ai_quality.py --n 6 --max-tokens 1024
    python -X utf8 tools/measure_ai_quality.py --failover      # 再看一遍容灾链整体
"""
import argparse
import json
import os
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.greet_engine import AIAnalyzerChain          # noqa: E402
from boss_bot.unified_config import UnifiedConfig          # noqa: E402

JOB = {"job_name": "数据分析师（电商）", "salary": "9-14K", "company": "某电商公司",
       "url": "https://www.zhipin.com/job_detail/measure1.html",
       "description": "岗位职责：1.负责业务数据日常监控与看板搭建；2.用 SQL 取数并输出周报；"
                      "3.参与选品与库存分析；4.对接业务方梳理指标口径。"
                      "任职要求：本科及以上，统计学/数学/计算机相关专业优先，"
                      "熟悉 MySQL、Excel，会 Python 优先，有电商经验加分。",
       "requirements": "本科，1-3年经验，会 SQL 和 Excel"}


def raw_call(p, messages, max_tokens, thinking=True):
    """原样发一次请求，带回 finish_reason / usage 这些链上层看不到的遥测。"""
    payload = json.dumps({
        "model": p["model"], "messages": messages, "temperature": 0.3,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }).encode("utf-8")
    req = Request(f"{p['api_base'].rstrip('/')}/chat/completions", data=payload,
                  method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {p['api_key']}")
    t0 = time.time()
    try:
        with urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        return {"err": f"HTTP: {e}", "cost": time.time() - t0}
    except Exception as e:
        return {"err": f"{type(e).__name__}: {e}", "cost": time.time() - t0}
    cost = time.time() - t0
    try:
        choice = data["choices"][0]
    except Exception:
        return {"err": f"无 choices：{str(data)[:100]}", "cost": cost}
    msg = choice.get("message") or {}
    return {"cost": cost, "choice": choice,
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "finish": choice.get("finish_reason") or "",
            "usage": data.get("usage") or {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="只测前 N 个有 Key 的接口")
    ap.add_argument("--max-tokens", type=int, default=0,
                    help="覆盖配置里的判分预算")
    ap.add_argument("--no-thinking", action="store_true",
                    help="关掉 enable_thinking 对照（thinking 会吃掉预算和时间）")
    ap.add_argument("--failover", action="store_true",
                    help="再用真实容灾链跑一遍，看切换后的实际结果")
    args = ap.parse_args()

    cfg = UnifiedConfig.load()
    providers = [{"name": p.name, "api_key": p.api_key, "api_base": p.api_base,
                  "model": p.model, "timeout": p.timeout} for p in cfg.ai.providers
                 if p.api_key]
    budget = args.max_tokens or cfg.ai.analyze_max_tokens
    thinking = not args.no_thinking

    chain = AIAnalyzerChain(providers=providers, cache_enabled=False,
                            match_threshold=cfg.ai.match_threshold,
                            custom_filter_keywords=cfg.ai.custom_filter_keywords,
                            custom_scoring_prompt=cfg.ai.custom_scoring_prompt,
                            analyze_max_tokens=budget)
    chain.set_resume({"school": cfg.resume.school, "major": cfg.resume.major,
                      "degree": cfg.resume.degree, "skills": cfg.resume.skills,
                      "experience": cfg.resume.experience,
                      "target_position": cfg.resume.target_position,
                      "self_intro": cfg.resume.self_intro})
    messages = chain._build_prompt(JOB)

    picked = providers[:args.n] if args.n else providers
    print(f"接口 {len(providers)} 个有 Key，本次测 {len(picked)} 个；"
          f"判分预算 max_tokens={budget}，enable_thinking={thinking}\n")
    print(f"{'接口':<26}{'耗时':>7}{'finish':>9}{'正文':>6}{'思考':>6}"
          f"{'补全':>6}  生产解析结果")
    print("-" * 96)

    stats = {}
    for p in picked:
        r = raw_call(p, messages, budget, thinking)
        if r.get("err"):
            print(f"{p['name'][:25]:<26}{r['cost']:6.1f}s        -     -     -     -"
                  f"   ✗ 调用失败 {r['err'][:44]}")
            stats["调用失败"] = stats.get("调用失败", 0) + 1
            continue
        try:
            verdict = chain._parse_completion(r["choice"])
            outcome = f"✓ {verdict['score']}分 匹配={verdict['is_match']} " \
                      f"{str(verdict.get('reason', ''))[:26]}"
            stats["判分成功"] = stats.get("判分成功", 0) + 1
        except Exception as e:
            outcome = f"✗ {str(e)[:56]}"
            key = ("截断" if "截断" in str(e) else
                   "空正文" if "未返回正文" in str(e) else
                   "没有 JSON" if "没有 JSON" in str(e) else
                   "缺判分字段" if "无法判分" in str(e) else "其它")
            stats[key] = stats.get(key, 0) + 1
        comp = (r["usage"] or {}).get("completion_tokens", "-")
        print(f"{p['name'][:25]:<26}{r['cost']:6.1f}s{r['finish']:>9}"
              f"{len(r['content']):>6}{len(r['reasoning']):>6}{str(comp):>6}  {outcome}")

    if args.failover:
        print("\n容灾链整体（真实 analyze_job，含切换）：")
        t0 = time.time()
        res = chain.analyze_job(dict(JOB, url="https://www.zhipin.com/job_detail/mf.html"))
        cost = time.time() - t0
        print(f"  耗时 {cost:.1f}s  接口={chain.last_model_name}  "
              f"评分={res.get('score')} 匹配={res.get('is_match')}  "
              f"兜底={bool(res.get('ai_error'))}")
        print(f"  理由：{str(res.get('reason'))[:120]}")

    print("\n汇总：" + "  ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    ok = stats.get("判分成功", 0)
    print(f"可用率：{ok}/{len(picked)} 个接口能给出可信判分"
          f"（容灾链只要有一个能用就不会落到默认通过）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
