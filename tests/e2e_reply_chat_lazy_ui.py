# -*- coding: utf-8 -*-
"""真机验证：回复记录这一页，正文改成"点开哪一路才取哪一路"之后真的还能用。

起因（2026-10-30 用户第三次提"面板很卡"）：/api/reply_records/grouped 线上现测
解压后 **15,279,539 字节**（gzip 后 1.24 MB），2779 路会话的气泡全在这一份里。
浏览器要先 JSON.parse 完再建 DOM，主线程一停就是几秒——这一处比之前修掉的
job_pool（4.1 MB）还大一倍，之前只测了 /api/status 与 /api/job_pool 所以漏了它。

改法：合并列表只留会话元数据（名字/公司/岗位/最后一条/未读/full_message_count），
正文走 /api/chats/<会话名>（前端 refreshBossChatDetail 早就是这一条路）。

这条套件钉四件事：
1. 列表接口一份正文都不带，传输量与"带正文"那份（同一批种子数据打 /api/chats）对比；
2. 三路会话在左侧都还在——尤其没回复过的那一路（records 是空的，靠
   full_message_count 才不会被"过滤空分组"抹掉）；
3. 点开哪一路，右侧气泡就真出现那一路的条数与正文（懒取到位，不是画个空壳）；
4. 来源筛选筛完剩下的会话数还对得上接口；
5. 存档条数与屏上行数差在哪：差的那几条只许是没勾上的引擎标记
   （线上 2779 路里就见到 38 存 / 37 画这一例，见 README）。

全程用临时面板与临时数据目录，不碰 5001 那台线上面板、不碰 BOSS、不点发送。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from tests.conftest import mk测试临时目录, 挑空闲端口  # noqa: E402
from boss_bot.platform_compat import 终止进程  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import e2e_greet_records_ui as ui  # noqa: E402
from e2e_greet_records_ui import check, close_browser, open_browser  # noqa: E402

results = ui.results

TEST_PORT = 挑空闲端口(int(os.environ.get("BOSS_LAZY_CHAT_PORT", "5063")), "正文懒取实测")
PROBE_PORT = int(os.environ.get("BOSS_LAZY_CHAT_PROBE_PORT", "9351"))
URL = "http://127.0.0.1:%d" % TEST_PORT

# 三路会话：甲有回复记录（4 条气泡）、乙只有 HR 消息没回复过（3 条）、丙 6 条
种子 = [
    {"文件": "陈女士_甲科技有限公司.json", "chat_name": "陈女士",
     "company": "甲科技有限公司", "job_name": "数据分析师", "account_index": 0,
     "updated_at": "2026-10-29 15:00:00",
     "气泡": [("hr", "你好，请问还在招数据分析师吗"),
              ("me", "在招的，方便发份简历看看"),
              ("hr", "我这边要坐班的，可以接受吗"),
              ("me", "可以，我就在本地")]},
    {"文件": "李女士_乙广告有限公司.json", "chat_name": "李女士",
     "company": "乙广告有限公司", "job_name": "数据标注", "account_index": 0,
     "updated_at": "2026-10-29 14:00:00",
     "气泡": [("hr", "简历我看过了"), ("hr", "明天下午有空来聊聊吗"),
              ("hr", "在吗")]},
    {"文件": "王先生_丙信息技术有限公司.json", "chat_name": "王先生",
     "company": "丙信息技术有限公司", "job_name": "AI产品经理", "account_index": 0,
     "updated_at": "2026-10-29 13:00:00",
     "气泡": [("hr", "岗位在长沙"), ("me", "接受"), ("hr", "薪资 8-12K"),
              ("me", "可以"), ("hr", "那安排个面试"), ("me", "好的")],
     # 引擎自记的那两条也进存档：BOSS 线上没有这两条气泡，默认视图只画
     # 「拒绝/风控」这类结果（默认可见的引擎来源），[简历已发送] 那种要勾上才出。
     # 线上实测就是这样（李先生@青岛云裁 存 38 条、默认画 37 条，差的就是这条
     # [简历已发送]），所以这里把"存档条数 ≠ 默认画行数"钉成一条看得见的判据
     "自记行": [
         {"kind": "action", "sender": "bot", "is_mine": True, "mid": None,
          "content": "[简历已发送]", "text": "[简历已发送]",
          "reply_source": "rule", "source": "bot",
          "timestamp": "2026-10-29 13:30:00", "time": ""},
         {"kind": "action", "sender": "bot", "is_mine": True, "mid": None,
          "content": "[已拒绝交换联系方式]", "text": "[已拒绝交换联系方式]",
          "reply_source": "reject_contact", "source": "bot",
          "timestamp": "2026-10-29 13:31:00", "time": ""},
     ]},
]
回复记录 = [
    {"chat_name": "陈女士", "job_name": "数据分析师",
     "received_message": "你好，请问还在招数据分析师吗",
     "reply_content": "在招的，方便发份简历看看", "reply_source": "ai",
     "timestamp": "2026-10-29 15:00:00", "account_index": 0},
    {"chat_name": "王先生", "job_name": "AI产品经理",
     "received_message": "岗位在长沙", "reply_content": "接受",
     "reply_source": "rule", "timestamp": "2026-10-29 13:00:00", "account_index": 0},
]

LAUNCHER = """
import sys, pathlib
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(flask)r)
tmp = pathlib.Path(%(tmp)r)
import boss_bot.unified_config as UC
UC.BOT_CONFIG_FILE = tmp / "bot_config.json"
UC.OVERRIDES_FILE = tmp / "config_overrides.json"
UC.USER_PROFILE_FILE = tmp / "user_profile.json"
import boss_bot.config as CFG
CFG.BASE_DIR = tmp / "runtime_base"
import boss_bot.reply_record as RR
RR._reply_store = RR.ReplyRecordStore(path=str(tmp / "reply_records.json"))
RR._greet_store = RR.GreetRecordStore(path=str(tmp / "greet_records.json"))
for 一 in %(记录)r:
    RR._reply_store.add(RR.ReplyRecord(**一))
import app as A
A._ensure_config()
kwargs = {"host": "127.0.0.1", "port": %(port)d, "debug": False, "use_reloader": False}
if (getattr(A, "_socketio_kwargs", {}) or {}).get("async_mode") == "threading":
    kwargs["allow_unsafe_werkzeug"] = True
A.socketio.run(A.app, **kwargs)
"""


def 播种子(临时):
    消息目录 = 临时 / "runtime_base" / "messages"
    消息目录.mkdir(parents=True, exist_ok=True)
    for 路 in 种子:
        正文 = [{"kind": "bubble", "is_mine": 型 == "me", "sender": 型,
                 "text": 句, "content": 句, "mid": 1000 + i,
                 "time": "1%d:0%d" % (i % 6, i), "timestamp": "%s 1%d:0%d:00"
                 % (路["updated_at"][:10], i % 6, i)}
                for i, (型, 句) in enumerate(路["气泡"])]
        正文 += 路.get("自记行", [])
        (消息目录 / 路["文件"]).write_text(json.dumps({
            "chat_name": 路["chat_name"], "company": 路["company"],
            "job_name": 路["job_name"], "account_index": 路["account_index"],
            "updated_at": 路["updated_at"], "unread_on_boss": 0,
            "messages": 正文}, ensure_ascii=False), encoding="utf-8")


def start_server(临时):
    script = LAUNCHER % {"root": str(PROJECT_ROOT),
                         "flask": str(PROJECT_ROOT / "flask-version"),
                         "tmp": str(临时), "port": TEST_PORT,
                         "记录": 回复记录}
    env = dict(os.environ, BOSS_BOT_CONFIG=str(临时 / "bot_config.json"), PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    import requests
    for _ in range(60):
        if proc.poll() is not None:
            raise AssertionError("实测用的 Flask 实例自己退了")
        try:
            if requests.get(URL + "/api/config", timeout=1).ok:
                return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise AssertionError("实测用的 Flask 实例 30 秒内没起来")


def 等列表载入(page, 秒=12):
    page.run_js("""
        var b = document.querySelector('.record-tab[onclick*="reply"]');
        if (b) b.click();
        return 1;
    """)
    for _ in range(int(秒 / 0.4)):
        time.sleep(0.4)
        行 = int(page.run_js(
            "return document.querySelectorAll('#replyTabContent .boss-chat-item').length;") or 0)
        if 行 > 0:
            return 行
    return 0


def 点一行(page, 名字):
    """按会话名点左侧那一行（真人路径），返回点没点到。

    名字格里的文本不一定等于裸会话名（可能挂着账号标记），所以按"包含"匹配；
    认不出时把屏上真有的名字带回来，红了也知道去查哪一行。
    """
    raw = page.run_js("""
        var 行 = document.querySelectorAll('#replyTabContent .boss-chat-item');
        var 名 = [];
        for (var i = 0; i < 行.length; i++) {
          var n = 行[i].querySelector('.boss-chat-item-name');
          var t = ((n && n.textContent) || '').trim();
          名.push(t);
          if (t.indexOf(%s) >= 0) { 行[i].click(); return JSON.stringify([true, 名]); }
        }
        return JSON.stringify([false, 名]);
    """ % json.dumps(名字, ensure_ascii=False))
    if raw is None:
        raise AssertionError("run_js 没回值：点行这段没跑成，不许当成「没找到那一行」")
    中, 名 = json.loads(raw)
    return 中, 名


def 右侧正文(page):
    raw = page.run_js("""
        var 出 = [];
        var 块 = document.querySelectorAll('#bossChatMessages .boss-msg-row');
        for (var i = 0; i < 块.length; i++) {
          var t = 块[i].querySelector('.boss-msg-bubble, .boss-msg-text');
          出.push(((t && t.textContent) || '').trim());
        }
        return JSON.stringify(出);
    """)
    if raw is None:
        raise AssertionError("run_js 没回值：这段取气泡的 JS 没跑成，不许当成「右侧是空的」")
    return json.loads(raw)


def 右侧行数(page):
    """屏上右侧画了几行（含引擎标记那种小字行）。"""
    出 = page.run_js(
        "return document.querySelectorAll('#bossChatMessages .boss-msg-row').length;")
    if 出 is None:
        raise AssertionError("run_js 没回值：不许把「没读到」印成「屏上 0 行」")
    return int(出)


def 引擎标记行(page):
    raw = page.run_js("""
        return JSON.stringify([].slice.call(document.querySelectorAll(
            '#bossChatMessages .boss-msg-row.engine-mark .boss-msg-mark'))
            .map(function (d) { return (d.textContent || '').trim(); }));
    """)
    if raw is None:
        raise AssertionError("run_js 没回值：这段取引擎标记的 JS 没跑成")
    return json.loads(raw)


def main():
    临时 = mk测试临时目录("boss_lazy_chat")
    (临时 / "bot_config.json").write_bytes(
        (PROJECT_ROOT / "bot_config.json").read_bytes())
    播种子(临时)
    server = None
    browser = proc = None
    import requests
    try:
        server = start_server(临时)
        列表 = requests.get(URL + "/api/reply_records/grouped", timeout=30)
        组 = 列表.json().get("groups") or []
        带正文的 = requests.get(URL + "/api/chats?limit=0", timeout=30)
        check("合并列表里一份正文都没有（15.3 MB 那一处撤掉了）",
              all("messages" not in g for g in 组),
              "漏了正文的分组：%s" % [g.get("chat_name") for g in 组
                                     if "messages" in g][:3])
        check("每路还带着「这一路存了几条」，没回复过的那一路靠它才不会被筛掉",
              sorted(int(g.get("full_message_count") or 0) for g in 组) == [3, 4, 8],
              str([(g.get("chat_name"), g.get("full_message_count")) for g in 组]))
        列表字节 = len(列表.content)
        正文字节 = len(带正文的.content)
        # 这份套件只有三路会话，倍数说明不了什么（真数据上那一处是 15.3 MB，见
        # README 里线上重启前后的对照）。这里只钉方向：列表这份必须比"带全部正文"薄
        check("同一批种子数据：列表这份比带全部正文那份薄（%s B < %s B）"
              % ("{:,}".format(列表字节), "{:,}".format(正文字节)),
              列表字节 < 正文字节,
              "列表 %s B / 正文 %s B / 比值 %s" % (
                  列表字节, 正文字节, round(正文字节 / max(列表字节, 1), 2)))

        ui.DEBUG_PORT = PROBE_PORT
        browser, proc = open_browser()
        page = browser
        page.get(URL)
        time.sleep(1.2)
        行 = 等列表载入(page)
        check("左侧三路会话都在（含没回复过的那一路，不是只剩回复过的两路）", 行 == 3,
              "渲染 %d 行" % 行)
        载入时带正文的 = page.run_js("""
            return allReplyGroups.filter(function (g) {
                return g.messages && g.messages.length;
            }).length;
        """)
        if 载入时带正文的 is None:
            raise AssertionError("run_js 没回值：不许把「没读到」印成「页面带 0 组正文」")
        # 页面一载入会自动打开最上面那一路（那一路的正文是自己去取的），
        # 所以允许恰好 1 组带正文；多于 1 组就说明列表又把它塞回来了
        check("列表那一趟没把正文带进来（只有自动打开的那一路自己取过正文）",
              int(载入时带正文的) <= 1, "%s 组带正文" % 载入时带正文的)
        for 名, 期望条数, 期望首句 in (("陈女士", 4, "你好，请问还在招数据分析师吗"),
                                    ("李女士", 3, "简历我看过了"),
                                    ("王先生", 6, "岗位在长沙")):
            中, 屏上名字 = 点一行(page, 名)
            check("点得开「%s」那一行" % 名, 中, "屏上这几行：%s" % 屏上名字)
            气泡 = []
            for _ in range(14):
                time.sleep(0.4)
                气泡 = [x for x in 右侧正文(page) if x]
                # 等的是"这一路的正文"到位，不是"屏上有几条"——上一路的气泡还挂在
                # 屏上时条数可能刚好碰对，那是假绿
                if 气泡 and 期望首句 in 气泡[0]:
                    break
            check("「%s」右侧懒取到 %d 条气泡" % (名, 期望条数),
                  len(气泡) == 期望条数, "屏上 %d 条：%s" % (len(气泡), 气泡[:2]))
            if 气泡:
                check("「%s」第一条正文对得上（不是别路的话串过来）" % 名,
                      期望首句 in 气泡[0], "取到 %r" % 气泡[0][:40])

        # 存档条数 ≠ 默认画的行数，这一处是线上实测对出来的（李先生@青岛云裁
        # 存 38、默认画 37，差的正是 [简历已发送]）。种子把两种引擎标记都放上：
        # 「拒绝交换」那一族默认就画（10-07 他要看见结果），[简历已发送] 要勾上才画
        存档 = 右侧正文(page)
        默认行 = 引擎标记行(page)
        check("默认视图里「拒绝交换联系方式」这条引擎标记照画（不是把 action 一律藏掉）",
              any("已拒绝交换联系方式" in x for x in 默认行), str(默认行))
        check("默认视图不画 [简历已发送] 那条（默认视图就是 BOSS 线上的样子）",
              not any("简历已发送" in x for x in 默认行), str(默认行))
        check("默认视图屏上 7 行 = 存档 8 条 - 1 条藏起来的 [简历已发送]",
              右侧行数(page) == 7,
              "屏上 %d 行，标记行 %s，气泡 %s" % (右侧行数(page), 默认行, 存档))
        page.run_js("""
            var 框 = document.querySelector('.boss-chat-marks input[type=checkbox]');
            if (框) 框.click();
            return 框 ? 1 : 0;
        """)
        time.sleep(0.6)
        勾上行 = 引擎标记行(page)
        check("勾上「显示引擎标记」后 8 条全画出来（存档几条 = 屏上几行）",
              右侧行数(page) == 8 and any("简历已发送" in x for x in 勾上行),
              "屏上 %d 行，标记行 %s" % (右侧行数(page), 勾上行))
        page.run_js("""
            var 框 = document.querySelector('.boss-chat-marks input[type=checkbox]');
            if (框) 框.click();
            return 1;
        """)
        time.sleep(0.5)
        check("再取消勾选回到 7 行（开关是来回生效的，不是只认第一次）",
              右侧行数(page) == 7, "屏上 %d 行" % 右侧行数(page))

        # 来源筛选这一段是我改过的（分组留不留的判据从 messages 换成了
        # records/full_message_count），所以筛完剩几行要现算对得上
        for 来源, 期望行 in (("ai", 1), ("rule", 1), ("", 3)):
            page.run_js("""
                var s = document.getElementById('replyFilterSource');
                s.value = %s;
                s.dispatchEvent(new Event('change'));
                return 1;
            """ % json.dumps(来源, ensure_ascii=False))
            time.sleep(1.6)
            剩 = int(page.run_js(
                "return document.querySelectorAll('#replyTabContent .boss-chat-item').length;")
                or 0)
            check("来源筛「%s」剩 %d 路会话" % (来源 or "全部", 期望行), 剩 == 期望行,
                  "屏上 %d 行" % 剩)
        print("\n=== 临时面板数据留在 %s ===" % 临时)
    finally:
        if browser is not None:
            close_browser(proc)
        if server is not None:
            终止进程(server.pid)

    败 = [n for n, 绿, _ in results if not 绿]
    print("\n共 %d 项，失败 %d 项%s" % (len(results), len(败),
          "" if not 败 else "：" + ", ".join(败)), flush=True)
    return 1 if 败 else 0


if __name__ == "__main__":
    sys.exit(main())
