"""清理历史数据中已投递的非数据分析岗位记录。

对 data/greet_records.json 中 status 为 applied/completed 的记录逐条用
_is_data_analysis_job 逻辑检查，不匹配的：
  - 将 status 改为 "skipped"
  - 添加 skip_reason="岗位预过滤不匹配: {原因}"
  - 设置 is_skipped=True、is_greeted=False
  - 保留其他字段不变

用法:
    python tools/fix_job_filter.py            # 默认执行
    python tools/fix_job_filter.py --dry-run  # 只预览不写回
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime

# 让脚本既能从项目根目录运行，也能从 tools/ 目录运行
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

DATA_FILE = os.path.join(_PROJECT_ROOT, "data", "greet_records.json")


def _is_data_analysis_job(job_name: str) -> tuple[bool, str]:
    """复刻 GreetEngine._is_data_analysis_job 的逻辑（避免实例化复杂对象）。

    GreetEngine._is_data_analysis_job 只用到 job["job_name"]，不依赖 self
    的其它状态，因此这里直接复刻逻辑，保证与引擎内预过滤一致。
    """
    job_name = (job_name or "").strip()
    if not job_name:
        return False, "岗位名称为空"

    hard_exclude = [
        "数据标注", "数据库运维", "数据库开发", "大数据开发",
        "大数据产品", "财务分析", "项目分析", "数据管理-咨询",
        "数据工程师", "数据专员", "数据运营专员",
        "行业分析", "供应链数据分析",
        "电商管培生", "管培生",
    ]
    for kw in hard_exclude:
        if kw in job_name:
            return False, f"岗位含排除关键词'{kw}'"

    hard_include = [
        "数据分析", "数据分析师", "数据分析专员", "数据分析专家",
        "数据分析主管", "数据分析工程师", "数据分析讲师",
        "BI分析", "数据运营分析", "数据挖掘分析",
        "大数据分析", "电商数据分析", "商品数据分析",
        "反欺诈数据分析", "游戏数据分析",
    ]
    for kw in hard_include:
        if kw in job_name:
            return True, f"岗位含核心关键词'{kw}'"

    return True, "未命中关键词，需AI进一步判断"


def main() -> int:
    parser = argparse.ArgumentParser(description="清理历史数据中已投递的非数据分析岗位记录")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写回")
    parser.add_argument("--data-file", default=DATA_FILE, help="greet_records.json 路径")
    args = parser.parse_args()

    if not os.path.exists(args.data_file):
        print(f"❌ 数据文件不存在: {args.data_file}")
        return 1

    with open(args.data_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data.get("records", [])
    print(f"读取记录总数: {len(records)}")

    # 统计修复前 status 分布
    before_status = Counter(r.get("status", "?") for r in records)
    print(f"修复前 status 分布: {dict(before_status)}")

    # 只处理 applied / completed 的记录
    target_statuses = {"applied", "completed"}
    target_records = [r for r in records if r.get("status") in target_statuses]
    print(f"待检查的 applied/completed 记录数: {len(target_records)}")

    fixed_count = 0
    kept_count = 0
    fixed_details: list[tuple[str, str]] = []

    for rec in records:
        if rec.get("status") not in target_statuses:
            continue
        job_name = rec.get("job_name", "")
        is_match, reason = _is_data_analysis_job(job_name)
        if is_match:
            kept_count += 1
            continue
        # 不匹配，标记为 skipped
        rec["status"] = "skipped"
        rec["is_skipped"] = True
        rec["is_greeted"] = False
        rec["skip_reason"] = f"岗位预过滤不匹配: {reason}"
        fixed_count += 1
        fixed_details.append((job_name, reason))

    print(f"\n=== 清理结果 ===")
    print(f"保持 applied/completed 不变: {kept_count} 条")
    print(f"标记为 skipped: {fixed_count} 条")

    if fixed_details:
        print("\n被标记为 skipped 的岗位:")
        for i, (name, reason) in enumerate(fixed_details, 1):
            print(f"  {i}. {name!r}  ->  {reason}")

    # 统计修复后 status 分布
    after_status = Counter(r.get("status", "?") for r in records)
    print(f"\n修复后 status 分布: {dict(after_status)}")

    # 校验：所有被标记为 skipped 的记录都有 skip_reason
    bad = [r for r in records if r.get("status") == "skipped" and not r.get("skip_reason")]
    if bad:
        print(f"❌ 校验失败: {len(bad)} 条 skipped 记录缺少 skip_reason")
        return 2
    print("✅ 校验通过: 所有 skipped 记录都有 skip_reason")

    if args.dry_run:
        print("\n--dry-run 模式，未写回文件")
        return 0

    # 先备份原文件，再写回
    backup_path = args.data_file + ".bak"
    shutil.copy2(args.data_file, backup_path)
    print(f"\n已备份原文件到: {backup_path}")

    data["records"] = records
    data["total"] = len(records)
    data["last_saved"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(args.data_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已写回: {args.data_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
