"""AI 接口体检（命令行版）— 逐个真实发一条测试消息，只有回了内容才算可用

和 Web 端 AI 设置页共用同一份结果文件 data/ai_health.json，
所以命令行跑完，界面里的状态会跟着变。

用法：
    python tools/ai_health_check.py              # 全部接口
    python tools/ai_health_check.py --index 0 --index 3
    python tools/ai_health_check.py --timeout 25
"""

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.ai_health import (load_health, merge_results, probe_all,
                                save_health, summarize)
from boss_bot.unified_config import UnifiedConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="append", type=int, default=[],
                    help="只测这些下标，可重复；默认全测")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    cfg = UnifiedConfig.load()
    providers = [{"name": p.name, "api_key": p.api_key, "api_base": p.api_base,
                  "model": p.model, "timeout": p.timeout}
                 for p in (cfg.ai.providers or [])]
    if not providers:
        print("没有配置任何 AI 接口")
        return 1

    indexes = args.index or list(range(len(providers)))
    print(f"共 {len(providers)} 个接口，本轮检测 {len(indexes)} 个，"
          f"每个最多等 {args.timeout} 秒\n")

    def show(i, p, r):
        mark = "可用 " if r["status"] == "available" else "不可用"
        extra = r["reply"] if r["status"] == "available" else r["reason"]
        lat = f"{r['latency_ms']}ms" if r.get("latency_ms") else "-"
        print(f"[{i + 1:>2}/{len(providers)}] {mark} {p['name']:<22} "
              f"{p['model']:<22} {lat:>8}  {extra[:40]}")

    results = probe_all(providers, indexes=indexes, timeout=args.timeout,
                        on_result=show)
    merged = merge_results(load_health(), results, providers)
    save_health(merged)

    # 按接口下标算，重复配置的两条要分别计入，否则 22 个只显示 20 个
    by_index = {str(r["index"]): r for r in results}
    s = summarize(by_index)
    print(f"\n本轮 {sum(1 for r in results if r['status'] == 'available')}/"
          f"{len(results)} 可用；"
          f"按全部 {s['total']} 个接口算：{s['available']} 可用 / "
          f"{s['unavailable']} 不可用，平均 {s['avg_latency_ms']}ms")
    print("结果已写入 data/ai_health.json（Web 端 AI 设置页读同一份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
