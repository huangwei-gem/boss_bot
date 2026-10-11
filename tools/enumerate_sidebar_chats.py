# -*- coding: utf-8 -*-
"""只读枚举两个号侧栏里的全部会话（姓名 + 公司 + 是否置顶 + 红点数）。

为什么要这个：盘上查无「唐浣月」这个人（messages/ 2265 份存档、打招呼与回复记录、
日志全扫过，一个字都没有），可线上列表里像是出现过——那只有把侧栏整份读一遍才能定。
侧栏是虚拟列表（实测 171 行只渲染 20 行），所以必须边滚边收，用 get_all_chats 那套。

全程只开页面、只滚列表、只读 DOM：不点任何会话（点开会把 HR 消息标成已读）、
不点发送、不打招呼。用完自己关浏览器，不动线上面板正在用的端口。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage
from boss_bot.page_handler import BossChatHandler
from boss_bot.platform_compat import 破解版路径

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = 破解版路径(BASE)
端口 = {0: 9425, 1: 9426}


def 枚举(账号):
    assert os.path.exists(CLOAK), "必须用项目自带破解版浏览器"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(端口[账号])
    co.set_argument("--user-data-dir=%s" % os.path.join(BASE, "browser_data", "account_%d" % 账号))
    co.set_argument("--disable-blink-features=AutomationControlled")
    page = ChromiumPage(co)
    try:
        # get_all_chats 只用到 self.page，不跑 __init__（它自己去开浏览器、走端口守卫）。
        # 借这个类的滚动+读行逻辑，是为了和线上采集用同一份判据，别在这儿另写一套 CSS。
        handler = BossChatHandler.__new__(BossChatHandler)
        handler.page = page
        page.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(6)
        chats = handler.get_all_chats(max_rounds=60)
        return chats, sorted(handler.pinned_chats or [])
    finally:
        try:
            page.close()
        except Exception:
            pass


def main(要查的=None):
    命中 = []
    for 账号 in (0, 1):
        try:
            chats, 置顶 = 枚举(账号)
        except Exception as e:
            print("账号%d 读侧栏没成功: %s" % (账号, e))
            continue
        名 = [(c.get("name") or "").strip() for c in chats]
        print("账号%d：侧栏收到 %d 个会话，其中置顶 %d 个" % (账号, len(chats), len(置顶)))
        落盘 = os.path.join(BASE, "tools", "sidebar_chats_account_%d.json" % 账号)
        with open(落盘, "w", encoding="utf-8") as f:
            json.dump(chats, f, ensure_ascii=False, indent=1)
        print("  清单已写 %s" % 落盘)
        if 置顶:
            print("  置顶：%s" % "、".join("%s|%s" % k for k in 置顶))
        if 要查的:
            中 = [c for c in chats if 要查的 in (c.get("name") or "") or 要查的 in (c.get("company") or "")]
            print("  含「%s」的行：%d" % (要查的, len(中)))
            for c in 中:
                print("    %s" % json.dumps(c, ensure_ascii=False)[:200])
                命中.append((账号, c))
    if 要查的 and not 命中:
        print("\n两个号的侧栏里都没有「%s」" % 要查的)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
