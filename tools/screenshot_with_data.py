"""截图查看前端有数据时的显示问题"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'flask-version'))

from DrissionPage import ChromiumPage, ChromiumOptions

co = ChromiumOptions()
co.auto_port()
page = ChromiumPage(co)
page.get('http://127.0.0.1:5000')
time.sleep(5)

# 截图整体页面
page.get_screenshot(path='logs/screenshot_with_data.png', full_page=True)
print("整体页面截图已保存")

# 查找所有tab-like元素
print("\n=== 查找所有tab ===")
try:
    # 尝试多种选择器找tab
    for sel in ['.tab-btn', '.tab-item', '[role=tab]', '.nav-link', '.panel-tab', 'button']:
        elems = page.eles(sel, timeout=1)
        if elems:
            texts = [e.text[:20] for e in elems if e.text.strip()]
            if texts:
                print(f"  {sel}: {texts}")
except Exception as e:
    print(f"tab查找失败: {e}")

# 尝试点击打招呼记录tab
print("\n=== 打招呼记录 ===")
try:
    # 用文本查找
    greet_tab = page.ele('text:打招呼', timeout=3)
    if greet_tab:
        print(f"找到打招呼元素: tag={greet_tab.tag}, text={greet_tab.text[:30]}")
        greet_tab.click()
        time.sleep(2)
        page.get_screenshot(path='logs/screenshot_greet_with_data.png', full_page=True)
        print("打招呼记录截图已保存")
except Exception as e:
    print(f"打招呼记录失败: {e}")

# 尝试点击回复记录tab
print("\n=== 回复记录 ===")
try:
    reply_tab = page.ele('text:回复', timeout=3)
    if reply_tab:
        print(f"找到回复元素: tag={reply_tab.tag}, text={reply_tab.text[:30]}")
        reply_tab.click()
        time.sleep(2)
        page.get_screenshot(path='logs/screenshot_reply_with_data.png', full_page=True)
        print("回复记录截图已保存")
except Exception as e:
    print(f"回复记录失败: {e}")

# 查看投递记录表格的HTML结构
print("\n=== 表格HTML结构 ===")
try:
    # 查找所有表格
    tables = page.eles('table', timeout=2)
    print(f"找到 {len(tables)} 个table")
    for i, t in enumerate(tables):
        cls = t.attr('class') or ''
        # 获取表头
        ths = t.eles('th', timeout=1)
        headers = [th.text[:20] for th in ths]
        # 获取前3行数据
        trs = t.eles('tbody tr', timeout=1)
        rows_text = []
        for tr in trs[:3]:
            tds = tr.eles('td', timeout=1)
            row = [td.text[:25] for td in tds]
            rows_text.append(row)
        print(f"\n  表格{i}: class={cls}, 列数={len(headers)}, 行数={len(trs)}")
        print(f"    表头: {headers}")
        for j, row in enumerate(rows_text):
            print(f"    行{j}: {row}")
except Exception as e:
    print(f"表格分析失败: {e}")

page.quit()
print("\n截图完成")