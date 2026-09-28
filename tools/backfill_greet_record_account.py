"""一次性修好 data/greet_records.json 里的账号归属。

背景：GreetEngine 以前写记录时漏传 account_index（永远是 0），
account_name 又被塞成了 cookie 文件名，所以两个号的打招呼记录
全算在主账号头上，前端没法按账号切。

引擎已修（greet_engine._record_greet 现在写真实索引和账号名），
这个脚本负责把历史数据按 cookie 文件名反查回账号。

用法：
    python -X utf8 tools/backfill_greet_record_account.py           # 预演
    python -X utf8 tools/backfill_greet_record_account.py --apply   # 真正写盘
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from boss_bot.unified_config import UnifiedConfig  # noqa: E402

RECORDS = BASE / "data" / "greet_records.json"


def main():
    apply = "--apply" in sys.argv
    cfg = UnifiedConfig.load()
    by_cookie = {}
    for i, acc in enumerate(cfg.greet.accounts):
        by_cookie[(acc.cookie_file or "zhipin_cookies.json").strip()] = (i, acc.name)
    print("账号映射（cookie 文件 -> 索引/名称）:")
    for k, v in by_cookie.items():
        print(f"  {k} -> index={v[0]} name={v[1]}")

    data = json.loads(RECORDS.read_text(encoding="utf-8"))
    records = data.get("records", [])
    changed = 0
    unknown = set()
    for r in records:
        label = (r.get("account_name") or "").strip()
        hit = by_cookie.get(label)
        if hit is None:
            # 已经是账号名（非文件名）或空：只补索引为 0 之外的情况
            if label and not label.endswith(".json"):
                idx = next((i for i, n in by_cookie.values() if n == label), None)
                if idx is not None and int(r.get("account_index") or 0) != idx:
                    r["account_index"] = idx
                    changed += 1
                continue
            if label:
                unknown.add(label)
            continue
        idx, name = hit
        if int(r.get("account_index") or 0) != idx or label != name:
            r["account_index"] = idx
            r["account_name"] = name
            changed += 1

    print(f"共 {len(records)} 条，需修正 {changed} 条")
    if unknown:
        print(f"无法识别的 account_name: {sorted(unknown)}")
    if not changed:
        print("无需写盘")
        return
    if not apply:
        print("预演模式，未写盘。加 --apply 生效。")
        return

    bak = RECORDS.with_name(
        f"greet_records.bak_account_backfill_{datetime.now():%Y%m%d_%H%M%S}.json")
    shutil.copy2(str(RECORDS), str(bak))
    data["records"] = records
    tmp = RECORDS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(RECORDS)
    print(f"已写盘，备份: {bak.name}")


if __name__ == "__main__":
    main()
