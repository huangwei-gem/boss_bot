# -*- coding: utf-8 -*-
"""把串号污染的会话存档里"不属于这个号"的那几条挑出来。

背景：`MessageStore._own_files` 以前把账号0 的无前缀文件也算作"本账号文件"，
账号1 的同步于是把账号0 的对话当成自己的历史并写进 `aN_*.json`。
代码已经修了（tests/test_message_store_isolation.py），存量数据要单独清。

判据不用"mid 撞车就算脏"：BOSS 的 data-mid 是消息级 id，两个号的两段对话
理论上不会给出同一个 id，但只凭 id 定罪太冒险。这里要求 (mid, 正文) 成对相同
才算复制来的——正文一样还凑巧同 id 的概率可以忽略。

默认只报告不动手：--apply 才写盘（写之前整份文件先复制到 polluted_archive/<日期>/，
用户数据只归档不删除）。
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MSG_DIR = os.path.join(BASE, "messages")
ARCHIVE = os.path.join(MSG_DIR, "polluted_archive")


def text_of(m):
    return (m.get("content") or m.get("text") or m.get("card_text") or "").strip()


def key_of(m):
    return (str(m.get("mid") or "").strip(), text_of(m))


def load(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写盘（默认只报告）")
    args = ap.parse_args()

    # 账号0 的存档按 (chat_id) 建索引：它自己不会引用别的号，是"污染源"
    a0_keys = {}
    for fn in sorted(os.listdir(MSG_DIR)):
        if not fn.endswith(".json") or fn.endswith(".meta.json"):
            continue
        if re.match(r"^a\d+_", fn):
            continue
        try:
            d = load(os.path.join(MSG_DIR, fn))
        except Exception:
            continue
        cid = d.get("chat_id") or d.get("chat_name")
        a0_keys[cid] = {key_of(m) for m in d.get("messages", []) if m.get("mid")}

    dirty = []
    for fn in sorted(os.listdir(MSG_DIR)):
        m = re.match(r"^a(\d+)_(.+\.json)$", fn)
        if not m or fn.endswith(".meta.json"):
            continue
        try:
            d = load(os.path.join(MSG_DIR, fn))
        except Exception:
            continue
        cid = d.get("chat_id") or d.get("chat_name")
        twin = a0_keys.get(cid)
        if not twin:
            continue
        msgs = d.get("messages", []) or []
        keep = [x for x in msgs if key_of(x) not in twin]
        dropped = len(msgs) - len(keep)
        if dropped:
            dirty.append((fn, int(m.group(1)), cid, dropped, len(msgs), len(keep)))

    if not dirty:
        print("没有发现与账号0 逐条同 (mid, 正文) 的 aN_ 存档：存量是干净的。")
        return 0
    total_drop = sum(x[3] for x in dirty)
    empty_after = sum(1 for x in dirty if x[5] == 0)
    print(f"受影响存档 {len(dirty)} 个，其中判为复制来的消息 {total_drop} 条，"
          f"清完会变空的 {empty_after} 个")
    for fn, acct, cid, dropped, before, after in dirty:
        print(f"  a{acct} {cid}: {before} 条 → 留 {after} 条（去掉 {dropped} 条与账号0 相同的）")
    if not args.apply:
        print("\n只报告。要动手加 --apply（写之前整份文件先归档到 messages/polluted_archive/）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(ARCHIVE, stamp)
    os.makedirs(dest, exist_ok=True)
    changed = 0
    for fn, _acct, _cid, _dropped, _before, after in dirty:
        src = os.path.join(MSG_DIR, fn)
        shutil.copy2(src, os.path.join(dest, fn))
        d = load(src)
        twin = a0_keys[d.get("chat_id") or d.get("chat_name")]
        keep = [x for x in d.get("messages", []) if key_of(x) not in twin]
        if after == 0:
            # 整份都是别人的对话：文件挪进归档目录，messages/ 里不再留它
            shutil.move(src, os.path.join(dest, fn + ".emptied"))
        else:
            d["messages"] = keep
            d["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with io.open(src, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        changed += 1
    print(f"已处理 {changed} 个存档，原件备份在 {os.path.relpath(dest, BASE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
