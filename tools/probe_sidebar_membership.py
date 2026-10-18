# -*- coding: utf-8 -*-
"""侧栏滚到底之后，这些"搜不到的人"到底在不在列表里、公司那格写的是什么。

为什么要它：日志里「侧栏滚到底、搜索也找不到会话」一句把三种完全不同的事混在一起
（①人根本不在 30 天列表里 ②人在但公司那格和存档对不上 ③页面没那个框）。
这里把整份侧栏滚一遍收全，按名字挑出所有行，把公司原文打出来，才能定该改哪一头。

只开临时端口 + 临时 profile + Cookie，不碰线上 9222/9223；全程只滚列表只读 DOM，
不点会话（点会把 HR 消息标已读）、不点发送。

用法：
    python tools/probe_sidebar_membership.py --account 0 --names 宋女士 李女士 苏女士
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402

CHAT_URL = "https://www.zhipin.com/web/geek/chat"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=9434)
    ap.add_argument("--names", nargs="*", default=[])
    a = ap.parse_args()

    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    fname = "zhipin_cookies.json"
    if a.account < len(accounts):
        fname = accounts[a.account].cookie_file or fname
    ck = Path(str(resolve_path(fname)))
    sha = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]

    inst = launch_browser(headless=True, port=a.port,
                          user_data_dir=tempfile.mkdtemp(prefix="boss_member_"))
    try:
        if not inst.load_cookies(str(ck)):
            print("Cookie 加载失败")
            return 1
        inst.get(CHAT_URL)
        time.sleep(8)
        ph = BossChatHandler.__new__(BossChatHandler)
        ph.page = inst
        rows = ph.get_all_chats(max_rounds=80)
        print(f"账号{a.account} 侧栏滚完共收到 {len(rows)} 行")
        names = {}
        for r in rows:
            names.setdefault(r.get("name", ""), []).append(r)
        for n in a.names:
            hit = names.get(n, [])
            print("=" * 66)
            print(f"「{n}」在侧栏里有 {len(hit)} 行")
            for r in hit:
                print("   ", json.dumps({k: r.get(k) for k in
                      ("name", "company", "job_name", "unread_count", "pinned",
                       "preview")}, ensure_ascii=False)[:220])
        # 名字对不上但公司在的，也要打出来：那说明是"名字变了"这一族
        for n in a.names:
            for r in rows:
                comp = (r.get("company") or "")
                if n[:2] in comp:
                    print("公司里带这两个字的行:", json.dumps(r, ensure_ascii=False)[:200])
    finally:
        try:
            inst.quit()
        except Exception:
            pass
    print("Cookie 未改动:", sha == hashlib.sha256(ck.read_bytes()).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
