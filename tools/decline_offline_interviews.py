# -*- coding: utf-8 -*-
"""把已经约成线下、又不是我们要的面试拒掉。

用户 2026-10-06 的原话："我只要线上的兼职不要线下的，我约的面试都是线下的你给我都拒绝了吧"
——这是放行的真实发送，不是实测。

安全边界：
- 只发下面 TARGETS 里点名的会话，逐个先在页面上核对最后几条 HR 原文真的在约面试，
  对不上就不发（宁可少发也不能发错人）。
- 按端口连回该账号本来就在用的浏览器（9222+index），另开一个标签页操作，
  不新起浏览器、不碰 profile、不动 Cookie。
- 跑之前先暂停回复轮（同一浏览器里两个驱动者会抢会话选择），发完恢复。

跑法：python tools/decline_offline_interviews.py --check   # 只看会不会发
      python tools/decline_offline_interviews.py            # 真发
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import BrowserInstance  # noqa: E402
from boss_bot.message_store import MessageStore  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
PANEL = "http://127.0.0.1:5000"

# 实测 9 个"面试"会话里，只有这三单真的约到了线下、而且我们口头应过：
#   姬广凯｜湖南九片云：HR"我安排明天下午三点面试"+给了写字楼地址，我们回"我会准时到达"
#   陈女士｜爱森电商：HR"可直接来公司参观详聊"，我们回"工作日下午都可以安排面试"
#   章先生｜长沙坤舆互动文化传媒：HR"合适的话可以明天下午面试"，我们同样应了
# 杨贵兵那单存档里已经写着"拒绝了面试邀请"，不再重复发。
TARGETS = [
    {"account": 1, "name": "姬广凯", "company": "湖南九片云",
     "job": "出差助理4-9K长沙查看职位"},
    {"account": 1, "name": "陈女士", "company": "爱森电商",
     "job": "急聘！！！电商客服+月休8天3-6K长沙查看职位"},
    {"account": 0, "name": "章先生", "company": "长沙坤舆互动文化传媒",
     "job": "无门槛 +包吃包住+音乐小白6-8K长沙查看职位"},
]

DECLINE = ("不好意思，我这边只找线上远程就能完成的兼职，线下面试就不占用您的时间了，"
           "这个岗位我先放弃，感谢您的邀请。")

# HR 原文里提到这些才认为"这一单真的在约面试"。是不是线下由 TARGETS 里
# 那三条存档证据定（地址/来公司/包住坐班），页面上只复核"有没有邀约"这一件事。
# "地址/来公司"是实测补进来的：姬广凯那句只写了写字楼门牌号和前台签到，
# 陈女士那句写的是"可直接来公司参观详聊"，光盯"面试"两个字会把这两单漏掉。
INVITE_MARKS = ("面试", "邀约", "约个时间", "过来", "到岗", "地址", "来公司", "线下")


def pause_reply(client):
    return client.post(f"{PANEL}/api/pause_reply")


def resume_reply(client):
    return client.post(f"{PANEL}/api/resume_reply")


def attach(account_index):
    """连回该账号正在用的浏览器，另开一个聊天标签页，返回那个标签页的实例。"""
    from DrissionPage import ChromiumOptions, ChromiumPage
    co = ChromiumOptions()
    co.set_local_port(9222 + account_index)
    page = ChromiumPage(co)
    tab = page.new_tab(CHAT_URL)
    time.sleep(4)
    return BrowserInstance(chrome_page=tab)


def search_contact(tab, name):
    """侧栏是虚拟列表且只渲染 40 行，几天前的会话滚到底也找不到（实测姬广凯就是这么丢的）。
    页面自己有"搜索30天内的联系人"，用它能把人捞出来。"""
    try:
        box = tab.ele("css:.boss-search-input", timeout=3)
        if not box:
            return False
        box.clear()
        box.input(name)
        time.sleep(2.0)
        return True
    except Exception:
        return False


def hr_texts(messages):
    out = []
    for m in messages or []:
        if m.get("is_mine") or m.get("is_system"):
            continue
        text = str(m.get("text") or m.get("card_text") or "").strip()
        if text:
            out.append(text)
    return out


def looks_like_offline_invite(messages):
    joined = " ".join(hr_texts(messages)[-5:])
    return any(k in joined for k in INVITE_MARKS), joined[:120]


def run_one(account_index, target, send):
    instance = attach(account_index)
    handler = BossChatHandler(browser_instance=instance)
    try:
        search_contact(instance, target["name"])
        if not handler.enter_chat({"name": target["name"], "company": target["company"],
                                   "index": -1}):
            # 绝不退到"只按姓名点"：实测侧栏里同时有 陈女士|湖南袋鼠教盟 和
            # 陈女士|爱森电商，只按姓名会对着完全不相干的公司发拒绝话术。
            return target["name"], "跳过：侧栏里找不到这个人的这家会话（需人工在 BOSS 上拒）", ""
        live = handler.read_all_messages() or []
        ok, evidence = looks_like_offline_invite(live)
        if not ok:
            return target["name"], "跳过：页面上最后几条没提到面试", evidence
        if not send:
            return target["name"], "核对通过（--check 未发送）", evidence
        if not handler.send_text(DECLINE):
            return target["name"], "失败：文字没发出去", evidence
        MessageStore(account_index=account_index).append_bot_message(
            target["name"], DECLINE, target["job"],
            reply_source="manual", action="text", company=target["company"])
        return target["name"], "已拒绝", evidence
    finally:
        try:
            # 这版 DrissionPage 的 ChromiumPage 没有关标签页的方法（只有 tab 自己有），
            # 早前写成 page 上的 close_tab 只会抛 AttributeError 再被下面那句吃掉
            # ——实测这样漏了几十个标签页。
            instance._get_active().close()
        except Exception:
            pass



def committed_targets():
    """从存档里捞出"我们自己回话应了线下到场、之后又没拒绝过"的会话。

    2026-10-07 中午的窟窿就在这儿：判据只认平台那张"邀请您现场面试"卡片，
    HR 换成"明天下午几点可以过来""地址：××大厦413"这种口语约时间时，
    策略层不触发，AI 就正常聊天聊成了"好的，我准时到"——12:02、12:35 两条
    连"明天10点准时到新天地1310面试"都说出口了。
    所以名单不能靠判据，直接看我们发出去的话。
    """
    import re
    from boss_bot.contact_ledger import _message_text
    from boss_bot.reply_queue import order_key

    应约 = re.compile(r"准时到|准时到达|准时过去|我确定来|按约定准时|可以过去|我按时到")
    已拒 = re.compile(r"不占用您的时间|先放弃|拒绝了面试邀请|只找线上")
    out = []
    for c in MessageStore().get_all_chats_detail() or []:
        msgs = sorted([m for m in (c.get("messages") or []) if _message_text(m)],
                      key=order_key)
        mine = [i for i, m in enumerate(msgs)
                if m.get("is_mine") and 应约.search(_message_text(m))]
        if not mine:
            continue
        # 这一会话里我们任何时候说过拒绝话术就算结了——不按"应约之后"判断：
        # 工具补写的那条拒绝消息没有 data-mid，order_key 排 0，会被_sort_到最前面，
        # 按位置判断就会把已经拒过的 王女士/王博/迟女士 又列一遍（实测 14:35 复现）。
        if any(m.get("is_mine") and 已拒.search(_message_text(m)) for m in msgs):
            continue
        company = (c.get("company") or "").strip()
        if not company:
            continue      # 公司名空着没法双重核对，绝不按姓名点
        out.append({"account": int(c.get("account_index") or 0),
                    "name": c.get("chat_name"), "company": company,
                    "job": c.get("job_name") or ""})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只核对不发送")
    ap.add_argument("--only", default="", help="只处理这个名字的会话")
    ap.add_argument("--from-archive", action="store_true",
                    help="名单取自存档里我们应过线下到场的会话，而不是写死的 TARGETS")
    args = ap.parse_args()
    send = not args.check
    targets = committed_targets() if args.from_archive else TARGETS
    print(f"待处理 {len(targets)} 单：",
          "、".join(f"{t['name']}|{t['company'][:10]}" for t in targets), flush=True)

    import requests
    if send:
        r = pause_reply(requests)
        print("暂停回复轮:", r.json(), flush=True)
    results = []
    try:
        for t in targets:
            if args.only and args.only != t["name"]:
                continue
            try:
                results.append(run_one(t["account"], t, send))
            except Exception as e:
                results.append((t["name"], f"异常：{type(e).__name__}: {str(e)[:90]}", ""))
            time.sleep(2)
    finally:
        if send:
            print("恢复回复轮:", resume_reply(requests).json(), flush=True)

    print()
    for name, verdict, evidence in results:
        print(f"  {name}: {verdict}")
        if evidence:
            print(f"      依据: {evidence}")
    return 0 if all("已拒绝" in v or "--check" in v or "核对通过" in v
                    for _, v, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
