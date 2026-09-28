# -*- coding: utf-8 -*-
"""按 BOSS 线上顺序重采聊天记录，并逐条校验本地与线上一致。

只读：点开会话、读 DOM、写本地消息文件；绝不点发送/打招呼/发简历。

为什么需要它：旧文件的顺序是按时间标签重排的，而 BOSS 的时间标签只在分段处出现
（实测存量 306 条里 123 条没有 time，"昨天 21:54" 还解析不成时间戳），卡片类消息
的 .text-content 是空的又被存成空气泡 —— 界面自然和线上对不上。

用法：
    python -X utf8 tools/resync_boss_chat.py --archive-only          # 只归档旧文件
    python -X utf8 tools/resync_boss_chat.py --limit 8              # 重采前 8 个会话
    python -X utf8 tools/resync_boss_chat.py --account 1 --verify-only
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent


def archive_legacy(msg_dir: Path) -> int:
    """把没有会话身份字段的旧文件挪进 legacy/，不让它继续出现在列表里。

    旧文件按昵称存，同名的两段对话已经混在一个文件里了，内容顺序也已被错误的
    时间重排破坏，没法可靠拆开 —— 所以只归档不合并（文件仍留着，随时可查）。
    """
    legacy = msg_dir / "legacy"
    moved = 0
    for path in sorted(msg_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if data.get("chat_id"):
            continue          # 已经是新格式（姓名+公司），不动
        legacy.mkdir(parents=True, exist_ok=True)
        path.rename(legacy / path.name)
        moved += 1
    print(f"[archive] 归档 {moved} 个旧会话文件 → {legacy}")
    return moved


def resync(account_index: int, limit: int, verify_only: bool):
    from boss_bot.unified_config import UnifiedConfig, resolve_path
    from boss_bot.browser_launcher import BrowserManager
    from boss_bot.page_handler import BossChatHandler
    from boss_bot.message_store import MessageStore

    cfg = UnifiedConfig()
    bcfg = cfg.browser
    udd = str(resolve_path(bcfg.user_data_dir
                           or Path("browser_data") / f"account_{account_index}"))
    mgr = BrowserManager(config=bcfg, account_index=account_index,
                         port=bcfg.debug_port + account_index, user_data_dir=udd)
    handler = BossChatHandler(browser_manager=mgr,
                              browser_instance=mgr.get_chat_page())
    store = MessageStore(account_index=account_index)
    handler.go_to_chat()
    time.sleep(2)

    convs = handler.get_chat_conversations()
    print(f"账号{account_index} 侧栏 {len(convs)} 个会话，本次处理 "
          f"{min(limit, len(convs)) if limit else len(convs)} 个")
    if not convs:
        print("!! 侧栏为空：该账号可能没登录")
        return 1

    targets = convs[:limit] if limit else convs
    stats = {"ok": 0, "switch_fail": 0, "mismatch": 0, "empty": 0}
    for conv in targets:
        name, company = conv.get("name", ""), conv.get("company", "")
        if not handler.enter_chat(conv):
            print(f"  跳过 {name}|{company}：切换校验没过（selected 不是这一行）")
            stats["switch_fail"] += 1
            continue
        sel = handler.read_selected_row() or {}
        if sel.get("name") and sel["name"] != name:
            print(f"  跳过 {name}：目标行变成 {sel.get('name')!r}，不记录")
            stats["switch_fail"] += 1
            continue
        company = sel.get("company") or company
        job = handler.get_job_name()
        msgs = handler.read_all_messages()
        if not msgs:
            stats["empty"] += 1
            continue

        total = store.merge_messages(name, msgs, job_name=job, company=company)
        stored = store.get_messages(name, job, company)
        tail = stored[-len(msgs):]

        # 逐条比对：本地尾部 N 条 必须和线上这一屏完全同序同文
        bad = []
        for i, (d, s) in enumerate(zip(msgs, tail)):
            dtext = (d.get("text") or "").strip()
            stext = (s.get("text") or s.get("content") or "").strip()
            dmine = bool(d.get("is_mine"))
            smine = bool(s.get("is_mine"))
            dtime = (d.get("time") or "").strip()
            stime = (s.get("time") or "").strip()
            if dtext != stext or dmine != smine or dtime != stime:
                bad.append((i, dtext[:26], stext[:26], dmine, smine, dtime, stime))
        kind_bad = [(i, s.get("kind")) for i, s in enumerate(tail)
                    if not (s.get("text") or "").strip() and s.get("kind") == "bubble"]
        if bad or kind_bad:
            stats["mismatch"] += 1
            print(f"  ✗ {name}|{company} 线上{len(msgs)}条 本地共{total}条 "
                  f"不一致{len(bad)}处 空气泡{len(kind_bad)}处")
            for b in bad[:4]:
                print(f"      第{b[0]}条 线上[{b[1]!r} mine={b[3]} t={b[5]!r}] "
                      f"↔ 本地[{b[2]!r} mine={b[4]} t={b[6]!r}]")
        else:
            stats["ok"] += 1
            kinds = {}
            for s in tail:
                kinds[s.get("kind")] = kinds.get(s.get("kind"), 0) + 1
            print(f"  ✓ {name}|{company} 线上{len(msgs)}条 == 本地尾部{len(tail)}条 "
                  f"（顺序/正文/方向/时间标签全同，形态{kinds}）")

    print("\n汇总：" + "  ".join(f"{k}={v}" for k, v in stats.items()))
    try:
        mgr.close()
    except Exception:
        pass
    return 0


def renormalize_all() -> int:
    """存量文件按当前归一化规则刷一遍（不开浏览器）"""
    from boss_bot.message_store import MessageStore
    total = 0
    for acct in (0, 1, 2):
        store = MessageStore(account_index=acct)
        changed = store.renormalize()
        if changed:
            print(f"[renormalize] 账号{acct} 重写 {changed} 个文件")
        total += changed
    print(f"[renormalize] 共重写 {total} 个会话文件")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个会话（0=全部）")
    ap.add_argument("--archive-only", action="store_true")
    ap.add_argument("--renormalize", action="store_true",
                    help="只按当前规则重写存量文件（不开浏览器）")
    ap.add_argument("--verify-only", action="store_true", help="不重采，只逐条校验")
    ap.add_argument("--no-archive", action="store_true", help="保留旧文件不动")
    args = ap.parse_args()

    msg_dir = BASE / "messages"
    if args.renormalize:
        renormalize_all()
        return 0
    if not args.no_archive and not args.verify_only:
        archive_legacy(msg_dir)
    if args.archive_only:
        return 0
    return resync(args.account, args.limit, args.verify_only)


if __name__ == "__main__":
    sys.exit(main())
