"""数据清理和修复脚本

修复内容：
1. reply_records.json: 删除测试数据(今天天气不错啊→兜底回复)和AI未启用skip记录
2. greet_records.json: 推导status和greeting_message字段
3. reply_records.json: 修复空字段(chat_name/job_name/received_message/account_name)

使用方法:
    python tools/fix_data_records.py
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REPLY_RECORDS_PATH = DATA_DIR / "reply_records.json"
GREET_RECORDS_PATH = DATA_DIR / "greet_records.json"
BOT_CONFIG_PATH = PROJECT_ROOT / "bot_config.json"

# 默认打招呼语（兜底）
DEFAULT_GREETING_MESSAGE = (
    "您好！我对这个岗位很感兴趣，方便了解一下具体情况吗？"
)

# AI未启用skip记录的skip_reason
AI_NOT_ENABLED_SKIP_REASON = "无规则/意图命中且AI未响应或未启用"


def load_json(path: Path) -> dict | list:
    """加载JSON文件"""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict | list) -> None:
    """保存JSON文件（保留中文可读性）"""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def backup_file(path: Path) -> None:
    """备份原文件"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".backup_{timestamp}.json")
    shutil.copy2(path, backup_path)
    print(f"  备份: {backup_path.name}")


def get_default_greeting_from_config() -> str:
    """从bot_config.json获取默认打招呼语"""
    try:
        config = load_json(BOT_CONFIG_PATH)
        accounts = config.get("accounts", [])
        if accounts:
            jobs = accounts[0].get("jobs", [])
            if jobs:
                gm = jobs[0].get("greeting_message", "")
                if gm:
                    return gm
    except Exception as e:
        print(f"  警告: 读取bot_config.json失败: {e}")
    return DEFAULT_GREETING_MESSAGE


def fix_reply_records() -> dict:
    """修复reply_records.json

    Returns:
        统计信息字典
    """
    print("\n[1] 修复 reply_records.json")
    backup_file(REPLY_RECORDS_PATH)

    data = load_json(REPLY_RECORDS_PATH)
    records = data.get("records", [])
    original_count = len(records)

    # 步骤1: 删除测试数据和AI未启用skip记录
    test_data_removed = 0
    ai_skip_removed = 0
    new_records = []

    for record in records:
        received = record.get("received_message", "")
        reply = record.get("reply_content", "")
        skip_reason = record.get("skip_reason", "")

        # 删除测试数据: received_message="今天天气不错啊" 且 reply_content="兜底回复"
        if received == "今天天气不错啊" and reply == "兜底回复":
            test_data_removed += 1
            continue

        # 删除AI未启用skip记录
        if skip_reason == AI_NOT_ENABLED_SKIP_REASON:
            ai_skip_removed += 1
            continue

        new_records.append(record)

    print(f"  原记录数: {original_count}")
    print(f"  删除测试数据: {test_data_removed}条")
    print(f"  删除AI未启用skip记录: {ai_skip_removed}条")

    # 步骤2: 修复空字段
    chat_name_filled = 0
    job_name_filled = 0
    received_message_filled = 0
    account_name_filled = 0

    for record in new_records:
        # chat_name为空: 用boss_name填充（如果有），否则用"未知聊天对象"
        if not record.get("chat_name"):
            boss_name = record.get("boss_name", "")
            if boss_name:
                record["chat_name"] = boss_name
            else:
                record["chat_name"] = "未知聊天对象"
            chat_name_filled += 1

        # job_name为空: 用"未知岗位"填充
        if not record.get("job_name"):
            record["job_name"] = "未知岗位"
            job_name_filled += 1

        # received_message为空: 用"(空消息)"填充
        if not record.get("received_message"):
            record["received_message"] = "(空消息)"
            received_message_filled += 1

        # account_name为空: 用"主账号"填充
        if not record.get("account_name"):
            record["account_name"] = "主账号"
            account_name_filled += 1

        # reply_content为空且is_skipped=True: 保留为空（skip记录本来就没有回复）
        # 不做处理

    print(f"  填充chat_name: {chat_name_filled}条")
    print(f"  填充job_name: {job_name_filled}条")
    print(f"  填充received_message: {received_message_filled}条")
    print(f"  填充account_name: {account_name_filled}条")

    # 更新total字段
    data["records"] = new_records
    data["total"] = len(new_records)
    data["last_saved"] = datetime.now().isoformat()

    save_json(REPLY_RECORDS_PATH, data)

    print(f"  最终记录数: {len(new_records)}")
    print(f"  total字段: {data['total']}")

    return {
        "original_count": original_count,
        "test_data_removed": test_data_removed,
        "ai_skip_removed": ai_skip_removed,
        "final_count": len(new_records),
        "chat_name_filled": chat_name_filled,
        "job_name_filled": job_name_filled,
        "received_message_filled": received_message_filled,
        "account_name_filled": account_name_filled,
    }


def fix_greet_records() -> dict:
    """修复greet_records.json

    Returns:
        统计信息字典
    """
    print("\n[2] 修复 greet_records.json")
    backup_file(GREET_RECORDS_PATH)

    data = load_json(GREET_RECORDS_PATH)
    records = data.get("records", [])
    original_count = len(records)

    default_greeting = get_default_greeting_from_config()
    print(f"  默认打招呼语: {default_greeting[:50]}...")

    status_completed = 0
    status_failed = 0
    status_skipped = 0
    greeting_from_existing = 0
    greeting_from_actual_sent = 0
    greeting_from_default = 0

    for record in records:
        skip_reason = record.get("skip_reason", "")

        # 推导status字段
        if skip_reason and "投递失败" in skip_reason:
            record["status"] = "failed"
            status_failed += 1
        elif skip_reason and "AI" in skip_reason:
            record["status"] = "skipped"
            status_skipped += 1
        elif skip_reason:
            record["status"] = "skipped"
            status_skipped += 1
        else:
            record["status"] = "completed"
            status_completed += 1

        # 推导greeting_message字段
        existing_gm = record.get("greeting_message", "")
        if existing_gm:
            record["greeting_message"] = existing_gm
            greeting_from_existing += 1
        else:
            # 尝试从actual_greeting_sent获取
            actual_sent = record.get("actual_greeting_sent", "")
            if actual_sent:
                record["greeting_message"] = actual_sent
                greeting_from_actual_sent += 1
            else:
                record["greeting_message"] = default_greeting
                greeting_from_default += 1

    print(f"  原记录数: {original_count}")
    print(f"  status=completed: {status_completed}条")
    print(f"  status=failed: {status_failed}条")
    print(f"  status=skipped: {status_skipped}条")
    print(f"  greeting_message来自已有字段: {greeting_from_existing}条")
    print(f"  greeting_message来自actual_greeting_sent: {greeting_from_actual_sent}条")
    print(f"  greeting_message来自默认值: {greeting_from_default}条")

    # 更新total字段
    data["records"] = records
    data["total"] = len(records)
    data["last_saved"] = datetime.now().isoformat()

    save_json(GREET_RECORDS_PATH, data)

    print(f"  最终记录数: {len(records)}")
    print(f"  total字段: {data['total']}")

    return {
        "original_count": original_count,
        "status_completed": status_completed,
        "status_failed": status_failed,
        "status_skipped": status_skipped,
        "greeting_from_existing": greeting_from_existing,
        "greeting_from_actual_sent": greeting_from_actual_sent,
        "greeting_from_default": greeting_from_default,
        "final_count": len(records),
    }


def verify_fixes() -> bool:
    """验证修复结果

    Returns:
        全部通过返回True，否则返回False
    """
    print("\n[3] 验证修复结果")
    all_passed = True

    # 验证reply_records
    reply_data = load_json(REPLY_RECORDS_PATH)
    reply_records = reply_data.get("records", [])

    # 检查1: 无"兜底回复"字面字符串
    fallback_count = sum(
        1 for r in reply_records if r.get("reply_content") == "兜底回复"
    )
    print(f"  reply_records中'兜底回复'数量: {fallback_count} (期望: 0)")
    if fallback_count != 0:
        print("  ✗ 失败: 仍存在'兜底回复'字符串")
        all_passed = False
    else:
        print("  ✓ 通过")

    # 检查2: 无AI未启用skip记录
    ai_skip_count = sum(
        1 for r in reply_records if r.get("skip_reason") == AI_NOT_ENABLED_SKIP_REASON
    )
    print(f"  reply_records中AI未启用skip记录: {ai_skip_count} (期望: 0)")
    if ai_skip_count != 0:
        print("  ✗ 失败: 仍存在AI未启用skip记录")
        all_passed = False
    else:
        print("  ✓ 通过")

    # 检查3: total字段正确
    print(f"  reply_records total字段: {reply_data.get('total')} (期望: {len(reply_records)})")
    if reply_data.get("total") != len(reply_records):
        print("  ✗ 失败: total字段不正确")
        all_passed = False
    else:
        print("  ✓ 通过")

    # 检查4: 空字段率<10%
    total = len(reply_records)
    if total > 0:
        chat_name_empty = sum(1 for r in reply_records if not r.get("chat_name"))
        job_name_empty = sum(1 for r in reply_records if not r.get("job_name"))
        received_empty = sum(1 for r in reply_records if not r.get("received_message"))
        account_name_empty = sum(1 for r in reply_records if not r.get("account_name"))

        chat_name_rate = chat_name_empty * 100 / total
        job_name_rate = job_name_empty * 100 / total
        received_rate = received_empty * 100 / total
        account_name_rate = account_name_empty * 100 / total

        print(f"  chat_name空率: {chat_name_rate:.1f}% (期望<10%)")
        print(f"  job_name空率: {job_name_rate:.1f}% (期望<10%)")
        print(f"  received_message空率: {received_rate:.1f}% (期望<10%)")
        print(f"  account_name空率: {account_name_rate:.1f}% (期望<10%)")

        if chat_name_rate >= 10:
            print("  ✗ 失败: chat_name空率过高")
            all_passed = False
        if job_name_rate >= 10:
            print("  ✗ 失败: job_name空率过高")
            all_passed = False
        if received_rate >= 10:
            print("  ✗ 失败: received_message空率过高")
            all_passed = False
        if account_name_rate >= 10:
            print("  ✗ 失败: account_name空率过高")
            all_passed = False
        if all_passed:
            print("  ✓ 通过")

    # 验证greet_records
    greet_data = load_json(GREET_RECORDS_PATH)
    greet_records = greet_data.get("records", [])
    greet_total = len(greet_records)

    if greet_total > 0:
        status_non_empty = sum(1 for r in greet_records if r.get("status"))
        greeting_non_empty = sum(1 for r in greet_records if r.get("greeting_message"))

        status_rate = status_non_empty * 100 / greet_total
        greeting_rate = greeting_non_empty * 100 / greet_total

        print(f"  greet_records status非空率: {status_rate:.1f}% (期望>95%)")
        print(f"  greet_records greeting_message非空率: {greeting_rate:.1f}% (期望>95%)")

        if status_rate <= 95:
            print("  ✗ 失败: status非空率不足")
            all_passed = False
        if greeting_rate <= 95:
            print("  ✗ 失败: greeting_message非空率不足")
            all_passed = False
        if all_passed:
            print("  ✓ 通过")

    # 检查greet_records total字段
    print(f"  greet_records total字段: {greet_data.get('total')} (期望: {greet_total})")
    if greet_data.get("total") != greet_total:
        print("  ✗ 失败: greet_records total字段不正确")
        all_passed = False
    else:
        print("  ✓ 通过")

    return all_passed


def main() -> int:
    """主函数

    Returns:
        退出码，0表示成功
    """
    print("=" * 60)
    print("数据清理和修复脚本")
    print("=" * 60)

    # 检查文件存在
    for path in [REPLY_RECORDS_PATH, GREET_RECORDS_PATH, BOT_CONFIG_PATH]:
        if not path.exists():
            print(f"错误: 文件不存在 - {path}")
            return 1

    # 执行修复
    reply_stats = fix_reply_records()
    greet_stats = fix_greet_records()

    # 验证
    print("\n" + "=" * 60)
    all_passed = verify_fixes()

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ 所有验证通过！")
        return 0
    else:
        print("✗ 部分验证失败，请检查上述输出")
        return 1


if __name__ == "__main__":
    exit(main())