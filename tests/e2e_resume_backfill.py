# -*- coding: utf-8 -*-
"""真机补发"HR 要过简历但我们没发出去"的会话。

走的是生产链路本身（UnifiedBotLoop._backfill_pending_resumes → _process_single_chat
→ 规则命中 resume → BossChatHandler.send_resume），不另写一套点击：
脚本里自己点一遍和线上跑的不是同一段代码，测绿了也不算数。

红线：
- 不启动打招呼线程，也不构造 greet 引擎要用的搜索页动作 —— 只进会话、只发简历；
- 是否真发送由 bot_config.json 的 dry_run 决定（dry_run=true 时只记"本应发送简历"）；
- Cookie 是登录态，跑前备份 + 跑前后各算一次 sha256，两个号的都记。

用法：
    python tests/e2e_resume_backfill.py --list-only          # 只看清单，不动浏览器
    python tests/e2e_resume_backfill.py --account 1          # 补发账号2
    python tests/e2e_resume_backfill.py                      # 两个号依次补发
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.main_loop import UnifiedBotLoop  # noqa: E402
from boss_bot.pending_resume import pending_resume_asks  # noqa: E402
from boss_bot.unified_config import BASE_DIR, UnifiedConfig  # noqa: E402

COOKIE_FILES = ("zhipin_cookies.json", "zhipin_cookies_1.json")


def _sha(path: Path) -> str:
    if not path.exists():
        return "缺失"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _cookie_shas(tag: str) -> dict:
    shas = {name: _sha(BASE_DIR / name) for name in COOKIE_FILES}
    print(f"[cookie:{tag}] " + "  ".join(f"{k}={v}" for k, v in shas.items()))
    return shas


def _backup_cookies() -> Path:
    dst = BASE_DIR / "data" / "cookie_backups" / f"before_backfill_{datetime.now():%Y%m%d_%H%M%S}"
    dst.mkdir(parents=True, exist_ok=True)
    for name in COOKIE_FILES:
        src = BASE_DIR / name
        if src.exists():
            shutil.copy2(src, dst / name)
    print(f"[备份] Cookie → {dst}")
    return dst


def list_pending(account_index=None) -> dict:
    """按账号列出欠简历的会话（只读存档，不开浏览器）"""
    from boss_bot.message_store import MessageStore
    out = {}
    idxs = [account_index] if account_index is not None else [0, 1]
    for i in idxs:
        convs = [c for c in MessageStore(account_index=i).get_all_chats_detail()
                 if c.get("account_index") == i]
        out[i] = pending_resume_asks(convs)
    return out


def run_account(account_index: int, preview: bool = False) -> dict:
    """把一个账号的欠账补发掉，返回 {补发前清单, 补发后清单, 是否真发}

    preview=True 时只在内存里打开 dry_run（不写 bot_config.json）：
    进会话、读消息、规则判分全部照跑，最后那一下点击不发——
    用来在没拿到"去发"这句话之前，先把链验到"本应发送简历"。
    """
    cfg = UnifiedConfig.load()
    if preview:
        cfg.dry_run = True
    dry = bool(getattr(cfg, "dry_run", False))
    print(f"\n━━━ 账号{account_index + 1} · dry_run={dry} ━━━")
    before = list_pending(account_index)[account_index]
    for row in before:
        print(f"  欠: {row['chat_name']} | {row['company']} | {row['ask'][:34]}")
    if not before:
        print("  没有欠着的会话，不动浏览器")
        return {"before": [], "after": [], "dry_run": dry}

    loop = UnifiedBotLoop(config=cfg, account_index=account_index,
                          log_callback=lambda m: print("   ", m))
    loop._greet_paused = True          # 双保险：这个脚本绝不打招呼
    loop._running = True
    try:
        if not loop._init_browser():
            raise RuntimeError("浏览器启动失败")
        if not loop._handle_login():
            raise RuntimeError("登录未确认，停止补发（不动 Cookie 以外的东西）")
        loop.browser_manager.get_search_page()
        loop.browser_manager.get_chat_page()
        loop._init_engines()
        loop._sync_chat_tab()
        loop._backfill_pending_resumes()
    finally:
        loop.stop()
    after = list_pending(account_index)[account_index]
    return {"before": before, "after": after, "dry_run": dry}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=None, help="只处理指定账号下标（0/1）")
    ap.add_argument("--list-only", action="store_true", help="只报清单，不开浏览器")
    ap.add_argument("--preview", action="store_true",
                    help="全链路照跑但最后不点发送（只在内存里开 dry_run，不改配置文件）")
    args = ap.parse_args()

    idxs = [args.account] if args.account is not None else [0, 1]
    if args.list_only:
        for i in idxs:
            for row in list_pending(i)[i]:
                print(f"账号{i + 1} 欠: {row['chat_name']} | {row['company']} | {row['ask'][:40]}")
        return 0

    _backup_cookies()
    before_shas = _cookie_shas("before")
    results = {}
    for i in idxs:
        try:
            results[i] = run_account(i, preview=args.preview)
        except Exception as e:
            print(f"[账号{i + 1}失败] {e}")
            results[i] = {"error": str(e)}
        time.sleep(2)
    after_shas = _cookie_shas("after")

    print("\n━━━ 结果 ━━━")
    for i, r in results.items():
        if "error" in r:
            print(f"账号{i + 1}: 失败 —— {r['error']}")
            continue
        sent = len(r["before"]) - len(r["after"]) if not r["dry_run"] else 0
        print(f"账号{i + 1}: 欠 {len(r['before'])} 个，发出 {sent} 个，仍欠 {len(r['after'])} 个")
        for row in r["after"]:
            print(f"    仍欠: {row['chat_name']} | {row['ask'][:34]}")
    same = before_shas == after_shas
    print(f"Cookie: {'前后一致' if same else '有变化（登录态本身可能被刷新，已留备份）'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
