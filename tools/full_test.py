"""
全量真机实测 — 使用Playwright引擎全面测试boss_bot前端功能
覆盖：UI基础 + 多账号 + AI配置 + 回复记录 + 日志 + 配置面板 + 数据管理
"""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


async def run_full_test():
    from playwright.async_api import async_playwright

    results = {}
    all_checks = []

    def check(name, ok, detail=""):
        all_checks.append((name, ok, detail))
        return ok

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=False,
            args=["--disable-extensions"],
        )
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        print("导航到 http://127.0.0.1:5000/ ...")
        await page.goto("http://127.0.0.1:5000/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        # ============================================================
        # A组: UI基础 (6项 - 已验证通过)
        # ============================================================
        print("\n=== A组: UI基础 ===")

        # A1: Toggle开关可见性
        print("\n【A1: Toggle开关可见性】")
        toggles = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('span.toggle-switch')).map(t => {
                const r = t.getBoundingClientRect();
                const s = window.getComputedStyle(t);
                return {id: t.id||'no-id', isOn: t.classList.contains('on'),
                        visible: r.width>0&&r.height>0, bg: s.backgroundColor, opacity: parseFloat(s.opacity)};
            });
        }""")
        all_vis = all(t['visible'] for t in toggles)
        off_vis = all(t['visible'] for t in toggles if not t['isOn'])
        check("A1.Toggle开关全部可见", all_vis, f"{len(toggles)}个开关")
        check("A1.OFF状态开关可见", off_vis, f"{len([t for t in toggles if not t['isOn']])}个OFF")
        print(f"  {len(toggles)}个开关, 全部可见={'✅' if all_vis else '❌'}, OFF可见={'✅' if off_vis else '❌'}")

        # A2: 筛选栏布局
        print("\n【A2: 筛选栏布局】")
        layout = await page.evaluate("""() => {
            const tabs = document.querySelectorAll('.record-tab');
            const bar = document.getElementById('greetFilterBar');
            if (!tabs.length || !bar) return {sameRow: false};
            return {sameRow: Math.abs(tabs[0].getBoundingClientRect().top - bar.getBoundingClientRect().top) < 30,
                    sameParent: tabs[0].parentElement === bar.parentElement};
        }""")
        check("A2.筛选栏与tab同一行", layout['sameRow'], f"sameParent={layout.get('sameParent')}")
        print(f"  同一行: {'✅' if layout['sameRow'] else '❌'}")

        # A3: 筛选功能
        print("\n【A3: 筛选功能】")
        before = await page.evaluate("() => document.querySelectorAll('#greetTableBody tr').length")
        await page.fill('#greetFilterJob', '数据分析师')
        await page.wait_for_timeout(500)
        await page.locator('button:has-text("筛选")').first.click()
        await page.wait_for_timeout(2000)
        after = await page.evaluate("() => document.querySelectorAll('#greetTableBody tr').length")
        await page.locator('button:has-text("重置")').first.click()
        await page.wait_for_timeout(2000)
        reset = await page.evaluate("() => document.querySelectorAll('#greetTableBody tr').length")
        check("A3.筛选生效", before > after, f"{before}→{after}")
        check("A3.重置恢复", reset >= before * 0.9, f"重置后={reset}")
        print(f"  筛选: {before}→{after}→重置{reset} {'✅' if before > after and reset >= before*0.9 else '❌'}")

        # A4: Tab切换
        print("\n【A4: Tab切换】")
        await page.evaluate("switchRecordTab('reply')")
        await page.wait_for_timeout(2000)
        reply_info = await page.evaluate("""() => {
            const rb = document.getElementById('replyFilterBar');
            const gb = document.getElementById('greetFilterBar');
            return {replyDisplay: rb ? getComputedStyle(rb).display : 'x',
                    greetDisplay: gb ? getComputedStyle(gb).display : 'x',
                    hasSource: rb ? rb.innerText.includes('来源') : false};
        }""")
        check("A4.回复tab筛选栏显示", reply_info['replyDisplay'] == 'flex', f"display={reply_info['replyDisplay']}")
        check("A4.含来源下拉", reply_info['hasSource'])
        await page.evaluate("switchRecordTab('greet')")
        await page.wait_for_timeout(2000)
        print(f"  Tab切换: reply={reply_info['replyDisplay']}, 来源={'✅' if reply_info['hasSource'] else '❌'}")

        # A5: 数据显示
        print("\n【A5: 数据显示】")
        data_info = await page.evaluate("""() => {
            const tbody = document.getElementById('greetTableBody');
            if (!tbody) return {garbled: true, humanized: false, rows: 0};
            const text = tbody.innerText;
            let garbled = false;
            for (let i = 0; i < text.length; i++) {
                if (text.charCodeAt(i) >= 0xE000 && text.charCodeAt(i) <= 0xF8FF) {garbled = true; break;}
            }
            const patterns = ['刚刚','分钟前','小时前','昨天','前天','天前'];
            return {garbled, humanized: patterns.some(p => text.includes(p)),
                    rows: document.querySelectorAll('#greetTableBody tr').length};
        }""")
        check("A5.无乱码", not data_info['garbled'])
        check("A5.时间人性化", data_info['humanized'])
        print(f"  乱码={'❌' if data_info['garbled'] else '✅无'}, 人性化={'✅' if data_info['humanized'] else '❌'}, {data_info['rows']}行")

        # A6: Toggle点击
        print("\n【A6: Toggle点击】")
        init = await page.evaluate("() => document.querySelector('#aiEnabled')?.classList.contains('on')")
        await page.click('#aiEnabled')
        await page.wait_for_timeout(1000)
        clicked = await page.evaluate("() => document.querySelector('#aiEnabled')?.classList.contains('on')")
        await page.click('#aiEnabled')
        await page.wait_for_timeout(1000)
        final = await page.evaluate("() => document.querySelector('#aiEnabled')?.classList.contains('on')")
        check("A6.Toggle切换有效", init != clicked and final == init, f"{init}→{clicked}→{final}")
        print(f"  Toggle: {init}→{clicked}→{final} {'✅' if init != clicked and final == init else '❌'}")

        # ============================================================
        # B组: 多账号管理
        # ============================================================
        print("\n=== B组: 多账号管理 ===")

        # B1: 账号列表
        print("\n【B1: 账号列表】")
        accounts = await page.evaluate("""() => {
            const items = document.querySelectorAll('.account-item, [class*="account"]');
            return {count: items.length, texts: Array.from(items).slice(0,5).map(e => e.innerText.substring(0,50))};
        }""")
        check("B1.账号列表存在", accounts['count'] > 0, f"{accounts['count']}个账号")
        print(f"  账号数: {accounts['count']}")

        # B2: 账号启用开关
        acc_toggle = await page.evaluate("""() => {
            const t = document.querySelector('#accEnabled');
            if (!t) return {exists: false};
            const r = t.getBoundingClientRect();
            return {exists: true, isOn: t.classList.contains('on'), visible: r.width > 0};
        }""")
        check("B2.账号启用开关存在", acc_toggle['exists'])
        check("B2.账号启用开关可见", acc_toggle.get('visible', False))
        print(f"  accEnabled: exists={'✅' if acc_toggle['exists'] else '❌'}, visible={'✅' if acc_toggle.get('visible') else '❌'}")

        # B3: Cookie文件存在
        cookie_files = await page.evaluate("""() => {
            return document.body.innerText.includes('Cookie') || document.body.innerText.includes('cookie');
        }""")
        check("B3.Cookie相关UI存在", cookie_files)
        print(f"  Cookie UI: {'✅' if cookie_files else '❌'}")

        # ============================================================
        # C组: AI配置
        # ============================================================
        print("\n=== C组: AI配置 ===")

        # C1: AI开关
        print("\n【C1: AI开关】")
        ai_info = await page.evaluate("""() => {
            const t = document.querySelector('#aiEnabled');
            return {exists: !!t, isOn: t?.classList.contains('on')};
        }""")
        check("C1.AI开关存在", ai_info['exists'])
        check("C1.AI开关开启", ai_info['isOn'])
        print(f"  AI开关: exists={'✅' if ai_info['exists'] else '❌'}, ON={'✅' if ai_info['isOn'] else '❌'}")

        # C2: AI接口列表
        print("\n【C2: AI接口列表】")
        ai_providers = await page.evaluate("""() => {
            const text = document.body.innerText;
            const matches = text.match(/接口\[\d+\]/g);
            return {count: matches ? matches.length : 0, samples: matches ? matches.slice(0,5) : []};
        }""")
        check("C2.AI接口已配置", ai_providers['count'] > 0, f"{ai_providers['count']}个接口")
        print(f"  AI接口数: {ai_providers['count']}")

        # C3: AI阈值
        ai_threshold = await page.evaluate("""() => {
            const text = document.body.innerText;
            const match = text.match(/阈值[:\s]*(\d+)/);
            return match ? parseInt(match[1]) : null;
        }""")
        check("C3.AI阈值显示", ai_threshold is not None, f"阈值={ai_threshold}")
        print(f"  AI阈值: {ai_threshold}")

        # C4: AI智能解析开关提示
        ai_hint = await page.evaluate("""() => {
            return document.body.innerText.includes('自动回复AI始终开启') ||
                   document.body.innerText.includes('仅控制打招呼');
        }""")
        check("C4.AI开关提示存在", ai_hint)
        print(f"  AI开关提示: {'✅' if ai_hint else '❌'}")

        # ============================================================
        # D组: 回复记录
        # ============================================================
        print("\n=== D组: 回复记录 ===")

        # D1: 回复记录tab
        print("\n【D1: 回复记录tab】")
        reply_tab_info = await page.evaluate("""() => {
            const countEl = document.getElementById('replyTabCount');
            const btn = Array.from(document.querySelectorAll('.record-tab')).find(b => b.textContent.includes('回复记录'));
            return {exists: !!btn, count: countEl ? parseInt(countEl.textContent) || 0 : 0};
        }""")
        check("D1.回复记录tab存在", reply_tab_info['exists'])
        check("D1.回复记录数>0", reply_tab_info.get('count', 0) > 0, f"{reply_tab_info.get('count',0)}条")
        print(f"  回复记录: {reply_tab_info.get('count',0)}条")

        # D2: 回复记录内容
        print("\n【D2: 回复记录内容】")
        await page.evaluate("switchRecordTab('reply')")
        await page.wait_for_timeout(2000)
        reply_content = await page.evaluate("""() => {
            const wrap = document.getElementById('bossChatWrap');
            if (!wrap) return {exists: false};
            return {exists: true, hasSidebar: !!wrap.querySelector('.boss-chat-sidebar'),
                    hasDetail: !!wrap.querySelector('.boss-chat-main') || !!wrap.querySelector('#bossChatMain')};
        }""")
        check("D2.聊天界面存在", reply_content['exists'])
        check("D2.聊天侧边栏存在", reply_content.get('hasSidebar', False))
        check("D2.聊天详情区存在", reply_content.get('hasDetail', False))
        print(f"  聊天界面: exists={'✅' if reply_content['exists'] else '❌'}, sidebar={'✅' if reply_content.get('hasSidebar') else '❌'}, detail={'✅' if reply_content.get('hasDetail') else '❌'}")

        # D3: 消息来源标记
        source_mark = await page.evaluate("""() => {
            const text = document.body.innerText;
            return {hasAI: text.includes('机器自动') || text.includes('AI'),
                    hasRule: text.includes('规则') || text.includes('意图'),
                    hasManual: text.includes('人工')};
        }""")
        check("D3.消息来源标记存在", source_mark['hasAI'] or source_mark['hasRule'])
        print(f"  来源标记: AI={'✅' if source_mark['hasAI'] else '❌'}, 规则={'✅' if source_mark['hasRule'] else '❌'}, 人工={'✅' if source_mark['hasManual'] else '❌'}")

        await page.evaluate("switchRecordTab('greet')")
        await page.wait_for_timeout(2000)

        # ============================================================
        # E组: 日志显示
        # ============================================================
        print("\n=== E组: 日志显示 ===")

        # E1: 日志面板
        print("\n【E1: 日志面板】")
        log_panel = await page.evaluate("""() => {
            const logs = document.querySelectorAll('[class*="log"], #logPanel, #logBox, .log-content');
            return {count: logs.length, hasContent: logs.length > 0};
        }""")
        check("E1.日志面板存在", log_panel['count'] > 0, f"{log_panel['count']}个日志元素")
        print(f"  日志元素: {log_panel['count']}个")

        # E2: 日志内容
        log_content = await page.evaluate("""() => {
            const text = document.body.innerText;
            return {hasInfo: text.includes('INFO') || text.includes('info'),
                    hasSuccess: text.includes('SUCCESS') || text.includes('成功'),
                    hasError: text.includes('ERROR') || text.includes('错误')};
        }""")
        check("E2.日志有内容", log_content['hasInfo'] or log_content['hasSuccess'])
        print(f"  日志内容: INFO={'✅' if log_content['hasInfo'] else '❌'}, SUCCESS={'✅' if log_content['hasSuccess'] else '❌'}")

        # ============================================================
        # F组: 配置面板
        # ============================================================
        print("\n=== F组: 配置面板 ===")

        # F1: 投递限制
        print("\n【F1: 投递限制】")
        rate_limit = await page.evaluate("""() => {
            const text = document.body.innerText;
            const m1 = text.match(/每日上限\s*(\d+)/);
            const m2 = text.match(/(\d+)\s*份/);
            const m3 = text.match(/每天.*?(\d+)/);
            const m4 = text.match(/max_per_day[:\s]*(\d+)/);
            const m = m1 || m2 || m3 || m4;
            return m ? parseInt(m[1]) : null;
        }""")
        check("F1.投递限制显示", rate_limit is not None and rate_limit > 0, f"每天{rate_limit}份")
        print(f"  投递限制: {rate_limit}")

        # F2: 高级设置开关
        adv_toggles = await page.evaluate("""() => {
            const ids = ['advHeadless', 'advRateLimit', 'advClearCookies', 'advTestMode'];
            return ids.map(id => {
                const t = document.getElementById(id);
                return {id, exists: !!t, visible: t ? t.getBoundingClientRect().width > 0 : false};
            });
        }""")
        for at in adv_toggles:
            check(f"F2.{at['id']}存在可见", at['exists'] and at['visible'])
        print(f"  高级设置: {sum(1 for at in adv_toggles if at['exists'] and at['visible'])}/{len(adv_toggles)}个可见")

        # F3: 个人画像
        profile_info = await page.evaluate("""() => {
            const text = document.body.innerText;
            return {hasName: text.includes('求职者') || text.includes('姓名'),
                    hasEdu: text.includes('本科') || text.includes('学历') || text.includes('双一流'),
                    hasSkill: text.includes('SQL') || text.includes('Excel') || text.includes('技能')};
        }""")
        check("F3.个人画像-姓名", profile_info['hasName'])
        check("F3.个人画像-学历", profile_info['hasEdu'])
        check("F3.个人画像-技能", profile_info['hasSkill'])
        print(f"  画像: 姓名={'✅' if profile_info['hasName'] else '❌'}, 学历={'✅' if profile_info['hasEdu'] else '❌'}, 技能={'✅' if profile_info['hasSkill'] else '❌'}")

        # ============================================================
        # G组: 数据管理
        # ============================================================
        print("\n=== G组: 数据管理 ===")

        # G1: 下载按钮
        print("\n【G1: 下载按钮】")
        download_btns = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button')).filter(b =>
                b.textContent.includes('下载') || b.textContent.includes('导出'));
            return btns.map(b => b.textContent.trim().substring(0, 20));
        }""")
        check("G1.下载按钮存在", len(download_btns) > 0, str(download_btns))
        print(f"  下载按钮: {download_btns}")

        # G2: 历史数据按钮
        archive_btn = await page.evaluate("""() => {
            return !!Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('历史') || b.textContent.includes('归档'));
        }""")
        check("G2.历史数据按钮存在", archive_btn)
        print(f"  历史数据按钮: {'✅' if archive_btn else '❌'}")

        # G3: 清空按钮
        clear_btn = await page.evaluate("""() => {
            return !!Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('清空'));
        }""")
        check("G3.清空按钮存在", clear_btn)
        print(f"  清空按钮: {'✅' if clear_btn else '❌'}")

        # ============================================================
        # H组: 启动/停止控制
        # ============================================================
        print("\n=== H组: 启动/停止控制 ===")

        # H1: 启动按钮
        print("\n【H1: 启动按钮】")
        start_btn = await page.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll('button')).find(b =>
                b.textContent.includes('启动') && !b.textContent.includes('自动'));
            if (!btn) return {exists: false};
            const r = btn.getBoundingClientRect();
            return {exists: true, visible: r.width > 0, text: btn.textContent.trim().substring(0, 20)};
        }""")
        check("H1.启动按钮存在可见", start_btn['exists'] and start_btn.get('visible', False), start_btn.get('text', ''))
        print(f"  启动按钮: {'✅' if start_btn['exists'] and start_btn.get('visible') else '❌'} ({start_btn.get('text','')})")

        # H2: 状态显示
        status_info = await page.evaluate("""() => {
            const text = document.body.innerText;
            return {hasRunning: text.includes('运行') || text.includes('已停止') || text.includes('空闲'),
                    hasStats: text.includes('已投递') || text.includes('已回复') || text.includes('打招呼')};
        }""")
        check("H2.运行状态显示", status_info['hasRunning'])
        check("H2.统计数据显示", status_info['hasStats'])
        print(f"  状态: 运行={'✅' if status_info['hasRunning'] else '❌'}, 统计={'✅' if status_info['hasStats'] else '❌'}")

        # ============================================================
        # I组: 毛玻璃效果
        # ============================================================
        print("\n=== I组: 毛玻璃效果 ===")

        glass_count = await page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            let count = 0;
            all.forEach(el => {
                const s = getComputedStyle(el);
                if (s.backdropFilter && s.backdropFilter !== 'none' && s.backdropFilter.includes('blur')) count++;
            });
            return count;
        }""")
        check("I1.毛玻璃效果存在", glass_count > 0, f"{glass_count}个元素")
        print(f"  毛玻璃元素: {glass_count}个")

        # ============================================================
        # J组: API接口验证
        # ============================================================
        print("\n=== J组: API接口验证 ===")

        # J1: /api/status
        status_resp = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/status');
                const d = await r.json();
                return {ok: r.ok, hasFields: !!(d.running !== undefined || d.status !== undefined)};
            } catch(e) { return {ok: false, error: e.message}; }
        }""")
        check("J1./api/status接口", status_resp['ok'])
        print(f"  /api/status: {'✅' if status_resp['ok'] else '❌'}")

        # J2: /api/config
        config_resp = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/config');
                return {ok: r.ok};
            } catch(e) { return {ok: false}; }
        }""")
        check("J2./api/config接口", config_resp['ok'])
        print(f"  /api/config: {'✅' if config_resp['ok'] else '❌'}")

        # J3: /api/logs
        logs_resp = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/logs');
                return {ok: r.ok};
            } catch(e) { return {ok: false}; }
        }""")
        check("J3./api/logs接口", logs_resp['ok'])
        print(f"  /api/logs: {'✅' if logs_resp['ok'] else '❌'}")

        # J4: /api/greet_records
        greet_resp = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/greet_records');
                const d = await r.json();
                return {ok: r.ok, count: Array.isArray(d) ? d.length : (d.records ? d.records.length : 0)};
            } catch(e) { return {ok: false, count: 0}; }
        }""")
        check("J4./api/greet_records接口", greet_resp['ok'])
        check("J4.打招呼记录数>0", greet_resp.get('count', 0) > 0, f"{greet_resp.get('count',0)}条")
        print(f"  /api/greet_records: {'✅' if greet_resp['ok'] else '❌'}, {greet_resp.get('count',0)}条")

        # J5: /api/reply_records
        reply_resp = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/reply_records');
                const d = await r.json();
                return {ok: r.ok, count: Array.isArray(d) ? d.length : (d.records ? d.records.length : 0)};
            } catch(e) { return {ok: false, count: 0}; }
        }""")
        check("J5./api/reply_records接口", reply_resp['ok'])
        check("J5.回复记录数>0", reply_resp.get('count', 0) > 0, f"{reply_resp.get('count',0)}条")
        print(f"  /api/reply_records: {'✅' if reply_resp['ok'] else '❌'}, {reply_resp.get('count',0)}条")

        # J6: /api/user_profile
        profile_resp = await page.evaluate("""async () => {
            try {
                const r = await fetch('/api/user_profile');
                return {ok: r.ok};
            } catch(e) { return {ok: false}; }
        }""")
        check("J6./api/user_profile接口", profile_resp['ok'])
        print(f"  /api/user_profile: {'✅' if profile_resp['ok'] else '❌'}")

        await browser.close()

    # ============================================================
    # 汇总
    # ============================================================
    print("\n" + "=" * 70)
    print("全量真机实测汇总 (Playwright引擎)")
    print("=" * 70)

    passed = 0
    failed = 0
    failed_items = []
    for name, ok, detail in all_checks:
        if ok:
            passed += 1
        else:
            failed += 1
            failed_items.append(f"  ❌ {name}: {detail}")

    print(f"\n通过: {passed} | 失败: {failed} | 总计: {len(all_checks)}")
    print(f"通过率: {passed/len(all_checks)*100:.1f}%")

    if failed_items:
        print("\n失败项:")
        for item in failed_items:
            print(item)

    print(f"\n总体结果: {'✅ 全部通过' if failed == 0 else '❌ 有失败项'}")
    print("=" * 70)

    # 保存结果
    results_json = {name: {'pass': ok, 'detail': detail} for name, ok, detail in all_checks}
    with open(os.path.join(os.path.dirname(__file__), 'full_test_result.json'), 'w', encoding='utf-8') as f:
        json.dump(results_json, f, ensure_ascii=False, indent=2)
    print(f"结果已保存到 tools/full_test_result.json")

    return failed == 0


if __name__ == "__main__":
    asyncio.run(run_full_test())