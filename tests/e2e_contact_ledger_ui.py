"""「联系与简历」台账的真机点击测试（task #114）。

只用临时面板（默认 5059），绝不碰线上面板：
线上面板跑的是旧代码，在它上面测出来的"通过"是假的，
而且实测脚本会抢那个口、真点启停，会把投递打断。

跑法：python tests/e2e_contact_ledger_ui.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from tests.conftest import mk测试临时目录, 挑空闲端口  # noqa: E402
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from boss_bot.unified_config import 面板端口 as _面板端口  # 线上面板口只有一个出处

import e2e_account_scope_ui as E  # noqa: E402
import e2e_greet_records_ui as UI  # noqa: E402

PORT = 挑空闲端口(int(os.environ.get("BOSS_LEDGER_PORT", "5059")), "联系与简历台账实测")
URL = f"http://127.0.0.1:{PORT}"
SHOTS = PROJECT_ROOT / "tests" / "screenshots"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""), flush=True)


def api(qs=""):
    r = requests.get(f"{URL}/api/contact_ledger{qs}", timeout=180)
    r.raise_for_status()
    data = r.json()
    assert data.get("status") == "ok", data
    return data


def js(page, code):
    return page.run_js(code)


def tab_button(page, label):
    """只认那三个 tab 按钮：页面上还有「下载打招呼记录」这类按钮，
    按文字包含会先点到它身上，点了既不切 tab 也不报错，测试就白跑。"""
    return page.eles(f"xpath://button[contains(@class,'record-tab')][contains(.,'{label}')]")


def main():
    线上口 = _面板端口()
    assert (":%s" % 线上口) not in URL and PORT != 线上口, "实测禁止打线上面板"
    tmp = mk测试临时目录("boss_ledger")
    proc = None
    browser = None
    try:
        E.seed(tmp)
        # 句柄必须接住：这套跑完会 proc.kill()，接不住就每次留一个临时面板占着口和内存
        proc = E.start_server(tmp, PORT)
        check("临时面板起来了", True, URL)

        all_rows = api()["rows"]
        a0 = api("?account=0")["total"]
        a1 = api("?account=1")["total"]
        check("接口有数据", len(all_rows) > 0, f"全部 {len(all_rows)} 行")
        check("按号分不重不漏", a0 + a1 == len(all_rows), f"{a0}+{a1} vs {len(all_rows)}")
        with_num = [r for r in all_rows if r["wechats"] or r["phones"]]
        check("至少一条真拿到号码", len(with_num) > 0, f"{len(with_num)} 行带号")

        page, browser = UI.open_browser()
        page.get(URL)
        # 定长 sleep 在这台机器上不够：同机挂着两号的浏览器和别的实测窗口，页面还在
        # 加载就读 DOM，读到的就是"元素不存在"——那是测试读早了，不是界面坏了
        for _ in range(30):
            try:
                if js(page, "return !!document.getElementById('greetTabContent');"):
                    break
            except Exception:
                pass
            time.sleep(0.8)
        else:
            check("页面真加载出来了", False, "等 24 秒仍读不到 #greetTabContent")
        time.sleep(0.6)

        # 先证明我重写过的 switchRecordTab 没把原来两个 tab 弄坏
        UI.click_until_alive(page, lambda p: tab_button(p, "打招呼记录"))
        time.sleep(0.8)
        greet_state = js(page, """
            return {on: document.getElementById('greetTabContent').classList.contains('active'),
                    bar: getComputedStyle(document.getElementById('greetFilterBar')).display !== 'none',
                    clear: getComputedStyle(document.getElementById('btnClearRecord')).display !== 'none'};""")
        check("打招呼记录照旧可选中、筛选栏和清空都在",
              greet_state["on"] and greet_state["bar"] and greet_state["clear"], str(greet_state))
        UI.click_until_alive(page, lambda p: tab_button(p, "回复记录"))
        time.sleep(0.8)
        reply_state = js(page, """
            return {on: document.getElementById('replyTabContent').classList.contains('active'),
                    greetOff: !document.getElementById('greetTabContent').classList.contains('active'),
                    bar: getComputedStyle(document.getElementById('replyFilterBar')).display !== 'none'};""")
        check("回复记录照旧可选中，打招呼的筛选栏收起",
              reply_state["on"] and reply_state["greetOff"] and reply_state["bar"], str(reply_state))

        tab = UI.click_until_alive(page, lambda p: tab_button(p, "联系与简历"))
        check("点得到「联系与简历」这个 tab", tab)
        # 首屏要扫 800 多个会话存档，等一下再判
        for _ in range(30):
            n = js(page, "return document.querySelectorAll('#contactTableBody tr').length;")
            if n and "捞" not in js(page, "return document.getElementById('contactTableBody').textContent;"):
                break
            time.sleep(1.0)
        shown = js(page, "return document.querySelectorAll('#contactTableBody tr').length;")
        badge = js(page, "return document.getElementById('contactTabCount').textContent;")
        check("表里真渲染出行", shown == len(all_rows), f"渲染 {shown} / 接口 {len(all_rows)}")
        check("tab 上的计数和表里一致", str(badge) == str(len(all_rows)), f"角标 {badge}")

        cells = js(page, """
            var tr = document.querySelector('#contactTableBody tr');
            return tr ? tr.cells.length : 0;""")
        check("每行 10 列（时间/号/HR·公司/岗位/薪资/方式/微信名/号码/简历/依据）",
              cells == 10, f"{cells} 列")

        row_check = js(page, """
            var tr = document.querySelector('#contactTableBody tr');
            if (!tr) return null;
            var t = function(i){ return tr.cells[i].textContent.trim(); };
            return {time:t(0), acc:t(1), who:t(2), job:t(3), pay:t(4), quote:t(9)};""")
        check("首行有时间、公司、岗位", bool(row_check and row_check["time"] and row_check["who"]
                                and row_check["job"]), str(row_check)[:150])
        check("岗位不带「查看职位」这种侧栏尾巴",
              all("查看职位" not in (r["job_title"] or "") for r in all_rows))
        # 说法这一列整列判，不拿"排在最前面那一行"当样本：510 行里 176 行本来就没
        # 联系方式（只聊简历那一档），行序一变这条就假红（实测红过一次）
        说法 = ("卡片已同意", "卡片已拒绝", "给了号码", "发起交换请求", "说要给没留号")
        kinds = js(page, """
            return [...document.querySelectorAll('#contactTableBody tr')]
                .filter(function(tr){ return tr.cells.length > 1; })
                .map(function(tr){ return tr.cells[5].textContent.trim(); });""")
        check("联系方式那一列只有那五种说法，其余画破折号",
              all(k in 说法 + ("—",) for k in kinds), str(sorted(set(kinds)))[:120])
        check("至少一行真带着说法", any(k in 说法 for k in kinds),
              "%d/%d 行有说法" % (len([k for k in kinds if k in 说法]), len(kinds)))
        空名行 = js(page, "return document.querySelectorAll('#contactTableBody .ledger-name').length;")
        check("微信名那一列有破折号也有名字，两种都画出来了",
              0 < 空名行 < len(all_rows), f"{空名行} 行有名字 / 共 {len(all_rows)} 行")

        nums = js(page, "return document.querySelectorAll('#contactTableBody .ledger-num').length;")
        api_nums = sum(len(r["wechats"]) + len(r["phones"]) for r in all_rows)
        check("号码渲染成可点的复制块", nums == api_nums, f"界面 {nums} / 数据 {api_nums}")

        names = js(page, "return document.querySelectorAll('#contactTableBody .ledger-name').length;")
        api_names = sum(len(r.get("wechat_names") or []) for r in all_rows)
        check("微信名渲染成可点的复制块", names == api_names, f"界面 {names} / 数据 {api_names}")

        抄 = js(page, """
            window.__抄 = [];
            copyLedgerContact = function(t) { window.__抄.push(t); };
            var el = document.querySelector('#contactTableBody .ledger-name');
            if (!el) return {no: '一个微信名都没渲染出来'};
            var 文 = el.textContent.trim(), 属 = el.getAttribute('data-copy');
            el.click();
            return {文: 文, 属: 属, 抄: window.__抄};""")
        check("点微信名真能复制，且拿到的就是屏上那一串字",
              bool(抄.get("文")) and 抄.get("文") == 抄.get("属")
              and (抄.get("抄") or [""])[0] == 抄.get("文"), str(抄)[:150])

        # 名字是 BOSS 那边来的外部文本：带引号时不许把界面这句 JS 打断
        转义 = js(page, """
            var 名 = 'Bob' + String.fromCharCode(39) + 's 微' + String.fromCharCode(34) + '信';
            var h = ledgerNameHtml({wechat_names: [名], chat_name: '别人',
                                   wechat_given: true, wechat_name_note: ''});
            var d = document.createElement('div'); d.innerHTML = h;
            var s = d.querySelector('.ledger-name');
            return {attr: s.getAttribute('data-copy'), text: s.textContent,
                    有onclick: s.hasAttribute('onclick')};""")
        原样 = "Bob's 微\"信"
        check("带单双引号的微信名原样回到 data-copy 和文本（没被截断也没多字）",
              转义.get("attr") == 原样 and 转义.get("text") == 原样, str(转义)[:170])
        check("名字不拼进 onclick（拼进去引号会把那句 JS 打断）",
              转义.get("有onclick") is False, str(转义)[:120])

        # 统计条：数字只能来自接口，界面不许自己数一遍
        st = api().get("stats") or {}
        bar = js(page, "return document.getElementById('contactStats').textContent;")
        check("统计条写了给了微信号的人数且与接口一致",
              ("给了微信号 %d 个会话" % st.get("wechat_given", -1)) in bar, bar[:120])
        空态 = js(page, """
            var td = [...document.querySelectorAll('#contactTableBody .ledger-name')]
                .filter(function(s){ return s.textContent.trim()===''; }).length;
            return td;""")
        check("渲染出来的微信名没有空字（取不到的画破折号而不是空白）", 空态 == 0, f"{空态} 个空格子")

        chips = js(page, """
            return [...document.querySelectorAll('#contactFilterChips [data-ledger-bucket]')]
                .map(function(b){ return [b.getAttribute('data-ledger-bucket'), b.textContent.trim()]; });""")
        期 = {b["name"]: f"{b['name']} {b['count']}" for b in (st.get("buckets") or [])}
        check("筛选按钮一颗一个档，颗数等于档位表",
              len(chips) == len(期), f"界面 {len(chips)} / 接口 {len(期)}")
        check("每颗按钮上的数字就是接口那个档的数",
              all(t == 期.get(n) for n, t in chips), str(chips)[:180])

        hidden = js(page, "return getComputedStyle(document.getElementById('btnClearRecord')).display;")
        check("台账这一页藏掉了「清空」（现算视图没有可清的东西）", hidden == "none", hidden)

        quote_ok = js(page, """
            var els = document.querySelectorAll('#contactTableBody .ledger-quote');
            for (var i=0;i<els.length;i++){ if (els[i].textContent.length > 200) return 'long'; }
            return 'ok';""")
        check("依据原话不会长到撑破行", quote_ok == "ok")

        SHOTS.mkdir(parents=True, exist_ok=True)
        page.get_screenshot(str(SHOTS / "contact_ledger_all.png"))

        diff = js(page, "return document.querySelectorAll('#contactTableBody .ledger-name-diff').length;")
        api_diff = len([r for r in all_rows
                        if r.get("wechat_names") and r["chat_name"] not in r["wechat_names"]])
        check("微信名和 BOSS 会话名对不上的行标了出来（认人就靠这个）",
              diff == api_diff and api_diff >= 1, f"界面 {diff} / 数据 {api_diff}")

        # 点「给了微信号」这一档：行数要等于接口那个数，且每行的微信名栏不许是破折号
        那一档 = next((b["count"] for b in (st.get("buckets") or [])
                      if b["name"] == "给了微信号"), None)
        if 那一档:
            js(page, "document.querySelector(\"#contactFilterChips "
                     "[data-ledger-bucket='给了微信号']\").click();")
            time.sleep(0.8)
            got = js(page, """
                var trs = [...document.querySelectorAll('#contactTableBody tr')];
                var 空名 = trs.filter(function(tr){ return tr.cells[6]
                    && tr.cells[6].textContent.trim() === '—'; }).length;
                return {rows: trs.filter(function(tr){return tr.cells.length > 1;}).length,
                        空名: 空名, badge: document.getElementById('contactTabCount').textContent,
                        bar: document.getElementById('contactStats').textContent.trim().slice(0,14),
                        act: document.querySelectorAll('#contactFilterChips .active').length};""")
            check("筛到「给了微信号」只剩那一档的行", got["rows"] == 那一档,
                  f"界面 {got['rows']} / 接口 {那一档}")
            check("这一档里每一行都真有微信名（不许拿破折号冒充）", got["空名"] == 0, str(got)[:120])
            check("筛选只改表格体：角标仍是台账总行数", str(got["badge"]) == str(len(all_rows)),
                  got["badge"])
            check("选中态只有一颗按钮", got["act"] == 1, str(got["act"]))
            page.get_screenshot(str(SHOTS / "contact_ledger_wechat_only.png"))
            js(page, "document.querySelector(\"#contactStatsBar "
                     "[data-ledger-bucket='']\").click();")
            time.sleep(0.8)
            back_all = js(page, "return document.querySelectorAll('#contactTableBody tr').length;")
            check("切回「全部」行数跟着回来（不是只换样式不重画）",
                  back_all == len(all_rows), f"{back_all} / {len(all_rows)}")

        # ── 认人搜索框：他手上只有微信里那一串名字 ──
        def 搜(词):
            page.run_js("var el=document.getElementById('contactSearchInput');"
                        f"el.value={json.dumps(词)};"
                        "el.dispatchEvent(new Event('input',{bubbles:true}));")
            time.sleep(0.6)
            return js(page, """
                var trs = [...document.querySelectorAll('#contactTableBody tr')]
                    .filter(function(tr){ return tr.cells.length > 1; });
                return {rows: trs.length,
                        hit: document.getElementById('ledgerSearchHit').textContent.trim(),
                        kept: document.getElementById('contactSearchInput').value,
                        badge: document.getElementById('contactTabCount').textContent,
                        texts: trs.map(function(tr){ return tr.innerText.replace(/\\s+/g,' '); }),
                        占位: [...document.querySelectorAll('#contactTableBody tr')]
                            .filter(function(tr){ return tr.cells.length === 1; })
                            .map(function(tr){ return tr.textContent.trim(); }).join('|')};""")

        认不出 = [r for r in all_rows if r.get("wechat_names")
                  and r["chat_name"] not in r["wechat_names"]]
        if 认不出:
            名 = 认不出[0]["wechat_names"][0]
            got = 搜(名)
            check(f"拿微信名「{名}」一搜就照出是哪一单", got["rows"] >= 1, str(got)[:110])
            check("搜出来的每行都真的带着那串名字（不许靠运气多召行）",
                  all(名 in t for t in got["texts"]), str(got["texts"])[:120])
            check("命中数当场写在框旁边，且和屏上行数一致",
                  got["hit"].startswith(f"命中 {got['rows']}"), got["hit"])
            check("打字不重建输入框：值还在（还在接着输第二个词）", got["kept"] == 名, got["kept"])
            check("搜索不动角标：台账总行数照旧", str(got["badge"]) == str(len(all_rows)),
                  got["badge"])
            check("这一单会话名和微信名确实不是一个字（所以只能靠搜）",
                  认不出[0]["chat_name"] not in got["texts"][0] or 名 != 认不出[0]["chat_name"],
                  f"{名} vs {认不出[0]['chat_name']}")
            糊 = 搜(名.upper() if 名 != 名.upper() else "不存在的那串名字zz")
            if 名 != 名.upper():
                check("英文名字大小写不敏感（粘进来什么样子都搜得到）", 糊["rows"] >= 1, str(糊)[:90])
            else:
                check("搜不到时不画成空表：要说清在多少行里没命中",
                      "没命中" in 糊["占位"] and "这串名字不在台账里" in 糊["占位"], 糊["占位"][:110])
            零 = 搜("zzz不可能有的名字zzz")
            check("搜不到：行数为 0 但话说清楚（不许看着像存档掉了一批）",
                  零["rows"] == 0 and "没命中" in 零["占位"] and "不是没捞到" in 零["占位"],
                  零["占位"][:130])
            check("搜不到时命中数也照实在（命中 0）", 零["hit"].startswith("命中 0"), 零["hit"])
            # 两个轴叠起来用：先点「给了微信号」这一档，再拿名字搜——只能是交集
            if 那一档:
                js(page, "document.querySelector(\"#contactFilterChips "
                         "[data-ledger-bucket='给了微信号']\").click();")
                time.sleep(0.6)
                叠 = 搜(名)
                check("档位和搜索是交集：搜出来的行都还在「给了微信号」这一档里",
                      叠["rows"] >= 1 and all(名 in t for t in 叠["texts"]), str(叠)[:110])
                check("交集时命中数的分母跟着这一档走",
                      f"/ 范围内 {那一档} 行" in 叠["hit"], 叠["hit"])
                page.get_screenshot(str(SHOTS / "contact_ledger_search.png"))
                js(page, "document.querySelector(\"#contactStatsBar "
                         "[data-ledger-bucket='']\").click();")
            清 = 搜("")
            check("清空搜索词就回到当前档的全部行（框旁边那句话也收回）",
                  清["rows"] == len(all_rows) and 清["hit"] == "", f"{清['rows']} / {清['hit']}")

        # 切数据范围：只看账号2 时不许冒出账号1 的号码
        # 只数数据范围那一组：无头/有头两颗按钮和判分复盘那组账号按钮同用 .scope-chip，
        # 取全局会把 3 颗数成 5 颗，判据就再也不是"账号数 + 1"了
        chips = js(page, "return document.querySelectorAll('#metricsScope [data-scope]').length;")
        check("数据范围切换条在", chips >= 3, f"{chips} 个 chip")
        if chips >= 3:
            UI.click_chip(page, -1)      # 最后一个具体账号
            # 切号要重扫一遍存档再重画：实测 10.4 秒出数（首屏 12.4 秒），
            # 以前只等 8 秒就判"表里没数据"——那是读早了，不是界面坏了
            等秒 = 0
            state = {"want": "", "rows": 0, "bad": 0}
            while 等秒 < 45:
                time.sleep(1.0)
                等秒 += 1
                state = js(page, """
                    var rows = [...document.querySelectorAll('#contactTableBody tr')]
                        .filter(function(tr){ return tr.cells.length > 1; });
                    // 只数单元格数够的行：表里有"暂无…"这种 colspan 的单格占位行，
                    // 直接取 cells[1] 会抛 TypeError，把"读不到数据"变成"页面报错"，
                    // 分不清是产品问题还是测试问题（实测红过一次）
                    if (!rows.length) return {want: '', rows: 0, bad: 0,
                        占位: (document.getElementById('contactTableBody').textContent || '').trim().slice(0, 40)};
                    var first = rows[0].cells[1].textContent.trim();
                    var bad = 0;
                    rows.forEach(function(tr){
                      if (tr.cells[1].textContent.trim() !== first) bad++;
                    });
                    return {want: first, rows: rows.length, bad: bad, 占位: ''};""")
                if state["want"]:
                    break
            if not state["want"]:
                # 一行都没渲染出来：这是产品/数据问题，得说人话，
                # 不能拿 int('') 崩在这里冒充"测试跑过了"
                check("切账号后台账表里能读到数据", False,
                      f"等了 {等秒} 秒仍没出数，屏上那行写的是：{state.get('占位', '')!r}")
            else:
                expect = api(f"?account={int(state['want']) - 1}")["total"]
                check("切账号后表跟着换", state["rows"] == expect,
                      f"范围 账号{state['want']} 渲染 {state['rows']} / 接口 {expect}")
                check("表里没有别的号的数据", state["bad"] == 0, f"串号 {state['bad']} 行")
            page.get_screenshot(str(SHOTS / "contact_ledger_one_account.png"))

        # 回到打招呼记录：tab 切换不能把别的表弄坏
        UI.click_until_alive(page, lambda p: tab_button(p, "打招呼记录"))
        time.sleep(1.2)
        back = js(page, """
            var c = document.getElementById('greetTabContent');
            var bar = document.getElementById('greetFilterBar');
            return {on: c.classList.contains('active'),
                    contactOff: !document.getElementById('contactTabContent').classList.contains('active'),
                    bar: getComputedStyle(bar).display !== 'none',
                    clear: getComputedStyle(document.getElementById('btnClearRecord')).display};""")
        check("切回打招呼记录，筛选栏也跟着回来",
              back["on"] and back["contactOff"] and back["bar"], str(back))
        check("切回记录页「清空」又出现", back["clear"] != "none", back["clear"])
    finally:
        if browser:
            UI.close_browser(browser)
        if proc:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print(f"\n共 {len(results)} 项，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
