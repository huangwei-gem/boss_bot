# -*- coding: utf-8 -*-
"""会话归属只读体检：BOSS 每个号侧栏真实有哪些路，盘上把它们存成了哪个号。

为什么需要：界面和 BOSS 对不上时，先要分清是"没采到"还是"存错了号"。
两种病的修法完全不同，靠截图猜不出来。

只读：开自己的端口/profile、打开会话页、读 DOM、读本地文件。
绝不点发送/打招呼/发简历，也不写任何消息文件。
Cookie 红线：整轮跑前跑后逐个算 sha，必须一致。

运行：python -X utf8 tools/chat_account_attribution_check.py
"""

import hashlib
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

COOKIE_FILES = ("zhipin_cookies.json", "zhipin_cookies_1.json")


def sha_of_cookies():
    out = {}
    for name in COOKIE_FILES:
        p = BASE / name
        out[name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else None
    return out


def stored_identities(account_idx):
    """盘上这一号存了哪些会话身份（姓名#公司尾串）"""
    from boss_bot.message_store import MessageStore
    store = MessageStore(account_index=account_idx)
    out = {}
    for path in Path(store.base_dir).glob("*.json"):
        if path.name.endswith(".meta.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        m = __import__("re").match(r"^a(\d+)_", path.name)
        acct = int(data.get("account_index") if data.get("account_index") is not None
                   else (m.group(1) if m else 0))
        if acct != account_idx:
            continue
        key = (data.get("chat_name", path.stem),
               MessageStore.job_key(data.get("company") or data.get("job_name") or ""))
        out[key] = path.name
    return out


def online_rows(account_idx, cfg):
    """BOSS 这一号侧栏真实的路：用生产同一份扫描器（get_chat_conversations）"""
    from boss_bot.browser_launcher import BrowserManager
    from boss_bot.page_handler import BossChatHandler

    mgr = BrowserManager(config=cfg.browser, account_index=account_idx,
                         port=9222 + account_idx,
                         user_data_dir=str(BASE / "browser_data" / f"account_{account_idx}"))
    handler = BossChatHandler(browser_manager=mgr)
    handler.go_to_chat()
    # SPA 首屏会重排列表，扫到 0 行时再等一轮，别把"没来得及渲染"当成"线上没有"
    rows = []
    for _ in range(6):
        rows = handler.get_chat_conversations() or []
        if rows:
            break
        time.sleep(1.5)
    return rows


def main():
    from boss_bot.unified_config import UnifiedConfig
    from boss_bot.message_store import MessageStore

    cfg = UnifiedConfig.load()
    before = sha_of_cookies()
    print("Cookie 跑前 sha:", before)
    print("=" * 68)

    accounts = cfg.greet.accounts or []
    problems = 0
    for idx, acc in enumerate(accounts[:2]):
        try:
            rows = online_rows(idx, cfg)
        except Exception as e:
            print(f"账号{idx + 1}（{acc.name}）线上读取失败：{type(e).__name__}: {e}")
            continue
        stored = stored_identities(idx)
        other = stored_identities(1 - idx)
        online_unread = sum(int(r.get("unread_count") or 0) for r in rows)
        print(f"账号{idx + 1}（{acc.name}）线上 {len(rows)} 路，未读合计 {online_unread}；"
              f"本地存了 {len(stored)} 路")

        missing, misfiled = [], []
        for r in rows:
            key = (r.get("name", ""), MessageStore.job_key(r.get("company") or ""))
            if key in stored:
                continue
            (misfiled if key in other else missing).append(
                f"{key[0]}|{(r.get('company') or '')[:16]} 未读{r.get('unread_count')}")
        if missing:
            problems += len(missing)
            print(f"  线上有、本地从没存过（{len(missing)}）：" + "; ".join(missing[:8]))
        if misfiled:
            problems += len(misfiled)
            print(f"  线上有、本地却只存在**另一个号**名下（{len(misfiled)}）："
                  + "; ".join(misfiled[:8]))
        local_only = [n for k, n in stored.items()
                      if k not in {(r.get('name'), MessageStore.job_key(r.get('company') or ''))
                                   for r in rows}]
        if local_only:
            print(f"  本地有、线上这一屏看不到（{len(local_only)}，多为更早的历史会话）："
                  + "; ".join(local_only[:6]))
        if not missing and not misfiled:
            print("  线上每一路本地都存着，归属也对")
    print("=" * 68)
    after = sha_of_cookies()
    print("Cookie 跑后 sha:", after, "→", "一致" if after == before else "被改动！")
    print(f"归属类问题合计 {problems} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
