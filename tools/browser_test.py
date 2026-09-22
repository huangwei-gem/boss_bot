"""
真机实测 — 使用Playwright(browser-use底层引擎)直接程序化测试
"""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


async def run_test():
    from playwright.async_api import async_playwright

    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=False,
            args=["--disable-extensions"],
        )
        context = await browser.new_context()
        page = await context.new_page()

        print("导航到 http://127.0.0.1:5000/ ...")
        await page.goto("http://127.0.0.1:5000/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        # ========== 测试1: Toggle开关可见性 ==========
        print("\n【测试1: Toggle开关可见性】")
        toggles = await page.evaluate("""() => {
            const toggles = Array.from(document.querySelectorAll('span.toggle-switch'));
            return toggles.map(t => {
                const rect = t.getBoundingClientRect();
                const style = window.getComputedStyle(t);
                return {
                    id: t.id || 'no-id',
                    isOn: t.classList.contains('on'),
                    visible: rect.width > 0 && rect.height > 0,
                    bgColor: style.backgroundColor,
                    opacity: parseFloat(style.opacity)
                };
            });
        }""")
        print(f"  开关总数: {len(toggles)}")
        for t in toggles:
            status = "ON" if t['isOn'] else "OFF"
            vis = "可见" if t['visible'] else "不可见"
            print(f"  - {t['id']}: {status} | {vis} | bg={t['bgColor']} | opacity={t['opacity']}")
        all_visible = all(t['visible'] for t in toggles)
        off_visible = all(t['visible'] for t in toggles if not t['isOn'])
        off_count = len([t for t in toggles if not t['isOn']])
        results['test1'] = {'total': len(toggles), 'all_visible': all_visible, 'off_visible': off_visible, 'off_count': off_count}
        print(f"  全部可见: {'✅' if all_visible else '❌'} | OFF状态({off_count}个)可见: {'✅' if off_visible else '❌'}")
        print(f"  结果: {'✅ 通过' if all_visible and off_visible else '❌ 失败'}")

        # ========== 测试2: 筛选栏布局 ==========
        print("\n【测试2: 筛选栏布局】")
        layout = await page.evaluate("""() => {
            const tabBtns = Array.from(document.querySelectorAll('.record-tab'));
            const filterBar = document.getElementById('greetFilterBar');
            const filterBtns = Array.from(document.querySelectorAll('button')).filter(b =>
                b.textContent.trim() === '筛选' || b.textContent.trim() === '重置'
            );
            if (tabBtns.length === 0 || !filterBar) return {error: 'elements not found'};

            const tabY = Math.round(tabBtns[0].getBoundingClientRect().top);
            const barY = Math.round(filterBar.getBoundingClientRect().top);
            const filterY = filterBtns.length > 0 ? Math.round(filterBtns[0].getBoundingClientRect().top) : -1;

            // 检查是否在同一个父容器
            const tabParent = tabBtns[0].parentElement;
            const barParent = filterBar.parentElement;
            const sameParent = tabParent === barParent;

            return {
                tabY: tabY,
                barY: barY,
                filterY: filterY,
                sameParent: sameParent,
                sameRow: Math.abs(tabY - barY) < 30,
                tabParentClass: tabParent ? tabParent.className : '',
            };
        }""")
        if 'error' in layout:
            print(f"  ❌ {layout['error']}")
            results['test2'] = {'same_row': False}
        else:
            print(f"  Tab Y={layout['tabY']}, 筛选栏 Y={layout['barY']}, 筛选按钮 Y={layout['filterY']}")
            print(f"  同一父容器: {'是' if layout['sameParent'] else '否'} ({layout.get('tabParentClass','')})")
            print(f"  同一行: {'是' if layout['sameRow'] else '否'}")
            results['test2'] = {'same_row': layout['sameRow'], 'same_parent': layout['sameParent']}
        print(f"  结果: {'✅ 通过' if results['test2']['same_row'] else '❌ 失败'}")

        # ========== 测试3: 筛选功能 ==========
        print("\n【测试3: 筛选功能】")
        # 用表格行数计数（不依赖count文本）
        before_count = await page.evaluate("""() => {
            return document.querySelectorAll('#greetTableBody tr').length;
        }""")
        print(f"  筛选前行数: {before_count}")

        await page.fill('#greetFilterJob', '数据分析师')
        await page.wait_for_timeout(500)
        await page.locator('button:has-text("筛选")').first.click()
        await page.wait_for_timeout(2000)

        after_count = await page.evaluate("""() => {
            return document.querySelectorAll('#greetTableBody tr').length;
        }""")
        print(f"  筛选后行数: {after_count}")

        await page.locator('button:has-text("重置")').first.click()
        await page.wait_for_timeout(2000)

        reset_count = await page.evaluate("""() => {
            return document.querySelectorAll('#greetTableBody tr').length;
        }""")
        print(f"  重置后行数: {reset_count}")

        filter_works = before_count > after_count and reset_count >= before_count * 0.9
        results['test3'] = {'before': before_count, 'after': after_count, 'reset': reset_count, 'works': filter_works}
        print(f"  筛选生效: {'✅' if before_count > after_count else '❌'} ({before_count}→{after_count})")
        print(f"  重置恢复: {'✅' if reset_count >= before_count * 0.9 else '❌'} ({reset_count})")
        print(f"  结果: {'✅ 通过' if filter_works else '❌ 失败'}")

        # ========== 测试4: Tab切换 ==========
        print("\n【测试4: Tab切换】")
        # 直接调用switchRecordTab函数
        await page.evaluate("switchRecordTab('reply')")
        await page.wait_for_timeout(2000)

        reply_info = await page.evaluate("""() => {
            const replyBar = document.getElementById('replyFilterBar');
            const greetBar = document.getElementById('greetFilterBar');
            const replyContent = document.getElementById('replyTabContent');
            return {
                replyBarDisplay: replyBar ? getComputedStyle(replyBar).display : 'not found',
                greetBarDisplay: greetBar ? getComputedStyle(greetBar).display : 'not found',
                replyContentActive: replyContent ? replyContent.classList.contains('active') : false,
                hasSource: replyBar ? replyBar.innerText.includes('来源') : false,
            };
        }""")
        print(f"  回复筛选栏display: {reply_info['replyBarDisplay']}")
        print(f"  打招呼筛选栏display: {reply_info['greetBarDisplay']}")
        print(f"  回复内容active: {reply_info['replyContentActive']}")
        print(f"  含'来源'文本: {reply_info['hasSource']}")

        # 切回
        await page.evaluate("switchRecordTab('greet')")
        await page.wait_for_timeout(2000)

        tab_switch_ok = reply_info['replyBarDisplay'] == 'flex' and reply_info['hasSource']
        results['test4'] = {'reply_filter_visible': tab_switch_ok}
        print(f"  切回打招呼记录: 成功")
        print(f"  结果: {'✅ 通过' if tab_switch_ok else '❌ 失败'}")

        # ========== 测试5: 数据显示 ==========
        print("\n【测试5: 数据显示】")
        rows_data = await page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('#greetTableBody tr')).slice(0, 5);
            return rows.map(r => {
                const cells = Array.from(r.querySelectorAll('td'));
                return cells.map(c => c.innerText.trim());
            });
        }""")
        print(f"  前{len(rows_data)}行数据:")
        for i, row in enumerate(rows_data):
            short_row = [str(c)[:25] for c in row[:6]]
            print(f"    行{i+1}: {short_row}")

        has_garbled = await page.evaluate("""() => {
            const tbody = document.getElementById('greetTableBody');
            if (!tbody) return false;
            const text = tbody.innerText;
            for (let i = 0; i < text.length; i++) {
                const code = text.charCodeAt(i);
                if (code >= 0xE000 && code <= 0xF8FF) return true;
            }
            return false;
        }""")
        print(f"  有乱码: {'是' if has_garbled else '否'}")

        time_humanized = await page.evaluate("""() => {
            const tbody = document.getElementById('greetTableBody');
            if (!tbody) return false;
            const text = tbody.innerText;
            const patterns = ['刚刚', '分钟前', '小时前', '昨天', '前天', '天前'];
            return patterns.some(p => text.includes(p));
        }""")
        print(f"  时间人性化: {'是' if time_humanized else '否'}")

        results['test5'] = {'has_garbled': has_garbled, 'time_humanized': time_humanized}
        print(f"  结果: {'✅ 通过' if not has_garbled and time_humanized else '❌ 失败'}")

        # ========== 测试6: Toggle点击 ==========
        print("\n【测试6: Toggle点击】")
        initial_state = await page.evaluate("""() => {
            const t = document.querySelector('#aiEnabled');
            return t ? t.classList.contains('on') : null;
        }""")
        print(f"  AI开关初始: {'ON' if initial_state else 'OFF'}")

        await page.click('#aiEnabled')
        await page.wait_for_timeout(1000)
        after_click = await page.evaluate("""() => {
            const t = document.querySelector('#aiEnabled');
            return t ? t.classList.contains('on') : null;
        }""")
        print(f"  点击后: {'ON' if after_click else 'OFF'}")

        await page.click('#aiEnabled')
        await page.wait_for_timeout(1000)
        final_state = await page.evaluate("""() => {
            const t = document.querySelector('#aiEnabled');
            return t ? t.classList.contains('on') : null;
        }""")
        print(f"  切回后: {'ON' if final_state else 'OFF'}")

        toggle_works = initial_state != after_click and final_state == initial_state
        results['test6'] = {'initial': initial_state, 'after_click': after_click, 'final': final_state, 'works': toggle_works}
        print(f"  结果: {'✅ 通过' if toggle_works else '❌ 失败'}")

        await browser.close()

    # ========== 汇总 ==========
    print("\n" + "=" * 60)
    print("真机实测汇总 (Playwright引擎 - browser-use底层)")
    print("=" * 60)
    test_results = {
        'test1': results.get('test1', {}).get('all_visible', False) and results.get('test1', {}).get('off_visible', False),
        'test2': results.get('test2', {}).get('same_row', False),
        'test3': results.get('test3', {}).get('works', False),
        'test4': results.get('test4', {}).get('reply_filter_visible', False),
        'test5': not results.get('test5', {}).get('has_garbled', True) and results.get('test5', {}).get('time_humanized', False),
        'test6': results.get('test6', {}).get('works', False),
    }
    test_names = {
        'test1': 'Toggle开关可见性',
        'test2': '筛选栏布局',
        'test3': '筛选功能',
        'test4': 'Tab切换',
        'test5': '数据显示',
        'test6': 'Toggle点击',
    }
    all_pass = True
    for key, name in test_names.items():
        ok = test_results[key]
        if not ok:
            all_pass = False
        print(f"  {name}: {'✅ 通过' if ok else '❌ 失败'}")

    print(f"\n总体结果: {'✅ 全部6项通过' if all_pass else '❌ 有失败项'}")
    print("=" * 60)

    with open(os.path.join(os.path.dirname(__file__), 'browser_test_result.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"结果已保存到 tools/browser_test_result.json")

    return all_pass


if __name__ == "__main__":
    asyncio.run(run_test())
