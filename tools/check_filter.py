import asyncio
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=False,
            args=["--disable-extensions"],
        )
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.goto("http://127.0.0.1:5000/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        # 截图筛选栏区域
        bar_info = await page.evaluate("""() => {
            const bar = document.getElementById('greetFilterBar');
            if (!bar) return null;
            const r = bar.getBoundingClientRect();
            const children = Array.from(bar.children).map(c => {
                const cr = c.getBoundingClientRect();
                const cs = getComputedStyle(c);
                return {
                    tag: c.tagName,
                    text: c.innerText || c.value || '',
                    x: Math.round(cr.x),
                    y: Math.round(cr.y),
                    w: Math.round(cr.width),
                    h: Math.round(cr.height),
                    whiteSpace: cs.whiteSpace,
                    flexShrink: cs.flexShrink,
                };
            });
            return {
                barX: Math.round(r.x), barY: Math.round(r.y),
                barW: Math.round(r.width), barH: Math.round(r.height),
                barScrollW: bar.scrollWidth, barScrollH: bar.scrollHeight,
                overflowX: getComputedStyle(bar).overflowX,
                flexWrap: getComputedStyle(bar).flexWrap,
                children: children
            };
        }""")
        if bar_info:
            print(f"筛选栏: x={bar_info['barX']} y={bar_info['barY']} w={bar_info['barW']} h={bar_info['barH']}")
            print(f"scrollW={bar_info['barScrollW']} scrollH={bar_info['barScrollH']} overflowX={bar_info['overflowX']} flexWrap={bar_info['flexWrap']}")
            print(f"\n子元素 ({len(bar_info['children'])}个):")
            ys = set()
            for i, c in enumerate(bar_info['children']):
                ys.add(c['y'])
                print(f"  {i}: {c['tag']} '{c['text'][:15]}' x={c['x']} y={c['y']} w={c['w']} h={c['h']} ws={c['whiteSpace']} shrink={c['flexShrink']}")
            # 检查是否真正换行：元素Y范围是否重叠（垂直居中不算换行）
            # 忽略零尺寸元素（如空span）
            visible_ranges = [(c['y'], c['y'] + c['h']) for c in bar_info['children'] if c['w'] > 0 and c['h'] > 0]
            if visible_ranges:
                max_start = max(r[0] for r in visible_ranges)
                min_end = min(r[1] for r in visible_ranges)
                all_same_row = max_start < min_end  # 有重叠区间
            else:
                all_same_row = True
            print(f"\nY坐标集合: {sorted(ys)} (共{len(ys)}个不同Y值)")
            if visible_ranges:
                print(f"Y重叠区间: [{max_start}, {min_end}) → {'同一行（垂直居中）' if all_same_row else '真正换行！'}")
            if not all_same_row:
                print("⚠️ 存在真正换行！")
            else:
                print("✅ 所有元素在同一行（Y坐标差异来自垂直居中）")

        # 截图
        await page.screenshot(path="tools/filter_bar.png")
        print("\n截图已保存到 tools/filter_bar.png")

        await browser.close()

asyncio.run(check())