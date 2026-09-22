# -*- coding: utf-8 -*-
"""
清理 greet_records.json / reply_records.json 中的反爬乱码。

背景：
  BOSS直聘用 Unicode 私用区字符（E030-E039）代替数字（反爬）。
  旧版 _parse_job_list() 用 texts() 整体解析，把岗位名和薪资合并到同一段，
  导致正则无法分离，数字被混入岗位名（如"分析专员822"），薪资只剩"K"。

本脚本对已有数据做一次性修复：
  1. 清理岗位名中的 Unicode 私用区字符（E000-F8FF）
  2. 把 E030-E039 映射回数字 0-9
  3. 从岗位名末尾提取被混入的薪资数字，正确分离岗位名和薪资
  4. 同步清理 company / location 等字段

用法：
    python tools/fix_garbled.py            # 默认清理 data/greet_records.json
    python tools/fix_garbled.py --reply    # 清理 data/reply_records.json
    python tools/fix_garbled.py --dry-run  # 只预览不写回
"""
import json
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"


def clean_garbled(text):
    """清理乱码：E030-E039 映射回数字，其他私用区字符（E000-F8FF）跳过。"""
    if not text or not isinstance(text, str):
        return text
    result = []
    for ch in text:
        code = ord(ch)
        if 0xE030 <= code <= 0xE039:
            result.append(str(code - 0xE030))
        elif 0xE000 <= code <= 0xF8FF:
            continue  # 跳过其他私用区字符
        else:
            result.append(ch)
    return "".join(result)


def has_garbled(text):
    """检查文本是否含 Unicode 私用区字符。"""
    if not text or not isinstance(text, str):
        return False
    return any(0xE000 <= ord(c) <= 0xF8FF for c in text)


def separate_job_salary(job_name, salary):
    """
    从岗位名中分离被混入的薪资数字，并正确合并单位。

    实际数据模式（逆向得出）：
      A. job='分析专员[8-22]'  salary='K'        → job='分析专员'  salary='8-22K'
         （数字在 job_name，单位 K 在 salary）
      B. job='大数据分析工程师[91-231]元/时'  salary=''  → salary='91-231元/时'
         （数字和单位都在 job_name）
      C. job='数据分析专员[23-28]'  salary='K·[24]薪'  → salary='23-28K·24薪'
         （数字在 job_name，K 和月薪在 salary）
      D. job='数据分析师'  salary='[26-31]K·[24]薪'  → 已正确，仅清理反爬字符
    """
    cleaned_name = clean_garbled(job_name or "")
    cleaned_salary = clean_garbled(salary or "")

    # 情况1：salary 已含完整数字段（模式D）→ 只清理 job_name 末尾残留数字
    if re.search(r"\d+[-–]\d+", cleaned_salary):
        m = re.search(r"(\d+[-–]\d+)\s*$", cleaned_name)
        if m:
            cleaned_name = cleaned_name[: m.start()].rstrip("-–—·").strip()
        return cleaned_name.rstrip("-–—·").strip(), cleaned_salary

    # 情况2：salary 无数字段，从 job_name 末尾提取
    # 先尝试完整薪资（数字+单位）：如 "91-231元/时"、"8-22K"
    m_full = re.search(
        r"(\d+[-–]\d+(?:K|k|元/[月天小时])?(?:·\d+薪)?)\s*$", cleaned_name
    )
    if m_full:
        extracted = m_full.group(1)
        real_name = cleaned_name[: m_full.start()].rstrip("-–—·").strip()
        # extracted 已含单位（如 "91-231元/时"）→ 直接用
        if re.search(r"[Kk元薪]", extracted):
            return real_name, extracted
        # extracted 只有数字（如 "8-22"）→ 合并 salary 的单位后缀
        if cleaned_salary:
            return real_name, extracted + cleaned_salary
        return real_name, extracted

    # 情况3：仅数字段无单位
    m_num = re.search(r"(\d+[-–]\d+)\s*$", cleaned_name)
    if m_num:
        num = m_num.group(1)
        real_name = cleaned_name[: m_num.start()].rstrip("-–—·").strip()
        return real_name, (num + cleaned_salary) if cleaned_salary else num

    # 情况4：无数字可提取，仅清理
    return cleaned_name.rstrip("-–—·").strip(), cleaned_salary


def fix_record(record):
    """修复单条记录，返回 (新记录, 是否改动, 改动详情)。"""
    changed = False
    details = []

    # 岗位名 + 薪资（核心修复）
    old_name = record.get("job_name", "")
    old_salary = record.get("salary", "")
    if has_garbled(old_name) or has_garbled(old_salary) or (
        old_name and re.search(r"\d+[-–]\d+\s*$", clean_garbled(old_name)) and
        (not old_salary or re.fullmatch(r"[Kk元/月天小时·薪]+", old_salary))
    ):
        new_name, new_salary = separate_job_salary(old_name, old_salary)
        if new_name != old_name:
            record["job_name"] = new_name
            changed = True
            details.append(f"job_name: {old_name!r} -> {new_name!r}")
        if new_salary != old_salary:
            record["salary"] = new_salary
            changed = True
            details.append(f"salary: {old_salary!r} -> {new_salary!r}")

    # 其他字段清理
    for field in ["company", "location", "company_location", "experience", "education"]:
        val = record.get(field, "")
        if has_garbled(val):
            new_val = clean_garbled(val)
            if new_val != val:
                record[field] = new_val
                changed = True
                details.append(f"{field}: {val!r} -> {new_val!r}")

    return record, changed, details


def fix_file(path, dry_run=False):
    """修复一个 JSON 记录文件。"""
    if not path.exists():
        print(f"[WARN] 文件不存在: {path}")
        return 0

    print(f"\n{'='*60}")
    print(f"处理文件: {path}")
    print(f"{'='*60}")

    data = json.load(open(path, encoding="utf-8"))
    is_dict = isinstance(data, dict)
    records = data.get("records", []) if is_dict else data
    total = len(records)
    print(f"共 {total} 条记录")

    fixed_count = 0
    samples = []
    for i, rec in enumerate(records):
        rec, changed, details = fix_record(rec)
        if changed:
            fixed_count += 1
            if len(samples) < 15:
                samples.append((i, details))

    print(f"修复了 {fixed_count}/{total} 条记录")

    # 打印修复样例
    print("\n── 修复样例 ──")
    for idx, details in samples:
        print(f"\n[记录 {idx}]")
        for d in details:
            print(f"  {d}")

    # 剩余乱码检查
    remaining = 0
    for rec in records:
        if has_garbled(rec.get("job_name", "")) or has_garbled(rec.get("salary", "")):
            remaining += 1
    print(f"\n剩余含乱码记录: {remaining}")

    if dry_run:
        print("[DRY-RUN] 未写回文件")
        return fixed_count

    if fixed_count > 0:
        # 备份
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = path.with_suffix(f".bak_before_garble_fix_{ts}")
        shutil.copy2(path, bak)
        print(f"[BACKUP] 已备份到 {bak}")

        if is_dict:
            data["records"] = records
            data["last_saved"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            data = records
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[OK] 已写回 {path}")

    return fixed_count


def main():
    dry_run = "--dry-run" in sys.argv
    targets = []
    if "--reply" in sys.argv:
        targets.append(DATA_DIR / "reply_records.json")
    else:
        targets.append(DATA_DIR / "greet_records.json")
    if "--all" in sys.argv:
        targets = [DATA_DIR / "greet_records.json", DATA_DIR / "reply_records.json"]

    total_fixed = 0
    for t in targets:
        total_fixed += fix_file(t, dry_run=dry_run)
    print(f"\n{'='*60}")
    print(f"总计修复 {total_fixed} 条记录")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()