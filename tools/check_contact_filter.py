# -*- coding: utf-8 -*-
"""真机验证：日志里"滚到底也找不到"的那些会话，靠联系人搜索能不能点开。

只用临时端口 + 临时 profile + Cookie 文件（跑前跑后 sha 必须一致），
不碰线上 9222/9223 那两个正在跑的浏览器；全程只点会话行，
不点发送、不点打招呼、不点发简历。

用法：
    python tools/check_contact_filter.py                # 用今天日志里的失败样本
    python tools/check_contact_filter.py --name 陈女士 --company 爱森电商
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402

CHAT_URL = "https://www.zhipin.com/web/geek/chat"
# 今天日志里反复出现的两个"侧栏滚到底也没有会话"
SAMPLES = [("陈女士", "成都市小智时代科技"), ("肖蒙", "新东方"),
           ("陈女士", "爱森电商")]

FIRST_ROW_NAME_JS = r"""
var f = document.querySelector('.friend-content .name-text');
return f ? f.textContent.trim() : '';
"""
ROW_COUNT_JS = "return document.querySelectorAll('.friend-content').length;"

DUMP_SEARCH_JS = r"""
return Array.prototype.slice.call(document.querySelectorAll('[class*=search]')).map(function(e){
    var r = e.getBoundingClientRect();
    return {tag: e.tagName, cls: String(e.className).slice(0, 70),
            w: Math.round(r.width), h: Math.round(r.height),
            text: (e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60)};
});
"""
PRESS_ENTER_JS = r"""
var i = document.querySelector('input.boss-search-input');
if (!i) return 'no-input';
['keydown', 'keypress', 'keyup'].forEach(function(t){
    i.dispatchEvent(new KeyboardEvent(t, {key: 'Enter', code: 'Enter',
                                          keyCode: 13, which: 13, bubbles: true}));
});
return 'sent';
"""


def cookie_file(account_index: int) -> Path:
    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    name = "zhipin_cookies.json"
    if account_index < len(accounts):
        name = accounts[account_index].cookie_file or name
    return Path(str(resolve_path(name)))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--account", type=int, default=0)
    p.add_argument("--port", type=int, default=9431)
    p.add_argument("--name", default="")
    p.add_argument("--company", default="")
    p.add_argument("--diagnose", action="store_true",
                   help="只报页面事实：有哪些输入框、筛一个屏幕上就有的名字会怎样")
    a = p.parse_args()

    targets = SAMPLES if not a.name else [(a.name, a.company)]
    ck = cookie_file(a.account)
    sha_before = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]
    profile = Path(tempfile.mkdtemp(prefix="boss_filter_probe_"))

    instance = None
    failures = 0
    try:
        instance = launch_browser(headless=True, port=a.port,
                                  user_data_dir=str(profile))
        if not instance.load_cookies(str(ck)):
            print("Cookie 加载失败，测不了")
            return 1
        instance.get(CHAT_URL)
        time.sleep(7)

        handler = BossChatHandler.__new__(BossChatHandler)
        handler.page = instance

        rows_total = instance.run_js(ROW_COUNT_JS)
        print(f"聊天页 URL={instance.url} 侧栏当前渲染 {rows_total} 行")

        if a.diagnose:
            present = instance.run_js(FIRST_ROW_NAME_JS)
            print("屏幕上第一个名字:", present)
            print("[改之前] 搜索区 DOM:", json.dumps(
                instance.run_js(DUMP_SEARCH_JS), ensure_ascii=False)[:900])
            wrote = handler._set_contact_filter(present)
            time.sleep(2.5)
            print(f"写入={wrote} 行数 {instance.run_js(ROW_COUNT_JS)}")
            print("[打字之后] 搜索区 DOM:", json.dumps(
                instance.run_js(DUMP_SEARCH_JS), ensure_ascii=False)[:1400])
            print("按回车:", instance.run_js(PRESS_ENTER_JS))
            time.sleep(2.5)
            print("结果行结构:", json.dumps(instance.run_js(
                "var li=document.querySelector('li.search-list');"
                "if(!li) return 'no-li';"
                "return Array.prototype.slice.call(li.querySelectorAll('*')).map("
                "function(e){return (e.className||e.tagName)+' = '+(e.textContent||'')"
                ".replace(/\\s+/g,' ').trim().slice(0,40)});"), ensure_ascii=False)[:1200])
            handler._clear_contact_filter()
            return 0

        # 对照组：屏幕上就有的一个人。targets 全都"搜不到"时，没有这一条就
        # 分不清是搜索机制坏了还是那些人真不在 30 天窗口里。
        control = instance.run_js(
            "var e=document.querySelector('.friend-content .name-text');"
            "return e?e.textContent.trim():'';")
        if control:
            verdict = handler._open_chat_by_search(control, "")
            after = instance.run_js(ROW_COUNT_JS)
            print(f"  [对照·{control}] 搜索返回 {verdict}，"
                  f"浮层出来时侧栏仍渲染 {after} 行（实测它不筛侧栏）")
            handler._clear_contact_filter()
            if verdict != "ok":
                print("    ✗ 连屏幕上的人都搜不到，机制有问题")
                failures += 1

        for name, company in targets:
            had = handler._sidebar_has_row(name, company)
            scrolled = False if had else handler._scroll_to_chat_row(name, company)
            if had or scrolled:
                print(f"  [{name}|{company}] 本来就在屏幕上/滚到了"
                      f"（has_row={had}, scroll={scrolled}）")
                continue

            verdict = handler._open_chat_by_search(name, company)
            if verdict != "ok":
                # 搜不到不算失败：会话可能真超出"30 天内联系人"这个窗口
                print(f"  [{name}|{company}] 搜索返回 {verdict}（可能超出 30 天窗口）")
                handler._clear_contact_filter()
                continue

            entered = handler.enter_chat({"name": name, "company": company, "index": 0})
            left = instance.run_js(
                "var i=document.querySelector('input.boss-search-input');"
                "return i?i.value:'no-input';")
            open_chat = instance.run_js(
                "var e=document.querySelector('.chat-message-list .name-text')||"
                "document.querySelector('.chat-input');return e?'有聊天面板':'没聊天面板';")
            print(f"  [{name}|{company}] 搜索点开 -> enter_chat={entered} "
                  f"{open_chat} 搜索框残留={left!r}")
            if left not in ("", "no-input"):
                print("    ✗ 用完没清空搜索框")
                failures += 1
            if not entered:
                failures += 1
    finally:
        if instance is not None:
            try:
                instance.quit()
            except Exception:
                pass
        shutil.rmtree(str(profile), ignore_errors=True)
        sha_after = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]

    print(("Cookie 文件未被改动 ✓" if sha_before == sha_after
           else f"警告：Cookie 文件被改动 ✗ {sha_before} → {sha_after}"))
    return 1 if (failures or sha_before != sha_after) else 0


if __name__ == "__main__":
    raise SystemExit(main())
