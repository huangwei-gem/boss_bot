# -*- coding: utf-8 -*-
"""把"自己找上门、命中判据却还没拒过"的存量会话逐个拒掉。

用户 2026-10-07："有一些是自己找上门来的，该拒绝的也要给人家拒绝了，而且也要显示到前端。"
这些会话多数从没被回复轮处理过（HR 主动开口，我们没投过岗），所以线上那三层闸门
根本没机会跑：盘上 188 个命中会话里还有 73 个一条拒绝话都没发过，其中不乏
"是有什么顾虑吗，可以和我说一下的"这种一直在等的。

安全边界（和 decline_offline_interviews 一样）：
- 名单现取自闭档，不在页面上复核一遍不发送；复核用的是**页面上的岗位标题 + 最近几句 HR 的话**，
  存档里的标题可能已经过期（HR 换岗位、公司已招满都会让这一单变成"其实可以聊"）。
- 进会话按 姓名+公司 双重核对，对不上就跳过，绝不按姓名猜人。
- HR 已经拒绝过我们的（"不考虑""不匹配"）不发——那句话发出去就是追着人说话。
- 我们任何一句说过拒绝话术的不再重复发。
- 卡片只在命中时点卡片上的「拒绝」，不再追一句文字。

跑法：python tools/decline_unwanted_inbound.py --check
      python tools/decline_unwanted_inbound.py --limit 20
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from decline_offline_interviews import PANEL, attach  # noqa: E402

import requests  # noqa: E402
from boss_bot.contact_ledger import _message_text  # noqa: E402
from boss_bot.intent import (classify_interview_invite, is_contact_exchange_card,  # noqa: E402
                             veto_hit_anywhere)
from boss_bot.message_store import MessageStore  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402
from boss_bot.reply_engine import (FAMILY_DECLINE_REPLY, REPLY_REFUSAL_MARK,  # noqa: E402
                                   conversation_rejected)
from boss_bot.unified_config import UnifiedConfig, TITLE_VETO_KEYWORDS_DEFAULT  # noqa: E402

# 我们自己的拒绝话术里出现这些就算这一单已经表过态，不再重复发
REFUSED_MARKS = (REPLY_REFUSAL_MARK, "先放弃", "只找线上远程就能", "祝您招聘顺利")


def tables():
    cfg = UnifiedConfig.load()
    return (list(getattr(cfg.ai, "title_veto_keywords", None)
                 or TITLE_VETO_KEYWORDS_DEFAULT),
            list(cfg.ai.custom_filter_keywords or []))


def hr_window(msgs, n=4):
    """最近 n 句 HR 的话（自己发的、系统噪声都不算）。"""
    out = []
    for m in msgs:
        if m.get("is_mine") or m.get("is_system"):
            continue
        body = _message_text(m)
        if body:
            out.append(body)
    return out[-n:]


def hit_of(title_words, body_words, title, msgs):
    """这一单是不是不该要的：标题过两张表，HR 最近的话过否决词表。"""
    hit = veto_hit_anywhere(title_words, body_words, title=title or "",
                            text=" ".join(hr_window(msgs)))
    if hit:
        return hit, "岗位类型"
    if any(classify_interview_invite(t) == "offline" for t in hr_window(msgs)):
        return "线下面试", "线下面试"
    return "", ""


def owed(limit):
    """存档里命中判据、我们又从没拒过的会话（按账号+姓名+公司去重）。"""
    title_words, body_words = tables()
    rows, seen = [], set()
    for c in MessageStore().get_all_chats_detail() or []:
        msgs = c.get("messages") or []
        if not msgs:
            continue
        key = (c.get("account_index"), c.get("chat_name"), c.get("company"))
        if key in seen:
            continue
        hit, _why = hit_of(title_words, body_words, c.get("job_name") or "", msgs)
        if not hit:
            continue
        if any(m.get("is_mine") and any(k in (_message_text(m) or "")
                                        for k in REFUSED_MARKS) for m in msgs):
            continue
        if conversation_rejected(msgs):
            continue          # HR 已经拒过我们，别再追着说话
        seen.add(key)
        rows.append({"account": int(c.get("account_index") or 0),
                     "name": c.get("chat_name"), "company": c.get("company") or "",
                     "job": c.get("job_name") or "", "hit": hit,
                     "last_hr": (hr_window(msgs, 1) or [""])[0]})
    return rows[:limit], len(rows)


def record(account, name, company, job, received, content, source, reason):
    """会话存档由本进程写（分文件合并，安全），回复记录必须让在线面板自己写。

    reply_records 是整份文件写回的：工具在自己进程里 append 完，面板下一次落盘
    就把这 68 条盖没了——2026-10-07 实测：BOSS 里的气泡还在，前端筛不到拒绝记录。
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    MessageStore(account_index=account).append_bot_message(
        name, content, job, reply_source=source, action="text", company=company)
    body = {"timestamp": ts, "chat_name": name, "job_name": job,
            "received_message": received, "reply_content": content,
            "reply_source": source, "reply_intent": "other",
            "reply_reason": reason, "account_index": account}
    try:
        r = requests.post(f"{PANEL}/api/reply_records", json=body, timeout=10)
        if r.status_code != 200:
            print(f"      （回复记录没记上：HTTP {r.status_code} {r.text[:60]}）")
    except Exception as e:
        print(f"      （回复记录接口没通：{type(e).__name__}: {str(e)[:60]}）")


def run_one(row, send):
    account, name, company = row["account"], row["name"], row["company"]
    title_words, body_words = tables()
    instance = attach(account)
    handler = BossChatHandler(browser_instance=instance)
    try:
        if not handler.enter_chat({"name": name, "company": company, "index": -1}):
            return "跳过：页面上按 姓名+公司 找不到这一行"
        live = handler.read_all_messages() or []
        live_title = handler.get_job_name() or row["job"]
        hit, why = hit_of(title_words, body_words, live_title, live)
        if not hit:
            return f"跳过：页面上不像不该要的一单（岗位「{live_title[:20]}」没命中）"
        if any(m.get("is_mine") and any(k in (_message_text(m) or "")
                                        for k in REFUSED_MARKS) for m in live):
            return "跳过：这一会话里已经说过拒绝话术"
        if not send:
            return f"核对通过：{why}「{hit}」（未发送）"
        last_hr = (hr_window(live, 1) or [""])[0]
        if is_contact_exchange_card(last_hr):
            if handler.decline_contact_exchange():
                record(account, name, company, live_title, last_hr,
                       "[已拒绝交换联系方式]", "reject_contact",
                       f"存量清剿：{why}命中「{hit}」，交换联系方式的卡片点了「拒绝」")
                return f"已点卡片「拒绝」：{why}「{hit}」"
            return f"失败：{why}「{hit}」但卡片上没有可点的「拒绝」"
        if not handler.send_text(FAMILY_DECLINE_REPLY):
            return f"失败：{why}「{hit}」，但文字没发出去"
        record(account, name, company, live_title, last_hr,
               FAMILY_DECLINE_REPLY, "family_filter",
               f"存量清剿：{why}命中「{hit}」，只找线上兼职")
        return f"已拒绝：{why}「{hit}」"
    finally:
        try:
            instance._get_active().close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只核对不发送")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--account", type=int, default=-1, help="只处理某个号，默认两个号")
    args = ap.parse_args()

    todo, total = owed(args.limit)
    if args.account >= 0:
        todo = [r for r in todo if r["account"] == args.account]
    print(f"待拒 {len(todo)} / 共 {total} 个命中且从没拒过的会话", flush=True)
    if not todo:
        return 0
    if not args.check:
        print("暂停回复轮:", requests.post(f"{PANEL}/api/pause_reply").json(), flush=True)
    results = []
    try:
        for row in todo:
            try:
                verdict = run_one(row, not args.check)
            except Exception as e:
                verdict = f"异常：{type(e).__name__}: {str(e)[:70]}"
            results.append((row, verdict))
            print(f"  号{row['account']} {row['name']}｜{row['company'][:14]}: {verdict}",
                  flush=True)
            time.sleep(1.5)
    finally:
        if not args.check:
            print("恢复回复轮:", requests.post(f"{PANEL}/api/resume_reply").json(), flush=True)

    做了 = len([1 for _, v in results if v.startswith("已")])
    print(f"\n处理 {len(results)} 单，真拒掉 {做了} 单，"
          f"跳过/失败 {len(results) - 做了} 单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
