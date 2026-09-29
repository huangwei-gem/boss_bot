"""清掉打招呼记录里那些代码已经不会再产生的旧原因行。

为什么需要单独一个脚本：面板进程把记录全放在内存里，每次新增都会整份重写
greet_records.json。进程还在跑的时候直接改文件，下一次投递落库就把旧行又写
回来了——上一轮清理就是这么白做的。所以必须"先停面板 → 再清 → 再开面板"。

用法：
    python tools/prune_greet_records.py                 # 只看会删什么、删多少
    python tools/prune_greet_records.py --apply         # 停面板后真删（先备份）
    python tools/prune_greet_records.py --apply --reason 未找到输入框
"""

import argparse
import json
import shutil
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RETIRED_REASONS = (
    "未找到输入框",   # 三档归因改造前的平铺原因；现在的代码只会写
                      # "登录态失效 / 人机验证 / 抽屉没渲染 / 未找到聊天输入框，页面上有…"
)

DEFAULT_FILE = ROOT / "data" / "greet_records.json"


def dashboard_running(port: int = 5000) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def prune(records, reasons):
    keep, drop = [], []
    for rec in records:
        reason = rec.get("skip_reason") or ""
        if any(r in reason for r in reasons):
            drop.append(rec)
        else:
            keep.append(rec)
    return keep, drop


def main():
    ap = argparse.ArgumentParser(description="清理打招呼记录里的旧原因行")
    ap.add_argument("--apply", action="store_true", help="真删（默认只报告）")
    ap.add_argument("--file", default=str(DEFAULT_FILE), help="记录文件路径")
    ap.add_argument("--reason", action="append", default=[],
                    help=f"要清掉的原因关键字，可多次；不给则用内置清单 {RETIRED_REASONS}")
    ap.add_argument("--force", action="store_true", help="面板在跑也照删（会被内存回写，不建议）")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"找不到记录文件：{path}")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records") or []
    reasons = tuple(args.reason) or RETIRED_REASONS
    keep, drop = prune(records, reasons)

    print(f"记录总数 {len(records)} 条，命中 {len(drop)} 条（关键字：{'、'.join(reasons)}）")
    by_reason = {}
    for rec in drop:
        by_reason.setdefault(rec.get("skip_reason") or "", []).append(rec)
    for reason, rows in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        days = sorted({(r.get("timestamp") or "")[:10] for r in rows})
        print(f"  {len(rows):4d} 条 | {reason[:40]} | {days[0]} ~ {days[-1]}")

    if not args.apply:
        print("\n未做任何改动。确认无误后加 --apply。")
        return 0
    if dashboard_running() and not args.force:
        print("\n面板还在运行：进程内存里仍有这些旧行，落库时会把整份文件写回去，"
              "清理会白做。请先停面板再跑本脚本（确实要强行清理再加 --force）。")
        return 1

    backup = path.with_name(f"{path.stem}.backup_{time.strftime('%Y%m%d_%H%M%S')}.json")
    shutil.copy2(path, backup)
    data["records"] = keep
    data["total"] = len(keep)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    print(f"\n已删除 {len(drop)} 条，剩下 {len(keep)} 条。备份：{backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
