# -*- coding: utf-8 -*-
"""清除修复前留下的"HR已拒绝过"误判跳过记录（带时间戳备份）。

背景：2026-09-27 之前，防骚扰判定是按昵称跨会话扫历史记录，
只要同名 HR 里有一条拒绝就全部标跳过，产生了大批错误的 skip 记录。
判定范围已收敛到"当前这段对话自己以拒绝收尾"，修复后新产生的记录是真的。

用法：python tools/purge_stale_rejection_skips.py [--apply]
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RECORDS = BASE / "data" / "reply_records.json"
REASONS = ("HR已拒绝过，不再回复", "HR历史已拒绝，不再推销")
FIX_DATE = "2026-09-27"   # 修复上线当天，之前的同名跨会话判定不可信


def main():
    apply = "--apply" in sys.argv
    data = json.loads(RECORDS.read_text(encoding="utf-8"))
    recs = data if isinstance(data, list) else data.get("records", [])
    stale = [x for x in recs
             if x.get("skip_reason") in REASONS
             and (x.get("timestamp") or "")[:10] < FIX_DATE]
    kept = [x for x in recs if x not in stale]
    print(f"总记录 {len(recs)} | 待清除 {len(stale)} | 保留 {len(kept)}")
    for d in sorted({(x.get('timestamp') or '')[:10] for x in stale}):
        n = sum(1 for x in stale if (x.get('timestamp') or '')[:10] == d)
        print(f"   {d}: {n} 条")
    if not apply:
        print("\n未写文件（加 --apply 执行）")
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = RECORDS.with_name(f"reply_records.bak_before_purge_{stamp}.json")
    shutil.copy2(RECORDS, backup)
    print(f"已备份：{backup.name}")
    if isinstance(data, list):
        out = kept
    else:
        data["records"] = kept
        data["total"] = len(kept)   # total 是展示字段，不跟着改会一直显示旧条数
        out = data
    tmp = RECORDS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(RECORDS)
    print(f"完成：{RECORDS.name} 现在 {len(kept)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
