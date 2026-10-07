# -*- coding: utf-8 -*-
"""把 HR 发起了交换、但我们没点同意的那些会话补上，号才拿得到。

用户 2026-10-06："你要把之前的给补上啊微信，电话之类的"。
台账里 39 行的说法是「发起交换请求」——BOSS 的号只有点了卡片上的「同意」才放出来，
回复轮本来会自动点，但实测这些会话大多在侧栏虚拟列表里找不到行
（"⏭️ 跟进跳过：会话切换校验失败" 今天刷屏），所以一直没点到。

这里用聊天页自带的"搜索30天内的联系人"把人筛出来再点，绕开只渲染 40 行的问题。
只点 .message-card-top-title 含"是否同意"+"微信/电话"的那张卡片上的「同意」，
点不到就跳过，绝不顺手点页面上别的同名按钮。

跑法：python tools/accept_pending_contacts.py --check
      python tools/accept_pending_contacts.py --limit 12
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from decline_offline_interviews import PANEL, attach  # noqa: E402

from boss_bot.contact_ledger import contact_rows, extract_contacts, _message_text  # noqa: E402
from boss_bot.intent import veto_hit_anywhere  # noqa: E402
from boss_bot.message_store import MessageStore  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402
from boss_bot.unified_config import UnifiedConfig  # noqa: E402

# 只探不点：报告这一行现在到底有没有可同意的卡片
PROBE_JS = '''(
    function() {
        var cards = document.querySelectorAll(".message-card-wrap");
        var titles = [];
        for (var i = 0; i < cards.length; i++) {
            var t = cards[i].querySelector(".message-card-top-title");
            if (t) titles.push((t.textContent || "").trim().slice(0, 30));
        }
        return JSON.stringify(titles);
    }
)()'''

# 点不动的时候要知道卡在哪儿：卡片在不在、按钮文字是什么、有没有矩形
WHY_JS = '''(
    function() {
        var out = [];
        var cards = document.querySelectorAll(".message-card-wrap");
        for (var i = 0; i < cards.length; i++) {
            var t = cards[i].querySelector(".message-card-top-title");
            var tx = t ? (t.textContent || "").trim().slice(0, 24) : "";
            if (tx.indexOf("是否同意") < 0) continue;
            var btns = cards[i].querySelectorAll(".message-card-buttons .card-btn");
            var one = [];
            for (var j = 0; j < btns.length; j++) {
                one.push((btns[j].textContent || "").trim() + ":"
                         + btns[j].getClientRects().length);
            }
            out.push(tx + " [" + (one.join(" ") || "没有按钮") + "]");
        }
        return JSON.stringify(out);
    }
)()'''



def pending_rows(limit):
    chats = MessageStore().get_all_chats_detail()
    rows = contact_rows(chats)
    todo = [r for r in rows if r["contact_kind"] == "发起交换请求"]
    return todo[:limit], len(todo)


def family_hit(row, job_name=""):
    """这一行是不是普工/主播/快递/保洁那一类（判据和线上回复轮完全同一套）。

    补点工具以前是无条件点「同意」的：一跑就把这批号的微信/电话全交出去，
    而用户明确说过这一类「别同意，直接拒绝就行」。
    """
    cfg = UnifiedConfig.load().apply_account(int(row.get("account_index") or 0))
    title = job_name or row.get("job_name", "")
    return veto_hit_anywhere(cfg.ai.title_veto_keywords,
                             cfg.ai.custom_filter_keywords,
                             title=title, text=row.get("hr_last_message", ""))


def run_one(row, send):
    account = int(row.get("account_index") or 0)
    name, company = row["chat_name"], row["company"]
    instance = attach(account)
    handler = BossChatHandler(browser_instance=instance)
    try:
        # 不再自己往搜索框里打字：enter_chat 现在滚不到就会自己用搜索点开，
        # 并且用完会清框。这里先打一遍的话，人正好在屏幕上时那条筛条没人清。
        if not handler.enter_chat({"name": name, "company": company, "index": -1}):
            return name, company, "跳过：搜索也没把这行会话找出来", ""
        live_job = handler.get_job_name() or ""
        hit = family_hit(row, live_job)
        if hit and send:
            if not handler.decline_contact_exchange():
                return name, company, f"跳过：命中「{hit}」但没点到「拒绝」", ""
            return name, company, f"已拒绝（命中「{hit}」）", live_job[:30]
        if hit:
            return name, company, f"该拒绝：命中岗位类型「{hit}」", live_job[:30]
        if not send:
            titles = json.loads(handler.page.run_js(PROBE_JS, as_expr=True) or "[]")
            has = any("是否同意" in t for t in titles)
            return name, company, ("有可同意的卡片" if has else "页面上没有待同意的卡片"), "、".join(titles[:3])
        if not handler.accept_contact_exchange():
            # 说不清为什么点不到就等于白跑一趟，把卡片和按钮的实际状态打出来
            try:
                why = json.loads(handler.page.run_js(WHY_JS, as_expr=True) or "[]")
            except Exception:
                why = []
            return name, company, "跳过：没点到「同意」", " ｜ ".join(why[:3]) or "没有是否同意卡片"
        # 点完 BOSS 不一定立刻把号放出来，而且放出来的形状不止一种
        # （"xxx 的微信号"卡片、纯文字号码都见过），判据直接用台账那套提取。
        live, got = [], []
        for _ in range(6):
            time.sleep(3)
            live = handler.read_all_messages() or []
            got = [m for m in live
                   if any(extract_contacts(_message_text(m)).get(k)
                          for k in ("phones", "wechats"))]
            if got:
                break
        job = handler.get_job_name() or row.get("job_name", "")
        MessageStore(account_index=account).merge_messages(
            chat_name=name, new_messages=live, job_name=job, company=company)
        first = got[-1] if got else {}
        return name, company, "已同意", (
            str(extract_contacts(_message_text(first)).get("phones")
                or extract_contacts(_message_text(first)).get("wechats"))[:40]
            if got else "点了，这一屏还没看到号放出来")
    finally:
        try:
            instance._get_active().close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    todo, total = pending_rows(args.limit)
    print(f"待发 {len(todo)} / 共 {total} 行「发起交换请求」")
    if not args.check:
        import requests
        print("暂停回复轮:", requests.post(f"{PANEL}/api/pause_reply").json(), flush=True)
    results = []
    try:
        for row in todo:
            try:
                results.append(run_one(row, not args.check))
            except Exception as e:
                results.append((row["chat_name"], row["company"],
                                f"异常：{type(e).__name__}: {str(e)[:70]}", ""))
            time.sleep(1.5)
    finally:
        if not args.check:
            import requests
            print("恢复回复轮:", requests.post(f"{PANEL}/api/resume_reply").json(), flush=True)

    agreed = 0
    declined = 0
    for name, company, verdict, extra in results:
        if verdict == "已同意":
            agreed += 1
        if verdict.startswith("已拒绝"):
            declined += 1
        print(f"  {name}｜{company}: {verdict}" + (f" — {extra}" if extra else ""))
    print(f"\n同意 {agreed} / 拒绝 {declined} / 处理 {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
