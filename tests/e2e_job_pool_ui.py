"""「岗位候选池」的真机点击测试（task #198）。

只起临时面板（默认 5061），绝不碰线上面板，也绝不碰 BOSS：
线上面板跑的是旧代码，在它上面测出来的"通过"是假的。

红线：
- 池文件写在临时目录里，不动 data/job_pool*.json（那是他攒的候选清单）；
- 全程只点本地面板，不点任何发送/打招呼/发简历，不打开 zhipin；
- 跑前跑后逐个算 Cookie sha，一个字节都不许变。

跑法：python tests/e2e_job_pool_ui.py
      BOSS_PROBE_HEADLESS=1 python tests/e2e_job_pool_ui.py   # 不弹窗口再来一遍
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from tests.conftest import mk测试临时目录, 挑空闲端口  # noqa: E402
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from boss_bot.unified_config import 面板端口 as _面板端口  # 线上面板口只有一个出处

import e2e_account_scope_ui as E          # noqa: E402
import e2e_greet_records_ui as UI         # noqa: E402
from boss_bot.job_pool import 岗位池       # noqa: E402

# 端口不能踩 Chrome 的 restricted ports：5060/5061 是 SIP，浏览器直接 ERR_UNSAFE_PORT，
# 页面停在 chrome-error:// 上，后面每个 DOM 取值都拿到 null，看着像"界面没做出来"。
PORT = 挑空闲端口(int(os.environ.get("BOSS_POOL_UI_PORT", "5073")), "岗位候选池实测")
URL = "http://127.0.0.1:%d" % PORT
SHOTS = PROJECT_ROOT / "tests" / "screenshots"
results = []

# 临时实例里把池的落点顶到临时目录：app 的接口每次现建 岗位池，而 岗位池 只认
# resolve_path 这一处，换掉它等于换掉整张池的地址。不换就是往真实 data/ 写假岗位。
# 筛选要求文件（data/ai_screen_doc.json）同理：实测保存的要求一个字都不许落进真的那份。
# AI 判分换成固定结论的假引擎——线程、写回、接口、界面照走全程，但一次真接口都不打。
LAUNCHER = """
import sys, pathlib
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(flask)r)
import boss_bot.unified_config as UC
import boss_bot.job_pool as JP
import boss_bot.ai_screen_doc as ASD
tmp = pathlib.Path(%(tmp)r)
UC.BOT_CONFIG_FILE = tmp / "bot_config.json"
UC.OVERRIDES_FILE = tmp / "config_overrides.json"
UC.USER_PROFILE_FILE = tmp / "user_profile.json"
JP.resolve_path = lambda p: tmp / "data"
ASD.resolve_path = lambda p: tmp / "data"
import app as A
A._ensure_config()

class _假引擎:
    # 只判「标题里有没有标注」：够造出合/不合两种结论，又不碰任何真接口
    def _analyze_job_with_ai(self, job):
        if "标注" in str(job.get("job_name") or ""):
            return {"score": 40, "is_match": False, "reason": "实测假判：只要数据分析",
                    "model": "假接口", "veto_hit": ""}, 0.01
        return {"score": 86, "is_match": True, "reason": "实测假判：方向对口",
                "model": "假接口", "veto_hit": ""}, 0.01

A._筛选引擎 = lambda 号: _假引擎()
kwargs = {"host": "127.0.0.1", "port": %(port)d, "debug": False, "use_reloader": False}
if (getattr(A, "_socketio_kwargs", {}) or {}).get("async_mode") == "threading":
    kwargs["allow_unsafe_werkzeug"] = True
A.socketio.run(A.app, **kwargs)
"""


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           (" — " + str(detail)) if detail else ""), flush=True)


def js(page, code):
    return page.run_js(code)


def tab_button(page, label):
    return page.eles("xpath://button[contains(@class,'record-tab')][contains(.,'%s')]" % label)


def 读接口(qs=""):
    # 接口默认只回一页（200 条）：这个套件比的是"筛出来的到底是哪几条"，要的是全量。
    # 自己带了 limit 的那条（专门测分页）照原样发。
    if "limit=" not in qs:
        qs += ("&" if "?" in qs else "?") + "limit=0"
    return requests.get(URL + "/api/job_pool" + qs, timeout=60).json()


def 写接口(path, 体):
    r = requests.post(URL + path, json=体, timeout=60)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"message": "接口回的不是 JSON"}


def 配置():
    d = requests.get(URL + "/api/config", timeout=30).json()
    return d.get("config") or d


def 盘上池(目录, 号):
    return 岗位池(account_index=号, 目录=str(目录)).列表()


def seed_pool(目录):
    """造五条覆盖各列的行：判过的高分、判过的不匹配、没判过的、被本地否过的、
    投不出去的+置顶。空表测不出渲染，全好数据也测不出坏数据。"""
    池0 = 岗位池(account_index=0, 目录=str(目录))
    池0.收([
        {"url": "https://www.zhipin.com/job_detail/pool001.html", "job_name": "数据分析师",
         "company": "某某科技", "salary": "8-12K", "location": "长沙",
         "experience": "经验不限", "education": "本科", "query": "数据分析"},
        {"url": "https://www.zhipin.com/job_detail/pool002.html", "job_name": "AI 产品经理",
         "company": "某某智能", "salary": "10-15K", "location": "长沙",
         "experience": "1-3年", "education": "本科", "query": "AI产品"},
        {"url": "https://www.zhipin.com/job_detail/pool003.html", "job_name": "普工/操作工",
         "company": "某某制造", "salary": "5-7K", "location": "长沙·雨花区", "query": "普工"},
        {"url": "https://www.zhipin.com/job_detail/pool004.html", "job_name": "数据标注",
         "company": "某某数据", "salary": "6-8K", "location": "上海·浦东新区·张江", "query": "标注"},
        {"url": "https://www.zhipin.com/job_detail/pool005.html", "job_name": "BI 报表开发",
         "company": "某某信息", "salary": "9-13K", "location": "长沙", "query": "数据分析"},
    ])
    按名 = {r["job_name"]: r for r in 池0.列表()}
    池0.改(按名["数据分析师"]["id"],
           ai={"分": 88, "合": True, "理由": "SQL/Excel 都对口，经验不限",
               "接口": "agnes-2.5-flash", "判于": time.time()},
           gate={"方向命中": True, "依据": "卡片(未开JD)"})
    池0.改(按名["AI 产品经理"]["id"],
           ai={"分": 82, "合": True, "理由": "有 AIGC 项目经历，匹配度高",
               "接口": "agnes-2.5-flash", "判于": time.time()},
           gate={"方向命中": True, "依据": "卡片(未开JD)"})
    池0.改(按名["普工/操作工"]["id"],
           gate={"否决命中": "普工", "方向命中": False, "依据": "卡片(未开JD)"},
           ai={"分": 10, "合": False, "理由": "岗位类型不合，硬否决", "否决": "普工",
               "接口": "agnes-2.5-flash", "判于": time.time()})
    池0.改(按名["BI 报表开发"]["id"], gate={"方向命中": True},
           投递={"成": False, "原因": "已沟通过（BOSS 侧已有这条会话）", "时间": time.time()},
           status="rejected", note="上周聊过了")
    池0.改(按名["数据标注"]["id"], pinned=True, gate={"方向命中": True, "依据": "卡片(未开JD)"})
    # 数据标注那条故意不写 ai：界面上必须画成「未判」而不是 0 分
    池1 = 岗位池(account_index=1, 目录=str(目录))
    池1.收([{"url": "https://www.zhipin.com/job_detail/pool101.html",
             "job_name": "数据分析实习生", "company": "另一个号看到的",
             "salary": "4-6K", "location": "北京", "query": "数据分析"}])
    return 池0.列表(), 池1.列表()


def 起临时面板(tmp):
    script = LAUNCHER % {"root": str(PROJECT_ROOT),
                         "flask": str(PROJECT_ROOT / "flask-version"),
                         "tmp": str(tmp), "port": PORT}
    env = dict(os.environ, BOSS_BOT_CONFIG=str(tmp / "bot_config.json"))
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(40):
        if proc.poll() is not None:
            raise AssertionError("临时面板自己退了（端口 %d 被占？换个号再来）" % PORT)
        try:
            if requests.get(URL + "/api/config", timeout=1).ok:
                return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise AssertionError("临时面板 20 秒内没起来")


def 表里行数(page):
    return js(page, "return document.querySelectorAll('#poolTableBody tr').length;") or 0


def 设格(page, 编号, 值):
    """真填表头那一行里的某一格：改 value 之后必须补一个 change 事件——
    那些框挂的是 onchange="applyPoolFilter()"，只用 JS 塞 value 不发事件，
    测出来的是「没点过」，界面根本不知道他填了东西。"""
    return js(page, """
        var el = document.getElementById(%s);
        if (!el) return null;
        el.value = %s;
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return 1;""" % (json.dumps(编号), json.dumps(值, ensure_ascii=False))) == 1


def 按着shift点(page, selector):
    """真按 Shift 点一下（CDP modifiers=8）。

    用 JS 伪造 click 测不出「浏览器到底有没有把 shiftKey 带进来」——
    而连续勾靠的正是那一份 shiftKey。这里走真鼠标事件，选中的框会被浏览器
    自己翻转，跟人用手点一模一样。

    表在滚动容器里，屏外的行 getBoundingClientRect 拿到的坐标点下去会落到别的
    元素上（第一版就这么假失败了一次）：先滚到中间，再核实那个点真的是这个框。
    """
    pt = js(page, """
        var el = document.querySelector(%s);
        if (!el) return null;
        el.scrollIntoView({block: 'center'});
        var r = el.getBoundingClientRect();
        var x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
        var hit = document.elementFromPoint(x, y);
        return [x, y, hit === el || (!!hit && !!el.closest('label') && hit.closest('label') === el.closest('label'))];"""
        % repr(selector))
    assert pt, "找不到要 Shift 点的元素：%s" % selector
    x, y, 点得中 = pt
    assert 点得中, "这个坐标点下去落不到 %s 上（被别的元素盖住了）" % selector
    page.run_cdp('Input.dispatchMouseEvent', type='mouseMoved', x=x, y=y)
    for 类 in ('mousePressed', 'mouseReleased'):
        page.run_cdp('Input.dispatchMouseEvent', type=类, x=x, y=y,
                     button='left', clickCount=1, modifiers=8)



def 等到行数(page, 期望, 次=16):
    for _ in range(次):
        if 表里行数(page) == 期望:
            return True
        time.sleep(0.6)
    return 表里行数(page) == 期望


def main():
    assert PORT not in (_面板端口(), 5056, 5059), "实测禁止打线上面板或别的实测口"
    tmp = mk测试临时目录("boss_pool_ui")
    proc = None
    browser = None
    before = E.sha_of_cookies()
    真实要求 = PROJECT_ROOT / "data" / "ai_screen_doc.json"
    真实要求戳 = 真实要求.stat().st_mtime_ns if 真实要求.exists() else None
    try:
        E.seed(tmp)
        池目录 = tmp / "data"
        rows0, rows1 = seed_pool(池目录)
        proc = 起临时面板(tmp)
        check("临时面板起来了", True, URL)

        # ── 先把接口这侧钉住，后面界面要和它对数 ──
        d0 = 读接口("?account=0")
        check("账号0 的池有 5 条", d0.get("status") == "ok" and d0.get("total") == 5,
              str(d0.get("total")))
        d1 = 读接口("?account=1")
        check("账号1 只看得到自己那 1 条（池按号分开）", d1.get("total") == 1, str(d1.get("total")))
        全部 = requests.get(URL + "/api/job_pool?account=all", timeout=30)
        全 = 全部.json() if 全部.status_code == 200 else {}
        check("「全部账号」把两号的池并成一张表（面板开机就停在这个范围）",
              全部.status_code == 200 and 全.get("total") == 6
              and (全.get("per_account") or {}).get("0", {}).get("总数") == 5
              and (全.get("per_account") or {}).get("1", {}).get("总数") == 1,
              "%s 共 %s / 分号 %s" % (全部.status_code, 全.get("total"), 全.get("per_account")))
        check("合并视图的行带着自己是哪个号的",
              {r.get("account_index") for r in 全.get("rows", [])} == {0, 1},
              str({r.get("account_index") for r in 全.get("rows", [])}))
        没号 = 写接口("/api/job_pool/update", {"id": rows0[0]["id"], "note": "不该写进去"})
        check("不说是哪个号就不许改", 没号[0] == 400, str(没号[1].get("message", ""))[:44])

        page, browser = UI.open_browser()
        page.get(URL)
        time.sleep(3.5)

        # ── tab 接进去没有：三个老 tab 不能被弄坏 ──
        UI.click_until_alive(page, lambda p: tab_button(p, "联系与简历"))
        time.sleep(1.2)
        led = js(page, """
            return {on: document.getElementById('contactTabContent').classList.contains('active'),
                    clear: getComputedStyle(document.getElementById('btnClearRecord')).display};""")
        check("「联系与简历」照旧可点、清空照旧藏掉",
              led["on"] and led["clear"] == "none", str(led))

        tab = UI.click_until_alive(page, lambda p: tab_button(p, "岗位候选池"))
        check("点得到「岗位候选池」这个 tab", bool(tab))
        # 面板开机停在「全部账号」：这一页必须当场把两号的池并出来，不是一句话空表
        # （他点完启动看到空白，就是这一档从前回的 400）。先验合并视图，再切单号。
        合 = {}
        for _ in range(14):
            合 = js(page, """
                return {行: document.querySelectorAll('#poolTableBody tr').length,
                        标: document.querySelectorAll('#poolTableBody .pool-acc-tag').length,
                        角标: (document.getElementById('poolTabCount')||{}).textContent||'',
                        统计: (document.getElementById('poolStats')||{}).textContent||''};""") or {}
            if 合.get("行") == 6:
                break
            time.sleep(0.6)
        check("「全部账号」下表直接出来：6 行、每行标着号、角标是合计",
              合.get("行") == 6 and 合.get("标") == 6 and 合.get("角标") == "6",
              str(合)[:160])
        check("顶部统计说明这是两号合计", "两个号合计" in 合.get("统计", ""), 合.get("统计", "")[:80])
        check("数据范围切得到账号1", bool(UI.click_chip(page, 1)))
        等到行数(page, 5)
        shown = 表里行数(page)
        badge = js(page, "return document.getElementById('poolTabCount').textContent;")
        check("表里渲染的行数和接口对得上", shown == d0["total"], "界面 %s / 接口 %s" % (shown, d0["total"]))
        check("tab 角标是池内总数", str(badge) == str(d0["stats"]["总数"]), "角标 %s" % badge)
        栏 = js(page, """
            return {bar: getComputedStyle(document.getElementById('poolFilterBar')).display !== 'none',
                    greetOff: !document.getElementById('greetTabContent').classList.contains('active'),
                    clear: getComputedStyle(document.getElementById('btnClearRecord')).display};""")
        check("筛选栏出现、打招呼 tab 收起、清空在这页藏掉",
              栏["bar"] and 栏["greetOff"] and 栏["clear"] == "none", str(栏))
        列 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            return {行: tr ? tr.cells.length : 0,
                    头: document.querySelectorAll('#poolTable thead tr:first-child th').length,
                    筛: document.querySelectorAll('#poolTable thead tr.pool-filter-row th').length};""")
        check("列数与表头对齐（错了就是整行错位）",
              列["行"] == 列["头"] == 12, str(列))
        check("逐列筛选那一行与标题行一样宽（少一格，他填的薪资就是公司那列）",
              列["筛"] == 列["头"], str(列))

        首行 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            var t = function(i){ return tr.cells[i].textContent.trim(); };
            var sel = tr.cells[8].querySelector('select');
            return {job:t(1), company:t(2), city:t(3), pay:t(4), ai:t(5), reason:t(6), gate:t(7),
                    status: sel ? sel.value : '', statusText: sel && sel.selectedOptions.length
                        ? sel.selectedOptions[0].textContent.trim() : ''};""")
        check("首行是 AI 分最高那条，分数/接口/理由都画出来了",
              首行["job"].startswith("数据分析师") and "88" in 首行["ai"]
              and "SQL" in 首行["reason"] and "agnes" in 首行["ai"], str(首行)[:220])
        check("状态那格读出来是「待选」而不是整串下拉",
              首行["status"] == "new" and 首行["statusText"] == "待选", str(首行)[:120])
        未判 = js(page, """
            var trs = [...document.querySelectorAll('#poolTableBody tr')];
            var 目标 = trs.filter(function(tr){ return tr.cells[1].textContent.indexOf('数据标注') >= 0; })[0];
            return 目标 ? 目标.cells[5].textContent.trim() : '(找不到这一行)';""")
        check("没轮到精筛的那条画成「未判」而不是 0 分", "未判" in 未判, 未判[:60])
        否 = js(page, """
            var trs = [...document.querySelectorAll('#poolTableBody tr')];
            var 目标 = trs.filter(function(tr){ return tr.cells[1].textContent.indexOf('普工') >= 0; })[0];
            return 目标 ? 目标.cells[7].textContent.trim() : '(找不到这一行)';""")
        check("本地粗筛把否决词与方向都标在行上",
              "普工" in 否 and "方向不合" in 否, 否[:120])
        投不出 = js(page, """
            var trs = [...document.querySelectorAll('#poolTableBody tr')];
            var 目标 = trs.filter(function(tr){ return tr.cells[1].textContent.indexOf('BI') >= 0; })[0];
            return 目标 ? 目标.cells[1].textContent : '';""")
        check("投不出去的原因写在行上", "已沟通过" in 投不出, 投不出[:80])
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.get_screenshot(str(SHOTS / "job_pool_tab.png"))

        # ── AI 理由列：格子必须撑满整行，行悬停不许露出没涂到的空方块 ──
        # 他截图指的就是这块：display:-webkit-box 以前挂在 <td> 上，这一格就不再是
        # 表格单元，高度只剩文本那两行；行悬停时整列空出一条深色带（实测 (35,39,58)，
        # 同一行别处 (54,58,75)），看着像一个空框。
        理由格 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            var td = tr.querySelector('td.pool-reason');
            td.scrollIntoView({block: 'center'});
            var r = td.getBoundingClientRect(), rr = tr.getBoundingClientRect();
            var d = td.firstElementChild;
            return {格高: Math.round(r.height), 行高: Math.round(rr.height),
                    内层: d ? d.tagName : '',
                    截断: d ? getComputedStyle(d).webkitLineClamp : '',
                    折行: d ? getComputedStyle(d).whiteSpace : '',
                    文在层里: !!(d && d.textContent.trim().length > 0)};""")
        check("理由格撑满整行（以前只剩文本那两行高）",
              理由格["格高"] > 0 and 理由格["格高"] >= 理由格["行高"] - 1, str(理由格))
        check("两行截断挪到内层 div，并且允许换行",
             理由格["内层"] == "DIV" and 理由格["截断"] == "2"
              and 理由格["折行"] == "normal" and 理由格["文在层里"], str(理由格))

        # 真鼠标停在行首的留白上（真指针才触发 :hover），再按像素核对：这一列的上头
        # 与下头都该是行悬停色。格子缩回去时下半截会露出表格底色 —— 这条就是那团
        # 「空方块」的像素门，光看 DOM 高度发现不了。
        pt = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            var td = tr.querySelector('td.pool-reason');
            var r = td.getBoundingClientRect();
            return [Math.round(r.x + 4), Math.round(r.y + 4),
                    Math.round(r.x + 4), Math.round(r.bottom - 4),
                    window.devicePixelRatio];""")
        page.run_cdp('Input.dispatchMouseEvent', type='mouseMoved', x=pt[0], y=pt[1])
        time.sleep(0.5)
        import io as _io
        from PIL import Image
        图 = Image.open(_io.BytesIO(page.get_screenshot(as_bytes=True))).convert("RGB")
        比 = 图.width / js(page, "return window.innerWidth;")
        上 = 图.getpixel((int(pt[0] * 比), int(pt[1] * 比)))
        下 = 图.getpixel((int(pt[2] * 比), int(pt[3] * 比)))
        check("行悬停时理由列上下同一个色（没有露底的方块）",
              max(abs(a - b) for a, b in zip(上, 下)) <= 6, "上 %s 下 %s" % (上, 下))

        # ── 勾选列：框够大、整格可点、三态全选、计数、Shift 连选、按钮联动 ──
        # 他截图指的就是这一列：以前 26px 列宽减掉左右内边距只剩 6px，框被裁成一个点。
        尺寸 = js(page, """
            var td = document.querySelector('#poolTableBody td.pool-sel');
            var box = td.querySelector('input[type=checkbox]');
            var r = box.getBoundingClientRect(), c = td.getBoundingClientRect();
            return {框宽: Math.round(r.width), 框高: Math.round(r.height),
                    格宽: Math.round(c.width), 格高: Math.round(c.height),
                    在格子里: r.left >= c.left - 1 && r.right <= c.right + 1};""")
        check("行首勾选框放得下、看得清（不是被内边距裁成一个点）",
              尺寸["框宽"] >= 15 and 尺寸["框高"] >= 15
              and 尺寸["格宽"] >= 40 and 尺寸["在格子里"], str(尺寸))

        初始 = js(page, """
            return {计数: (document.getElementById('poolSelCount')||{}).textContent||'',
                    灰: ['poolBatchSelected','poolBatchNew','poolBatchRejected',
                         'poolDeleteSelected','poolClearChecked'].map(function(id){
                        var b = document.getElementById(id); return !!(b && b.disabled); })};""")
        check("还没勾任何行：计数是「未勾选」，批量按钮全是灰的",
              "未勾选" in 初始["计数"] and all(初始["灰"]), str(初始))

        js(page, "window.__首行 = document.querySelector('#poolTableBody tr'); return 1;")
        全选 = UI.click_until_alive(page, lambda p: p.eles("css:#poolCheckAll"))
        check("表头那颗全选框点得到", bool(全选))
        全后 = js(page, """
            var rows = [].slice.call(document.querySelectorAll('#poolTableBody tr'));
            var head = document.getElementById('poolCheckAll');
            return {勾了: rows.filter(function(tr){
                        return tr.querySelector('input.pool-acc').checked; }).length,
                    共: rows.length,
                    表头: head.checked, 半选: head.indeterminate,
                    计数: (document.getElementById('poolSelCount')||{}).textContent||'',
                    同一行: window.__首行 === rows[0],
                    灰: ['poolBatchSelected','poolDeleteSelected'].map(function(id){
                        return document.getElementById(id).disabled; })};""")
        check("表头一点，当前筛出的 5 行全勾上，表头是「全选」而不是半选",
              全后["勾了"] == 全后["共"] == 5 and 全后["表头"] and not 全后["半选"], str(全后))
        check("全选是就地改勾，没有把整张表重建（行节点还是原来那一个）",
              全后["同一行"], str(全后["同一行"]))
        check("已勾条数跟着走、批量按钮亮起来",
              "5" in 全后["计数"] and not any(全后["灰"]), str(全后))

        UI.click_until_alive(page, lambda p: p.eles(
            "css:#poolTableBody tr:nth-child(2) input.pool-acc"))
        半 = js(page, """
            var head = document.getElementById('poolCheckAll');
            var tr = document.querySelector('#poolTableBody tr');
            return {表头: head.checked, 半选: head.indeterminate,
                    这条: tr.querySelector('input.pool-acc').checked,
                    计数: (document.getElementById('poolSelCount')||{}).textContent||''};""")
        check("取消一条后表头是中间态（既不是全选也不是没选）",
              not 半["表头"] and 半["半选"] and "4" in 半["计数"], str(半))

        UI.click_until_alive(page, lambda p: p.eles("xpath://button[contains(.,'取消勾选')]"))
        清 = js(page, """
            var rows = [].slice.call(document.querySelectorAll('#poolTableBody tr'));
            var head = document.getElementById('poolCheckAll');
            return {没勾: rows.every(function(tr){
                        return !tr.querySelector('input.pool-acc').checked; }),
                    表头: head.checked,
                    计数: (document.getElementById('poolSelCount')||{}).textContent||'',
                    灰: document.getElementById('poolBatchSelected').disabled};""")
        check("「取消勾选」把勾全撤掉，表头和按钮跟着回到初始状态",
              清["没勾"] and not 清["表头"] and "未勾选" in 清["计数"] and 清["灰"], str(清))

        # Shift 连选：点第 1 行，再 Shift+点第 4 行 → 1~4 行全勾，第 5 行不动
        UI.click_until_alive(page, lambda p: p.eles(
            "css:#poolTableBody tr:nth-child(1) input.pool-acc"))
        按着shift点(page, "#poolTableBody tr:nth-child(4) input.pool-acc")
        段 = js(page, """
            var rows = [].slice.call(document.querySelectorAll('#poolTableBody tr'));
            var head = document.getElementById('poolCheckAll');
            return {勾的: rows.map(function(tr){
                        return tr.querySelector('input.pool-acc').checked ? 1 : 0; }),
                    半选: head.indeterminate,
                    计数: (document.getElementById('poolSelCount')||{}).textContent||''};""")
        check("Shift+点行首把一段一起勾上（第 5 行没被带上）",
              段["勾的"] == [1, 1, 1, 1, 0] and 段["半选"] and "4" in 段["计数"], str(段))

        # 收尾回到「什么都没勾」，后面的筛选用例照旧从干净状态开始
        UI.click_until_alive(page, lambda p: p.eles("xpath://button[contains(.,'取消勾选')]"))
        收 = js(page, """
            var rows = [].slice.call(document.querySelectorAll('#poolTableBody tr'));
            return {没勾: rows.every(function(tr){
                        return !tr.querySelector('input.pool-acc').checked; }),
                    灰: document.getElementById('poolDeleteSelected').disabled};""")
        check("Shift 勾完再取消，全部回到没勾、删除按钮变灰", 收["没勾"] and 收["灰"], str(收))

        # ── 筛选（筛在服务端；逐列那几格在表头下面那一行） ──
        check("表头那一格点得动（本地粗筛＝没过过的）", 设格(page, 'poolFilterGateState', '没过过的'))
        等到行数(page, 4)
        check("「本地粗筛＝没过过的」把否掉那条筛掉", 表里行数(page) == 4,
              "%s 条" % 表里行数(page))
        设格(page, 'poolFilterGateState', '')
        js(page, """
            document.getElementById('poolFilterQ').value = '标注';
            applyPoolFilter(); return 1;""")
        等到行数(page, 1)
        只一条 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            return tr ? tr.cells[1].textContent.trim() : '';""")
        check("关键字筛到岗位名，只剩那条标注", "数据标注" in 只一条, 只一条[:60])

        # 筛到一条都不剩：池里有货，就得说「没有符合当前筛选」，不许冒充空池
        js(page, """
            document.getElementById('poolFilterQ').value = '池里绝对没有这串字';
            applyPoolFilter(); return 1;""")
        空话 = {}
        for _ in range(16):
            空话 = js(page, """
                return {行: document.querySelectorAll('#poolTableBody tr').length,
                        题: (document.getElementById('poolEmptyMsg')||{}).textContent||'',
                        提示: (document.getElementById('poolEmptyHint')||{}).textContent||'',
                        显: (function(){var e = document.getElementById('poolEmpty');
                            return !!e && getComputedStyle(e).display !== 'none';})()};""") or {}
            if 空话.get("行") == 0 and "没有符合当前筛选" in 空话.get("题", ""):
                break
            time.sleep(0.5)
        check("筛到一条不剩时说清是筛选没命中，并指出去哪儿重置",
              空话.get("行") == 0 and "没有符合当前筛选" in 空话.get("题", "")
              and 空话.get("显") and "重置" in 空话.get("提示", ""), str(空话)[:150])
        js(page, "resetPoolFilter(); return 1;")
        等到行数(page, 5)
        check("点「重置」回到全部（空表提示也跟着收起来）",
              表里行数(page) == 5 and js(page, """
                var e = document.getElementById('poolEmpty');
                return !!e && getComputedStyle(e).display === 'none';"""), "5 条")

        # ── 地点下拉：选项按整池现算，选了就只剩那个城市 ──
        城市项 = js(page, """
            return [...document.querySelectorAll('#poolFilterCity option')].map(function(o){return o.textContent;});""") or []
        check("城市下拉按整池现算：两个城市都列出来并带条数",
              any("长沙" in x and "4" in x for x in 城市项)
              and any("上海" in x and "1" in x for x in 城市项), str(城市项))
        js(page, """
            var sel = document.getElementById('poolFilterCity');
            sel.value = '上海';
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return 1;""")
        等到行数(page, 1)
        只剩 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            return tr ? tr.cells[1].textContent.trim() : '';""")
        check("选上海就只剩那条上海的岗（下拉真的在筛，不只是摆着）",
              表里行数(page) == 1 and "数据标注" in 只剩,
              "%s 条 / %s" % (表里行数(page), 只剩[:20]))
        保 = js(page, """
            loadJobPool(true);
            return document.getElementById('poolFilterCity').value;""")
        time.sleep(0.8)
        check("重拉一次后他选的城市还在（不被冲回「全部城市」）",
              保 == "上海" and 表里行数(page) == 1, "%s / %s 条" % (保, 表里行数(page)))
        js(page, """
            var sel = document.getElementById('poolFilterCity');
            sel.value = '';
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return 1;""")
        等到行数(page, 5)

        # ── 逐列那几格（2026-10-29 他截图问「筛选这里不能加吗？」）：一列一格、各筛各的 ──
        格s = js(page, """
            return [...document.querySelectorAll
                ('#poolTable thead tr.pool-filter-row .pool-f')]
                .map(function(el){ return el.id; });""") or []
        check("表头下面那一行给每一列都摆了一格（10 格，对得上 12 列里的可筛那几列）",
              len(格s) == 10, str(格s))
        check("岗位那一格点得动", 设格(page, 'poolFilterJob', '分析'))
        等到行数(page, 1)
        岗位剩 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            return tr ? tr.cells[1].textContent.trim() : '';""")
        check("岗位格填「分析」只剩岗位名里带分析的那条",
              表里行数(page) == 1 and "数据分析师" in 岗位剩,
              "%s 条 / %s" % (表里行数(page), 岗位剩[:24]))
        # 公司格填一个只在别的列里出现的词：逐列不是"往每一列里搜"
        设格(page, 'poolFilterJob', '')
        check("公司那一格填岗位名里的字该一条不中", 设格(page, 'poolFilterCompany', '数据分析师'))
        等到行数(page, 0)
        check("公司格填岗位名＝筛空（证明它只认公司这一列）", 表里行数(page) == 0,
              "%s 条" % 表里行数(page))
        设格(page, 'poolFilterCompany', '某某制造')
        等到行数(page, 1)
        check("公司格填「某某制造」只剩那条普工", 表里行数(page) == 1,
              "%s 条" % 表里行数(page))
        设格(page, 'poolFilterCompany', '')
        等到行数(page, 5)

        # 薪资格：8-12K 折 8000、10-15K 折 10000，填 10 就该只剩那一条月薪上万的
        check("薪资那一格点得动", 设格(page, 'poolFilterSalaryK', '10'))
        等到行数(page, 1)
        薪资剩 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            return tr ? tr.cells[1].textContent.trim() : '';""")
        check("薪资≥10K/月 只剩 10-15K 那条（8-12K 折出来是 8000，不该混进来）",
              表里行数(page) == 1 and "产品经理" in 薪资剩,
              "%s 条 / %s" % (表里行数(page), 薪资剩[:24]))
        设格(page, 'poolFilterSalaryK', '')
        等到行数(page, 5)

        # 抓到那一格：先把普工那条的抓取时刻挪到 5 天前，不然 5 条全是刚抓的，筛不出差别
        池c = 岗位池(account_index=0, 目录=str(池目录))
        行s = 池c.列表()
        for r in 行s:
            if r["job_name"] == "普工/操作工":
                r["captured_at"] = time.time() - 5 * 86400
        池c._写(行s)
        check("抓到那一格点得动（今天）", 设格(page, 'poolFilterFresh', '1'))
        等到行数(page, 4)
        抓到名 = js(page, """
            return [...document.querySelectorAll('#poolTableBody tr')]
                .map(function(tr){ return tr.cells[1].textContent.trim(); });""") or []
        check("筛「今天」把 5 天前那条普工剔掉了",
              表里行数(page) == 4 and not any("普工" in x for x in 抓到名), str(抓到名)[:120])
        设格(page, 'poolFilterFresh', '7')
        等到行数(page, 5)
        check("改成「近 7 天」它又回来了（不是筛完就没了）", 表里行数(page) == 5,
              "%s 条" % 表里行数(page))
        设格(page, 'poolFilterFresh', '')
        等到行数(page, 5)

        # 两格同时填＝交集
        设格(page, 'poolFilterJob', '分析')
        check("两格一起填是交集：再叠一个城市就剩 0 条",
              设格(page, 'poolFilterCity', '上海'))
        等到行数(page, 0)
        check("岗位=分析 且 城市=上海 一条不剩（各自都有一条，合起来没有）",
              表里行数(page) == 0, "%s 条" % 表里行数(page))
        js(page, "resetPoolFilter(); return 1;")
        等到行数(page, 5)
        清完 = js(page, """
            var 值s = [...document.querySelectorAll
                ('#poolTable thead tr.pool-filter-row .pool-f')]
                .map(function(el){ return el.value; });
            return {都空: 值s.every(function(v){ return v === ''; }),
                    行: document.querySelectorAll('#poolTableBody tr').length};""") or {}
        check("重置把逐列那几格一次清干净（手写的清单会漏新加的一格）",
              清完.get("都空") and 清完.get("行") == 5, str(清完))

        # 他正在那一格里打字的时候，20 秒一次的轮询不许把格子换掉
        js(page, """
            var el = document.getElementById('poolFilterJob');
            el.__没换过 = 1;
            el.value = '报表';
            return 1;""")
        js(page, "loadJobPool(true); return 1;")
        time.sleep(1.2)
        还在 = js(page, """
            var el = document.getElementById('poolFilterJob');
            return {同一个: el.__没换过 === 1, 值: el.value,
                    行: document.querySelectorAll('#poolTableBody tr').length};""") or {}
        check("重拉一次表，他正在填的那一格还是原来那个元素（没被整行重建冲掉）",
              还在.get("同一个") and 还在.get("值") == "报表", str(还在))
        check("那一格填的字真筛出了 BI 那条", 还在.get("行") == 1, str(还在))
        js(page, "resetPoolFilter(); return 1;")
        等到行数(page, 5)

        # ── AI 自定义筛选：框看得见 → 文件读得进 → 存下就按它重判 → 只看匹配 ──
        面板 = js(page, """
            var p = document.getElementById('aiScreenPanel');
            var ta = document.getElementById('aiScreenText');
            var f = document.getElementById('aiScreenFile');
            return {shown: !!p && getComputedStyle(p).display !== 'none',
                    editable: !!ta && !ta.readOnly && !ta.disabled,
                    accept: f ? f.accept : '',
                    save: !!document.getElementById('aiScreenSave')};""") or {}
        check("池 tab 里就有一块 AI 自定义筛选的框（不是只剩一个按钮）",
              面板.get("shown") and 面板.get("editable")
              and ".docx" in str(面板.get("accept")) and 面板.get("save"), str(面板))
        要求 = "只要长沙的数据分析和数据标注岗；不要普工、外包。"
        解析回 = requests.post(URL + "/api/ai_screen_doc/parse", timeout=30,
                               files={"file": ("我的要求.txt", 要求.encode("utf-8"), "text/plain")})
        解析 = 解析回.json() if 解析回.status_code == 200 else {}
        check("他那个 .txt 文件能读成文字（只给他看，还没保存）",
              解析回.status_code == 200 and 要求 in str(解析.get("text") or ""),
              str(解析)[:120])
        未存 = requests.get(URL + "/api/ai_screen_doc", timeout=30).json()
        check("只解析不保存：盘上还没有这份要求",
              not (未存.get("doc") or {}).get("hash"),
              str((未存.get("doc") or {}).get("hash")))
        js(page, """
            var ta = document.getElementById('aiScreenText');
            ta.value = %s;
            ta.dispatchEvent(new Event('input', {bubbles: true}));
            return 1;""" % json.dumps(要求, ensure_ascii=False))
        保存钮 = UI.click_until_alive(page, lambda p: p.eles("css:#aiScreenSave"))
        check("「保存并匹配」按钮点得到", bool(保存钮))
        态 = {}
        for _ in range(40):
            态 = requests.get(URL + "/api/ai_screen_doc", timeout=30).json()
            r = 态.get("run") or {}
            if not r.get("running") and r.get("finished_at"):
                break
            time.sleep(0.4)
        文 = 态.get("doc") or {}
        统 = 态.get("stats") or {}
        check("要求真的存下来了（带指纹，换文件旧结论就不算）",
              bool(文.get("hash")) and 文.get("chars") == len(要求), str(文)[:120])
        check("后台按这份要求把两个号的池都判了（6 条里本地先否掉 1 条）",
              (态.get("run") or {}).get("judged") == 5 and 统.get("pending") == 0
              and 统.get("skipped") == 1,
              "run=%s stats=%s" % (态.get("run"), 统))
        check("判「合」的记了 4 条", 统.get("matched") == 4, str(统))
        进度 = {}
        for _ in range(30):
            进度 = js(page, """
                return {文: (document.getElementById('aiScreenDocName')||{}).textContent||'',
                        统: (document.getElementById('aiScreenStat')||{}).textContent||'',
                        跑: (document.getElementById('aiScreenRunText')||{}).textContent||''};""") or {}
            if "上一轮跑完" in 进度.get("跑", "") or "已停止" in 进度.get("跑", ""):
                break
            time.sleep(0.6)
        check("框里写着现在按的是哪份要求", "我的筛选要求" in 进度.get("文", ""),
              进度.get("文", "")[:60])
        check("跑完的账目上屏：判了 5、匹配 4",
              "已判 5" in 进度.get("统", "") and "匹配 4" in 进度.get("统", ""),
              进度.get("统", "")[:100])
        check("运行那行说清上一轮是跑完的",
              "上一轮跑完" in 进度.get("跑", "") and "判了 5" in 进度.get("跑", ""),
              进度.get("跑", "")[:100])
        AI档项 = js(page, """
            return [...document.querySelectorAll('#poolFilterAiState option')]
                .map(function(o){ return o.value; });""") or []
        check("AI 精筛那一格有未判/判过/合/不合四档",
              AI档项 == ["", "未判", "判过", "合", "不合"], str(AI档项))
        全0 = 读接口("?account=0")
        合0 = 读接口("?account=0&ai_state=" + quote("合"))
        check("筛「判成合」只剩判合的那几条（判「不合」的标注岗被剔掉）",
              0 < 合0["total"] < 全0["total"]
              and not any("数据标注" in r["job_name"] for r in 合0["rows"]),
              "合 %s / 全部 %s" % (合0["total"], 全0["total"]))
        check("顶部那个「AI匹配」与筛「判成合」是同一个数（判据只有一份）",
              全0["stats"].get("AI匹配") == 合0["total"],
              "统计 %s / 筛出 %s" % (全0["stats"].get("AI匹配"), 合0["total"]))
        check("表头那一格点得动（AI 精筛＝判成合）", 设格(page, 'poolFilterAiState', '合'))
        等到行数(page, 合0["total"])
        check("屏上剩下的行数就是接口筛出来的那条数（三端一个数）",
              表里行数(page) == 合0["total"], "屏上 %s / 接口 %s" % (
                  表里行数(page), 合0["total"]))
        设格(page, 'poolFilterAiState', '')
        等到行数(page, 5)

        # ── 勾选投递：真点勾选框 + 真点按钮 ──
        勾 = UI.click_until_alive(page, lambda p: p.eles(
            "css:#poolTableBody tr:nth-child(1) input[type=checkbox]"))
        check("第一行的勾选框点得动", bool(勾))
        勾住 = js(page, """
            var tr = document.querySelector('#poolTableBody tr');
            return {checked: tr.querySelector('input[type=checkbox]').checked,
                    job: tr.cells[1].textContent.trim().slice(0, 12)};""")
        check("勾真的打上了", 勾住["checked"], str(勾住))
        投 = UI.click_until_alive(page, lambda p: p.eles("xpath://button[contains(.,'投递选中的')]"))
        check("「投递选中的」按钮点得到", bool(投))
        目标id = None
        for _ in range(15):
            选中 = [r for r in 读接口("?account=0")["rows"] if r["status"] == "selected"]
            if 选中:
                目标id = 选中[0]["id"]
                break
            time.sleep(0.6)
        check("勾的那条在盘上变成 selected", 目标id is not None, str(目标id))
        check("盘上只这一条被勾，别的一条没动",
              len([r for r in 盘上池(池目录, 0) if r["status"] == "selected"]) == 1)
        # 气泡是 3 秒后自己收走的：抽一次可能正好抽在它出现之前/消失之后，
        # 有界地看 8 秒，真没弹出来照样红
        提示话 = ""
        for _ in range(16):
            提示话 = js(page, "return (document.querySelector('#toastStack')||{}).textContent||'';")
            if "1 条" in 提示话 and "没启动" in 提示话:
                break
            time.sleep(0.5)
        check("点了以后界面当场说了话，并且说清机器人没启动",
              "1 条" in 提示话 and "没启动" in 提示话, 提示话[:140])
        check("这一步没在浏览器里点过任何沟通（池文件是唯一变化）",
              len(盘上池(池目录, 0)) == 5)
        写接口("/api/job_pool/update", {"account": 0, "id": 目标id, "status": "new"})

        # ── 备注：真敲字 → 中途不许被重画冲掉 → 离开这一格才落盘 ──
        进 = UI.click_until_alive(page, lambda p: p.eles(
            "css:#poolTableBody tr:nth-child(1) input.pool-note-input"))
        check("备注框点得进去", bool(进))
        打的字 = "薪资可以谈"
        page.actions.type(打的字)
        if 打的字 not in (js(page, """
            var i = document.querySelector('#poolTableBody tr input.pool-note-input');
            return i ? i.value : '';""") or ""):
            # 这条测的是"别被轮询重画冲掉"，不是中文输入法；键入没落地就换成 ASCII 继续测不变量
            打的字 = "pay-negotiable"
            page.actions.type(打的字)
        中途 = js(page, "loadJobPool(); return 1;")     # 非 force：模拟 20 秒轮询敲门
        time.sleep(1.0)
        还在 = js(page, """
            var a = document.activeElement;
            var tr = document.querySelector('#poolTableBody tr');
            var i = tr ? tr.querySelector('input.pool-note-input') : null;
            return {focus: !!(a && a.classList && a.classList.contains('pool-note-input')),
                    val: i ? i.value : '', 行: document.querySelectorAll('#poolTableBody tr').length};""")
        check("正在打字时轮询不许把这一格重画掉（光标和已打的字都还在）",
              还在["focus"] and 打的字 in 还在["val"], str(还在))
        UI.click_until_alive(page, lambda p: tab_button(p, "打招呼记录"))
        落盘 = []
        for _ in range(14):
            落盘 = [r for r in 盘上池(池目录, 0) if 打的字 in (r.get("note") or "")]
            if 落盘:
                break
            time.sleep(0.5)
        check("离开这一格后备注真的落到盘上", bool(落盘), str(落盘)[:120])
        UI.click_until_alive(page, lambda p: tab_button(p, "岗位候选池"))
        回显 = ""
        for _ in range(14):
            回显 = js(page, """
                var i = document.querySelector('#poolTableBody input.pool-note-input');
                return i ? i.value : '';""") or ""
            if 打的字 in 回显:
                break
            time.sleep(0.6)
        check("重进这一页还能看到自己写的备注（不是只活在内存里）", 打的字 in 回显, str(回显))

        # ── 行内状态下拉直接改 ──
        js(page, """
            var sel = document.querySelector('#poolTableBody tr select.pool-status');
            sel.value = 'rejected';
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return 1;""")
        盘 = []
        for _ in range(12):
            盘 = [r for r in 盘上池(池目录, 0) if r["status"] == "rejected"]
            if len(盘) >= 2:
                break
            time.sleep(0.5)
        check("行内状态改成「不投」直接落盘", len(盘) >= 2, "%d 条不投" % len(盘))

        # ── 手工加一条 ──
        js(page, """
            document.getElementById('poolAddUrl').value = 'https://www.zhipin.com/job_detail/pool900.html';
            document.getElementById('poolAddName').value = '手工加的数据分析岗';
            document.getElementById('poolAddCompany').value = '某某研究院';
            document.getElementById('poolAddSalary').value = '11-14K';
            return 1;""")
        加 = UI.click_until_alive(page, lambda p: p.eles("xpath://button[contains(.,'加进池')]"))
        check("「加进池」按钮点得到", bool(加))
        等到行数(page, 6)
        新行 = [r for r in 盘上池(池目录, 0) if r["job_name"] == "手工加的数据分析岗"]
        check("手工粘链接能进池并且出现在表里",
              bool(新行) and 表里行数(page) == 6, "盘上 %d 条" % len(新行))
        空链接 = 写接口("/api/job_pool/add", {"account": 0, "url": "", "job_name": "没链接的"})
        check("没链接的照实挡回，不收进池",
              空链接[0] == 400 and "链接" in str(空链接[1].get("message", "")),
              str(空链接[1].get("message", ""))[:44])

        # ── 删除：先问一句才动手 ──
        js(page, """
            window.__问过的 = [];
            window.confirm = function(s){ window.__问过的.push(s); return true; };
            return 1;""")
        UI.click_until_alive(page, lambda p: p.eles(
            "css:#poolTableBody tr:nth-child(1) input[type=checkbox]"))
        删 = UI.click_until_alive(page, lambda p: p.eles("xpath://button[contains(.,'删除选中的')]"))
        check("「删除选中的」按钮点得到", bool(删))
        等到行数(page, 5)
        问过 = js(page, "return window.__问过的.join('|');")
        check("删之前先弹了确认（不是悄悄删数据）", "删掉" in 问过, 问过[:90])
        check("删完表里少一条、盘上也少一条",
              表里行数(page) == 5 and len(盘上池(池目录, 0)) == 5,
              "界面 %s / 盘上 %d" % (表里行数(page), len(盘上池(池目录, 0))))

        # ── 换数据范围：池必须跟着换号 ──
        # 只数数据范围那一组：无头/有头两颗按钮和判分复盘的账号按钮同用 .scope-chip，
        # 取全局会把 3 颗数成 5 颗，看着像"切换条多了俩"，其实是量错了地方
        号数 = len(((配置().get("accounts") or [])))
        chips = js(page, "return document.querySelectorAll('#metricsScope [data-scope]').length;")
        check("数据范围切换条在（全部账号 + 每个号一颗）",
              chips == 号数 + 1, f"{chips} 颗 / 期望 {号数 + 1} 颗")
        # 换范围前先勾一条：屏上换号后这批勾必须消失，
        # 否则「删除选中的」会对着他看不见的另一批行动手
        UI.click_until_alive(page, lambda p: p.eles(
            "css:#poolTableBody tr:nth-child(1) input.pool-acc"))
        勾着 = js(page, "return (document.getElementById('poolSelCount')||{}).textContent||'';")
        check("换范围前勾了一条（有勾才谈得上清）", "已勾" in 勾着, 勾着)
        UI.click_chip(page, -1)
        换后 = {}
        for _ in range(14):
            换后 = js(page, """
                var tr = document.querySelector('#poolTableBody tr');
                return {名: tr ? tr.cells[2].textContent.trim() : '',
                        行: document.querySelectorAll('#poolTableBody tr').length,
                        计数: (document.getElementById('poolSelCount')||{}).textContent||'',
                        灰: document.getElementById('poolDeleteSelected').disabled};""") or {}
            if 换后.get("行") == 1 and "另一个号" in 换后.get("名", ""):
                break
            time.sleep(0.6)
        check("切到另一个号后表里只有那个号的池（不串号）",
              换后.get("行") == 1 and "另一个号" in 换后.get("名", ""),
              "%s 条 / 公司 %s" % (换后.get("行"), 换后.get("名", "")[:24]))
        check("切号后上一批的勾被撤掉：计数回到「未勾选」、删除按钮变灰",
              换后.get("计数", "").strip() == "未勾选" and 换后.get("灰"), str(换后)[:140])
        UI.click_chip(page, 0)          # 全部账号
        合2 = {}
        for _ in range(14):
            合2 = js(page, """
                return {行: document.querySelectorAll('#poolTableBody tr').length,
                        标: document.querySelectorAll('#poolTableBody .pool-acc-tag').length,
                        角标: (document.getElementById('poolTabCount')||{}).textContent||''};""") or {}
            if 合2.get("行") == 6:
                break
            time.sleep(0.6)
        check("切回「全部账号」又并回两号的池（不是回一句空表）",
              合2.get("行") == 6 and 合2.get("标") == 6 and 合2.get("角标") == "6",
              str(合2)[:140])

        # ── 两把配置开关要真生效（改了必须生效） ──
        UI.click_until_alive(page, lambda p: p.eles("css:#advToggle"))
        time.sleep(0.8)
        原来 = ((配置().get("greet") or {}).get("capture_only"))
        # 配置面板是展开动画 + 周期性重渲染：第一下可能落在动画半途被吃掉。
        # 翻不动就再点（最多 3 下、12 秒），真的没接上照样红。
        开关 = UI.click_until_alive(page, lambda p: p.eles("css:#advCaptureOnly"))
        check("「只抓不投」开关点得到", bool(开关))
        现在 = 原来
        for _ in range(3):
            UI.click_until_alive(page, lambda p: p.eles("css:#advCaptureOnly"))
            for _ in range(8):
                现在 = ((配置().get("greet") or {}).get("capture_only"))
                if 现在 != 原来:
                    break
                time.sleep(0.5)
            if 现在 != 原来:
                break
        check("点了开关后配置真的翻了面（不是画个假开关）",
              现在 != 原来 and 现在 in (True, False), "%s -> %s" % (原来, 现在))
        for _ in range(3):
            UI.click_until_alive(page, lambda p: p.eles("css:#advCaptureOnly"))
            for _ in range(8):
                现在 = ((配置().get("greet") or {}).get("capture_only"))
                if 现在 is True:
                    break
                time.sleep(0.5)
            if 现在 is True:
                break
        check("再点一下翻回来，默认仍是只抓不投", 现在 is True, str(现在))

        js(page, """
            var el = document.getElementById('aiScreenMaxPerRound');
            el.value = '7';
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 1;""")
        额度 = None
        for _ in range(14):
            额度 = (配置().get("ai") or {}).get("screen_max_per_round")
            if 额度 == 7:
                break
            time.sleep(0.5)
        check("精筛额度输入框改了就落进配置", 额度 == 7, str(额度))
        js(page, """
            var el = document.getElementById('aiScreenMaxPerRound');
            el.value = '0';
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 1;""")
        for _ in range(14):
            额度 = (配置().get("ai") or {}).get("screen_max_per_round")
            if 额度 == 0:
                break
            time.sleep(0.5)
        check("额度填 0 是「关掉」而不是被兜回 40", 额度 == 0, str(额度))

        # 合并视图多一格就是整行错位；单号视图也一样，两档都逐行比
        错位合 = js(page, """
            var trs = [...document.querySelectorAll('#poolTableBody tr')];
            return trs.filter(function(tr){ return tr.cells.length !== 12; }).length;""")
        check("「全部账号」合并视图没有一行错位（逐行比列数）", 错位合 == 0, "%d 行错位" % 错位合)
        UI.click_chip(page, 1)
        等到行数(page, 5)
        错位 = js(page, """
            var trs = [...document.querySelectorAll('#poolTableBody tr')];
            return trs.filter(function(tr){ return tr.cells.length !== 12; }).length;""")
        check("整轮点下来没有一行错位（逐行比列数）", 错位 == 0, "%d 行错位" % 错位)
        # ── 2026-10-29 岗位池口径②④：修饰型否决词不许静默删、勾选要留痕 ──
        池b = 岗位池(account_index=0, 目录=str(池目录))
        池b.收([
            {"url": "https://www.zhipin.com/job_detail/pool901.html",
             "job_name": "AI数据标注（提供住宿）", "company": "住宿样例",
             "salary": "6-8K", "location": "长沙", "query": "标注"},
            {"url": "https://www.zhipin.com/job_detail/pool902.html",
             "job_name": "数据标注城市合伙人", "company": "合伙样例",
             "salary": "6-8K", "location": "长沙", "query": "标注"},
        ])
        名b = {r["job_name"]: r["id"] for r in 池b.列表()}
        # 这两条形状是盘上真有的：方向命中 + 一个修饰型否决词 / 一个硬拦族词
        池b.改(名b["AI数据标注（提供住宿）"],
              gate={"方向命中": True, "否决命中": "住宿", "要人看一眼": True,
                    "依据": "卡片(未开JD)"})
        池b.改(名b["数据标注城市合伙人"],
              gate={"方向命中": True, "否决命中": "合伙人", "依据": "卡片(未开JD)"})
        设格(page, 'poolFilterGateState', '')
        js(page, """document.getElementById('poolFilterQ').value = '';
            applyPoolFilter(); return 1;""")
        等到行数(page, 7)
        两行 = js(page, """
            function 找字(n){
              var trs = [...document.querySelectorAll('#poolTableBody tr')];
              return trs.find(function(tr){ return (tr.cells[1].textContent||'').indexOf(n) >= 0; });
            }
            var a = 找字('提供住宿'), b = 找字('城市合伙人');
            return {住宿在: !!a, 住宿标: a ? (a.cells[7].textContent||'') : '',
                    合伙在: !!b, 合伙标: b ? (b.cells[7].textContent||'') : ''};""") or {}
        check("方向命中只撞修饰词的，界面上标出「要人看一眼」",
              两行.get("住宿在") and "要人看一眼" in 两行.get("住宿标", ""), str(两行)[:120])
        check("硬拦族（合伙人）不标「要人看一眼」",
              "要人看一眼" not in 两行.get("合伙标", ""), str(两行.get("合伙标"))[:60])
        check("表头那一格点得动（本地粗筛＝没过过的）",
              设格(page, 'poolFilterGateState', '没过过的'))
        等到行数(page, 5)
        筛后 = js(page, """
            var trs = [...document.querySelectorAll('#poolTableBody tr')];
            var 名s = trs.map(function(tr){ return (tr.cells[1].textContent||'').slice(0,12); });
            return {行: trs.length,
                    住宿在: 名s.some(function(x){ return x.indexOf('提供住宿') >= 0; }),
                    合伙在: 名s.some(function(x){ return x.indexOf('城市合伙人') >= 0; }),
                    普工在: 名s.some(function(x){ return x.indexOf('普工') >= 0; })};""") or {}
        check("筛「没过过的」：方向正主不再被静默删掉",
              筛后.get("住宿在") is True, str(筛后))
        check("同一次筛选里硬拦族照旧藏起来、普工也照旧藏（筛子没坏）",
              筛后.get("合伙在") is False and 筛后.get("普工在") is False
              and 筛后.get("行") == 5, str(筛后))
        设格(page, 'poolFilterGateState', '')
        等到行数(page, 7)
        点中 = UI.click_until_alive(page, lambda p: p.eles(
            "xpath://tr[contains(.,'提供住宿')]/td[@class='pool-sel']//input"))
        check("这一行的勾选框点得到", bool(点中))
        投b = UI.click_until_alive(page, lambda p: p.eles("xpath://button[contains(.,'投递选中的')]"))
        check("点了「投递选中的」（机器人没启动也只入队，不碰浏览器）", bool(投b))
        痕 = {}
        for _ in range(12):
            痕 = dict((池b.取(名b["AI数据标注（提供住宿）"]) or {}).get("勾选") or {})
            if 痕.get("时间"):
                break
            time.sleep(0.5)
        check("勾完盘上答得出是谁动的：来源/动作/时间/首于都在",
              痕.get("来源") == "面板点击" and 痕.get("动作") == "勾成要投"
              and 痕.get("时间") and 痕.get("首于"), str(痕)[:120])
        界面痕 = js(page, """
            var trs = [...document.querySelectorAll('#poolTableBody tr')];
            var tr = trs.find(function(t){ return (t.cells[1].textContent||'').indexOf('提供住宿') >= 0; });
            return tr ? (tr.cells[8].textContent||'') : '';""") or ""
        check("界面上那一行也写着「勾成要投 · 刚刚」", "勾成要投" in 界面痕, 界面痕[:60])
        page.get_screenshot(str(SHOTS / "job_pool_after.png"))

        # ── 分页（他 10-30 说「感觉现在很卡」，根之一就是三千行全铺进 DOM）──
        # 这一屏的期望全部从接口现取：写死 200/250 这种数，池子形状一变就红成假警报
        池b.收([{"url": "https://www.zhipin.com/job_detail/page%03d.html" % i,
                 "job_name": "分页样例%03d" % i, "company": "分页公司%03d" % i,
                 "salary": "6-8K", "location": "长沙", "query": "标注"}
                for i in range(243)])
        js(page, "loadJobPool(true); return 1;")
        全 = 读接口("?account=0")     # 屏上此刻就是这个号的视图（上面 click_chip 过）
        N = int(全.get("total") or 0)
        期 = [(r.get("job_name") or "").strip() for r in (全.get("rows") or [])]
        每页 = js(page, "return POOL页.每页;")
        check("一页就铺 200 条（不是把三千行全塞进 DOM，那才是卡的那一下）", 每页 == 200, str(每页))
        check("造的样例把池子顶过了一页（不然下面几条等于没测）", N > 每页 and len(期) == N,
              "共 %s 条 / 接口 %d 行" % (全.get("total"), len(期)))

        def 屏上():
            # 岗位名读 <b>：那一格里还挂着「来自搜索…」和投递结局两行小字，
            # 整格 textContent 会把它们拼进来，比对就成了假红
            return js(page, """
                var 文 = (document.getElementById('poolPagerText')||{}).textContent||'';
                var 显 = (document.getElementById('poolPager')||{}).style.display||'';
                var 名 = [].slice.call(document.querySelectorAll('#poolTableBody tr'))
                        .map(function(tr){
                          var b = tr.cells[1] && tr.cells[1].querySelector('b');
                          return b ? b.textContent.trim() : '';
                        });
                var 勾 = document.getElementById('poolCheckAll');
                return {文: 文, 显: 显, 行: 名.length, 头: 名[0]||'', 尾: 名[名.length-1]||'',
                        空态: (document.getElementById('poolEmpty')||{}).style.display||'',
                        全选话: (勾 && 勾.parentNode) ? (勾.parentNode.title||'') : ''};""") or {}

        等到行数(page, 每页, 次=30)
        一 = 屏上()
        check("屏上就是接口那份的第一页：200 行、首尾两个岗位名都对得上",
              一.get("行") == 每页 and 一.get("头") == 期[0] and 一.get("尾") == 期[每页 - 1],
              "行=%s 头=%s 尾=%s" % (一.get("行"), 一.get("头"), 一.get("尾")))
        check("分页条露出来并写清「第 1–200 条 / 共 N 条」",
              一.get("显") != "none" and 一.get("文") == "第 1–%d 条 / 共 %d 条" % (每页, N),
              str(一.get("文")))
        check("表头全选的提示说的是本页，还带上筛出来一共几条",
              ("本页这 %d 条" % 每页) in 一.get("全选话", "") and ("筛出 %d 条" % N) in 一.get("全选话", ""),
              一.get("全选话", "")[:120])
        点下 = UI.click_until_alive(page, lambda p: p.eles(
            "xpath://span[@id='poolPager']/button[2]"))
        check("下一页那颗点得到", bool(点下))
        等到行数(page, N - 每页, 次=30)
        二 = 屏上()
        check("点了下一页就停在第二页那一屏（首行接在第一页尾行后面）",
              二.get("行") == N - 每页 and 二.get("头") == 期[每页],
              "行=%s 头=%s 期望头=%s" % (二.get("行"), 二.get("头"), 期[每页]))
        check("第二页的分页条数字跟着走",
              二.get("文") == "第 %d–%d 条 / 共 %d 条" % (每页 + 1, N, N), str(二.get("文")))
        # 20 秒轮询走的也是 loadJobPool：拉完得停在原来那一页，不能把人踢回第一页
        js(page, "loadJobPool(true); return 1;")
        等到行数(page, N - 每页, 次=30)
        check("重拉一次还停在第二页（轮询不许把人踢回第一页）", 屏上().get("文") ==
              "第 %d–%d 条 / 共 %d 条" % (每页 + 1, N, N), str(屏上().get("文")))
        # 停在第二页时把这一页筛空了：得退回第一页，不能指着空表说"没有符合当前筛选的岗位"
        设格(page, 'poolFilterJob', '分页样例001')
        等到行数(page, 1, 次=30)
        js(page, "POOL页.偏移 = %d; loadJobPool(true); return 1;" % 每页)
        等到行数(page, 1, 次=30)
        退 = 屏上()
        check("停在看不见的页上时退回第一页，不报一句假的「没有符合当前筛选」",
              退.get("行") == 1 and 退.get("空态") in ("", "none") and 退.get("显") == "none",
              str(退)[:160])
        设格(page, 'poolFilterJob', '')
        等到行数(page, 每页, 次=30)
        check("清空那一格就回到 200 行的一页（重置路径没把人卡在空页上）",
              屏上().get("行") == 每页)
        page.get_screenshot(str(SHOTS / "job_pool_pager.png"))
    finally:
        after = E.sha_of_cookies()
        变 = [k for k in before if before[k] != after[k]]
        check("两个号的 Cookie 一个字节都没变", before == after,
              ("变了的：" + "、".join(变)) if 变 else "全部一致")
        现在戳 = 真实要求.stat().st_mtime_ns if 真实要求.exists() else None
        check("实测保存的筛选要求没落到真那份上", 现在戳 == 真实要求戳,
              str(真实要求))
        if browser:
            UI.close_browser(browser)
        if proc:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print("\n共 %d 项，失败 %d" % (len(results), len(failed)))
    for 名, _, 细节 in failed:
        print("  FAIL %s — %s" % (名, 细节))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
