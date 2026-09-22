"""
一次性数据修复脚本：补全 greet_records.json 和 reply_records.json 中的空时间戳和跳过原因。

修复内容：
1. 把所有 timestamp 字段统一为 "YYYY-MM-DD HH:MM:SS" 格式
   - 空字符串/None → 用 last_saved 或文件修改时间填充
   - ISO 格式 (含 'T') → "YYYY-MM-DD HH:MM:SS"
2. 补全 skip_reason：
   - status=skipped/failed 但 skip_reason 为空 → 根据上下文推导填写
   - ai_is_match=False → "AI判定不匹配"
   - status=failed → "投递失败"
   - status=skipped → "跳过"

用法:
    python tools/fix_records.py
"""

import json
import os
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GREET_FILE = DATA_DIR / "greet_records.json"
REPLY_FILE = DATA_DIR / "reply_records.json"


def _normalize_timestamp(ts, fallback: str) -> str:
    """规范化时间戳为 YYYY-MM-DD HH:MM:SS 格式。"""
    if not ts:
        return fallback
    s = str(ts).strip()
    if not s:
        return fallback
    if "T" in s:
        try:
            parts = s.split("T")
            date_part = parts[0]
            time_part = parts[1].split(".")[0]
            return f"{date_part} {time_part}"
        except Exception:
            return s
    return s


def _file_mtime_str(path: Path) -> str:
    """返回文件修改时间字符串。"""
    mt = datetime.fromtimestamp(path.stat().st_mtime)
    return mt.strftime("%Y-%m-%d %H:%M:%S")


def _derive_skip_reason(rec: dict) -> str:
    """根据记录上下文推导跳过原因。"""
    if rec.get("ai_is_match") is False:
        return "AI判定不匹配"
    status = rec.get("status", "")
    if status == "failed":
        return "投递失败"
    if status == "skipped":
        return "跳过"
    if rec.get("is_skipped"):
        return "跳过"
    return ""


def fix_greet_records():
    """修复打招呼记录。"""
    if not GREET_FILE.exists():
        print(f"[跳过] 文件不存在: {GREET_FILE}")
        return
    with open(GREET_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", [])
    last_saved = data.get("last_saved", "")
    fallback = _normalize_timestamp(last_saved, _file_mtime_str(GREET_FILE))

    fixed_ts = 0
    fixed_skip = 0
    for rec in records:
        old_ts = rec.get("timestamp", "")
        new_ts = _normalize_timestamp(old_ts, fallback)
        if new_ts != old_ts:
            fixed_ts += 1
        rec["timestamp"] = new_ts

        # 补全 skip_reason
        status = rec.get("status", "")
        is_skipped = rec.get("is_skipped", False)
        skip_reason = rec.get("skip_reason", "")
        if (status in ("skipped", "failed") or is_skipped) and not skip_reason:
            derived = _derive_skip_reason(rec)
            if derived:
                rec["skip_reason"] = derived
                fixed_skip += 1

    data["last_saved"] = _normalize_timestamp(last_saved, fallback)
    with open(GREET_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[greet_records] 共 {len(records)} 条，修复时间戳 {fixed_ts} 条，补全跳过原因 {fixed_skip} 条")


def fix_reply_records():
    """修复回复记录。"""
    if not REPLY_FILE.exists():
        print(f"[跳过] 文件不存在: {REPLY_FILE}")
        return
    with open(REPLY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", [])
    last_saved = data.get("last_saved", "")
    fallback = _normalize_timestamp(last_saved, _file_mtime_str(REPLY_FILE))

    fixed_ts = 0
    fixed_skip = 0
    for rec in records:
        old_ts = rec.get("timestamp", "")
        new_ts = _normalize_timestamp(old_ts, fallback)
        if new_ts != old_ts:
            fixed_ts += 1
        rec["timestamp"] = new_ts

        # 补全 skip_reason
        status = rec.get("status", "")
        is_skipped = rec.get("is_skipped", False)
        skip_reason = rec.get("skip_reason", "")
        if (status in ("skipped", "failed") or is_skipped) and not skip_reason:
            derived = _derive_skip_reason(rec)
            if derived:
                rec["skip_reason"] = derived
                fixed_skip += 1

    data["last_saved"] = _normalize_timestamp(last_saved, fallback)
    with open(REPLY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[reply_records] 共 {len(records)} 条，修复时间戳 {fixed_ts} 条，补全跳过原因 {fixed_skip} 条")


def main():
    print("=== 修复数据记录 ===")
    fix_greet_records()
    fix_reply_records()
    print("=== 完成 ===")


if __name__ == "__main__":
    main()