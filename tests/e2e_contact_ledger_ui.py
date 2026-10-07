"""「联系与简历」台账的真机点击测试（task #114）。

只用临时面板（默认 5059），绝不碰线上 :5000：
线上面板跑的是旧代码，在它上面测出来的"通过"是假的，
而且实测脚本会抢 5000、真点启停，会把投递打断。

跑法：python tests/e2e_contact_ledger_ui.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import e2e_account_scope_ui as E  # noqa: E402
import e2e_greet_records_ui as UI  # noqa: E402

PORT = 5059
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
    assert ":5000" not in URL and PORT != 5000, "实测禁止打线上面板"
    tmp = Path(tempfile.mkdtemp(prefix="boss_ledger_"))
    proc = None
    browser = None
    try:
        E.seed(tmp)
        E.start_server(tmp, PORT)
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
        time.sleep(3.5)

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
        check("每行 9 列（时间/号/HR·公司/岗位/薪资/方式/号码/简历/依据）", cells == 9, f"{cells} 列")

        row_check = js(page, """
            var tr = document.querySelector('#contactTableBody tr');
            if (!tr) return null;
            var t = function(i){ return tr.cells[i].textContent.trim(); };
            return {time:t(0), acc:t(1), who:t(2), job:t(3), pay:t(4), kind:t(5), quote:t(8)};""")
        check("首行有时间、公司、岗位", bool(row_check and row_check["time"] and row_check["who"]
                                and row_check["job"]), str(row_check)[:150])
        check("岗位不带「查看职位」这种侧栏尾巴",
              all("查看职位" not in (r["job_title"] or "") for r in all_rows))
        check("联系方式一列有说法",
              row_check["kind"] in ("卡片已同意", "卡片已拒绝", "给了号码", "发起交换请求",
                                    "说要给没留号"),
              row_check["kind"])

        nums = js(page, "return document.querySelectorAll('#contactTableBody .ledger-num').length;")
        api_nums = sum(len(r["wechats"]) + len(r["phones"]) for r in all_rows)
        check("号码渲染成可点的复制块", nums == api_nums, f"界面 {nums} / 数据 {api_nums}")

        hidden = js(page, "return getComputedStyle(document.getElementById('btnClearRecord')).display;")
        check("台账这一页藏掉了「清空」（现算视图没有可清的东西）", hidden == "none", hidden)

        quote_ok = js(page, """
            var els = document.querySelectorAll('#contactTableBody .ledger-quote');
            for (var i=0;i<els.length;i++){ if (els[i].textContent.length > 200) return 'long'; }
            return 'ok';""")
        check("依据原话不会长到撑破行", quote_ok == "ok")

        SHOTS.mkdir(parents=True, exist_ok=True)
        page.get_screenshot(str(SHOTS / "contact_ledger_all.png"))

        # 切数据范围：只看账号2 时不许冒出账号1 的号码
        chips = js(page, "return document.querySelectorAll('.scope-chip').length;")
        check("数据范围切换条在", chips >= 3, f"{chips} 个 chip")
        if chips >= 3:
            UI.click_chip(page, -1)      # 最后一个具体账号
            time.sleep(1.0)
            for _ in range(25):
                n = js(page, "return document.querySelectorAll('#contactTableBody tr').length;")
                if n and n != shown:
                    break
                time.sleep(0.8)
            # dataScope 是 let 声明的，挂在脚本作用域不是 window 上，
            # 所以从表里读"号"那一列，别去读 JS 变量
            state = js(page, """
                var rows = document.querySelectorAll('#contactTableBody tr');
                if (!rows.length) return {want: '', rows: 0, bad: 0};
                var first = rows[0].cells[1].textContent.trim();
                var bad = 0;
                rows.forEach(function(tr){
                  if (tr.cells[1].textContent.trim() !== first) bad++;
                });
                return {want: first, rows: rows.length, bad: bad};""")
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
