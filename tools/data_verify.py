# -*- coding: utf-8 -*-
"""数据验证脚本：检查messages目录、时间戳格式、日志重复。"""
import json
import os
import re
import glob

MESSAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "messages")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "flask-version", "flask_test.log")

results = {
    "total_files": 0,
    "total_messages": 0,
    "system_messages": [],
    "bad_timestamps": [],
    "timestamp_format_ok": True,
    "no_system_messages": True,
    "log_total_lines": 0,
    "log_duplicate_lines": [],
    "log_no_duplicates": True,
    "errors": [],
}

TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def main():
    # 1. 遍历messages目录所有JSON文件
    json_files = sorted(glob.glob(os.path.join(MESSAGES_DIR, "*.json")))
    results["total_files"] = len(json_files)
    print(f"[1] messages目录JSON文件数: {len(json_files)}")

    for fpath in json_files:
        fname = os.path.basename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            results["errors"].append(f"读取{fname}异常: {e}")
            continue

        # 提取消息列表（可能是dict或list，消息可能在messages字段或根级）
        msgs = []
        if isinstance(data, list):
            msgs = data
        elif isinstance(data, dict):
            if "messages" in data and isinstance(data["messages"], list):
                msgs = data["messages"]
            elif "message_list" in data and isinstance(data["message_list"], list):
                msgs = data["message_list"]
            else:
                # 可能整个dict就是一条消息
                if "sender" in data or "time" in data:
                    msgs = [data]

        for i, m in enumerate(msgs):
            if not isinstance(m, dict):
                continue
            results["total_messages"] += 1

            # 检查sender=="system"
            sender = m.get("sender", "")
            if sender == "system":
                results["system_messages"].append({
                    "file": fname,
                    "index": i,
                    "content": str(m.get("content", ""))[:80],
                })

            # 检查时间戳格式
            t = m.get("time", "")
            if t and not TIME_PATTERN.match(str(t)):
                results["bad_timestamps"].append({
                    "file": fname,
                    "index": i,
                    "time": str(t),
                })

    results["no_system_messages"] = len(results["system_messages"]) == 0
    results["timestamp_format_ok"] = len(results["bad_timestamps"]) == 0
    print(f"[2] 总消息数: {results['total_messages']}")
    print(f"[3] system消息数: {len(results['system_messages'])} -> no_system={results['no_system_messages']}")
    if results["system_messages"]:
        print(f"    system消息示例(前5): {results['system_messages'][:5]}")
    print(f"[4] 时间戳格式异常数: {len(results['bad_timestamps'])} -> format_ok={results['timestamp_format_ok']}")
    if results["bad_timestamps"]:
        print(f"    异常时间戳示例(前10): {results['bad_timestamps'][:10]}")

    # 2. 检查日志不重复
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            log_lines = [line.rstrip("\n") for line in f]
        results["log_total_lines"] = len(log_lines)
        seen = {}
        for i, line in enumerate(log_lines):
            if line in seen:
                results["log_duplicate_lines"].append({
                    "line": i + 1,
                    "content": line[:100],
                    "first_seen": seen[line] + 1,
                })
            else:
                seen[line] = i
        results["log_no_duplicates"] = len(results["log_duplicate_lines"]) == 0
        print(f"[5] 日志总行数: {len(log_lines)}, 重复行数: {len(results['log_duplicate_lines'])} -> no_dup={results['log_no_duplicates']}")
        if results["log_duplicate_lines"]:
            print(f"    重复行示例(前5): {results['log_duplicate_lines'][:5]}")
    except Exception as e:
        results["errors"].append(f"读取日志异常: {e}")
        print(f"[5] 读取日志异常: {e}")

    print("\n=== JSON结果 ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()