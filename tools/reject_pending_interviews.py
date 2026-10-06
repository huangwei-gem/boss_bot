# -*- coding: utf-8 -*-
"""把已经发过来、又不合目标的面试邀请逐个点掉「拒绝」。

用户 2026-10-06（面试日程截图里两条"待接受"都写着线下面试）：
"我只要线上的…这种线下的就不合适直接拒绝就行了，他发面试邀请你直接拒绝"。
这是放行的真实操作，不是实测。

安全边界：
- 只处理存档里带"邀请您…面试"卡片、而且被 intent.classify_interview_invite
  判成 offline（现场/线下）的会话；判不出地点的一律不动，留给人工。
- 按端口连回该账号本来就在用的浏览器（9222+index），另开一个标签页操作，
  不新起浏览器、不碰 profile、不动 Cookie；用完关标签页（关不掉也只影响内存，不影响结论）。
- 跑之前暂停回复轮（同一浏览器里两个驱动者会抢会话选择），跑完恢复。

跑法：python tools/reject_pending_interviews.py --check   # 只看会不会点
      python tools/reject_pending_interviews.py            # 真点
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402

from decline_offline_interviews import PANEL, attach  # noqa: E402

from boss_bot.intent import classify_interview_invite  # noqa: E402
from boss_bot.message_store import MessageStore  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402


def offline_invites():
    """存档里带面试邀请卡片、且判成现场/线下的会话。"""
    out = []
    for row in MessageStore().get_all_chats_detail():
        for m in row.get("messages") or []:
            body = str(m.get("card_text") or m.get("text") or "")
            if "面试" in body and "邀请" in body and m.get("is_mine"):
                continue
            if classify_interview_invite(body) == "offline":
                out.append({"account": int(row.get("account_index") or 0),
                            "name": row.get("chat_name"), "company": row.get("company"),
                            "job": row.get("job_name") or "", "card": body[:60]})
                break
    return out


def run_one(row, send):
    instance = attach(row["account"])
    handler = BossChatHandler(browser_instance=instance)
    try:
        if not handler.enter_chat({"name": row["name"], "company": row["company"], "index": -1}):
            return "跳过：侧栏和搜索都找不到这家会话"
        verdict = handler.reject_interview_invite(execute=send, expect=row["company"] or "")
        if verdict == "no-btn" and handler.open_interview_invite():
            # 「立即查看」展开的面板是异步渲染的，实测 2~5 秒。之前这里固定睡 2 秒，
            # --check 就把 8 个里的 7 个报成"没有可点的拒绝"——不是没按钮，是没等到。
            verdict = handler.reject_interview_invite(execute=send, wait_sec=8,
                                                      expect=row["company"] or "")
        if not send:
            return {"found": "有可点的「拒绝」（--check 未点）",
                    "ambiguous": "挂着好几块面试面板、公司名挑不出这一单，不动",
                    "no-btn": "页面上没有可点的「拒绝」（多半已取消/过期）"}[verdict]
        if verdict not in ("clicked", "clicked-confirmed"):
            return f"没点成：{verdict}"
        # 点完再找一次：按钮还在就说明没真拒掉，别报成成功
        time.sleep(2)
        return "已拒绝" if handler.reject_interview_invite(execute=False) == "no-btn" \
            else "点了，但按钮还在（可能没生效）"
    finally:
        try:
            instance._get_active().close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    rows = offline_invites()
    print(f"现场/线下的面试邀请会话：{len(rows)} 个")
    send = not args.check
    if send:
        print("暂停回复轮:", requests.post(f"{PANEL}/api/pause_reply").json(), flush=True)
    results = []
    try:
        for r in rows:
            try:
                results.append((r, run_one(r, send)))
            except Exception as e:
                results.append((r, f"异常：{type(e).__name__}: {str(e)[:70]}"))
            time.sleep(1.5)
    finally:
        if send:
            print("恢复回复轮:", requests.post(f"{PANEL}/api/resume_reply").json(), flush=True)

    done = 0
    for r, verdict in results:
        if verdict == "已拒绝":
            done += 1
        print(f"  账号{r['account']} {r['name']}｜{r['company']}: {verdict}  ← {r['card']}")
    print(f"\n已拒绝 {done} / 处理 {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
