"""清理 messages/ 目录中的 system 跳过消息。

遍历 messages/*.json，删除 sender=="system" 或 content 以 "[跳过]" 开头的消息，
只保留真实 HR 消息和 bot 回复消息。

用法：
    python -m tools.clean_skip_messages          # 清理 messages/
    python -m tools.clean_skip_messages --dry    # 只统计不实际删除（dry-run）
"""
import argparse
import json
import sys
from pathlib import Path


def _is_skip_message(msg: dict) -> bool:
    """判断是否为应被清理的跳过/系统消息。"""
    sender = (msg.get("sender") or "").strip()
    content = (msg.get("content") or msg.get("text") or "").strip()
    if sender == "system":
        return True
    if content.startswith("[跳过]"):
        return True
    if msg.get("is_skipped") is True and sender != "bot" and sender != "hr":
        return True
    return False


def clean_dir(messages_dir: Path, dry_run: bool = False) -> dict:
    """清理指定目录下的所有会话 JSON 文件。

    Returns:
        统计信息 dict：{files, total_removed, files_modified, total_kept}
    """
    stats = {
        "files": 0,
        "total_removed": 0,
        "files_modified": 0,
        "total_kept": 0,
    }
    for path in sorted(messages_dir.glob("*.json")):
        stats["files"] += 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARN] 读取失败 {path.name}: {e}", file=sys.stderr)
            continue

        msgs = data.get("messages", [])
        original_count = len(msgs)
        kept = [m for m in msgs if not _is_skip_message(m)]
        removed = original_count - len(kept)
        stats["total_kept"] += len(kept)

        if removed == 0:
            continue

        stats["total_removed"] += removed
        stats["files_modified"] += 1

        if dry_run:
            print(f"[DRY] {path.name}: 将删除 {removed} 条跳过消息，"
                  f"保留 {len(kept)} 条")
            continue

        data["messages"] = kept
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[OK]  {path.name}: 删除 {removed} 条，保留 {len(kept)} 条")
        except Exception as e:
            print(f"[ERR] 写入失败 {path.name}: {e}", file=sys.stderr)

    return stats


def main():
    parser = argparse.ArgumentParser(description="清理 messages/ 目录中的 system 跳过消息")
    parser.add_argument("--dry", action="store_true", help="只统计不实际删除")
    parser.add_argument("--dir", default="messages", help="messages 目录路径")
    args = parser.parse_args()

    messages_dir = Path(args.dir).resolve()
    if not messages_dir.exists():
        print(f"目录不存在: {messages_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"开始清理 {messages_dir} ...")
    stats = clean_dir(messages_dir, dry_run=args.dry)
    print("\n=== 清理统计 ===")
    print(f"扫描文件数:   {stats['files']}")
    print(f"修改文件数:   {stats['files_modified']}")
    print(f"删除消息总数: {stats['total_removed']}")
    print(f"保留消息总数: {stats['total_kept']}")
    if args.dry:
        print("(dry-run 模式，未实际删除)")


if __name__ == "__main__":
    main()