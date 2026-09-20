"""截图查看前端打招呼记录和回复记录的显示问题"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'flask-version'))

from DrissionPage import ChromiumPage, ChromiumOptions

# 用普通Chrome打开前端
co = ChromiumOptions()
co.auto_port()
page = ChromiumPage(co)
page.get('http://127.0.0.1:5000')
time.sleep(3)

# 截图整体页面
page.get_screenshot(path='logs/screenshot_full_page.png', full_page=True)
print("整体页面截图已保存")

# 查找打招呼记录表格
print("\n=== 查找打招呼记录表格 ===")
# 尝试点击打招呼记录tab
try:
    greet_tab = page.ele('text=打招呼记录', timeout=3)
    if greet_tab:
        greet_tab.click()
        time.sleep(2)
        page.get_screenshot(path='logs/screenshot_greet_records.png', full_page=True)
        print("打招呼记录截图已保存")
        
        # 查看表格结构
        table = page.ele('.greet-table, #greetTable, table', timeout=3)
        if table:
            print(f"找到表格: tag={table.tag}, class={table.attr('class')}")
            # 查看表头
            headers = table.eles('th, thead td', timeout=2)
            print(f"表头列数: {len(headers)}")
            for i, h in enumerate(headers):
                print(f"  列{i}: {h.text[:20]}")
            # 查看首行
            rows = table.eles('tr', timeout=2)
            if len(rows) > 1:
                first_row = rows[1]  # 跳过表头
                cells = first_row.eles('td, th', timeout=2)
                print(f"首行单元格数: {len(cells)}")
                for i, c in enumerate(cells):
                    print(f"  单元格{i}: {c.text[:30]}")
        else:
            print("未找到表格")
    else:
        print("未找到打招呼记录tab")
except Exception as e:
    print(f"打招呼记录操作失败: {e}")

# 查找回复记录表格
print("\n=== 查找回复记录表格 ===")
try:
    reply_tab = page.ele('text=回复记录', timeout=3)
    if reply_tab:
        reply_tab.click()
        time.sleep(2)
        page.get_screenshot(path='logs/screenshot_reply_records.png', full_page=True)
        print("回复记录截图已保存")
        
        # 查看表格结构
        table = page.ele('.reply-table, #replyTable, table', timeout=3)
        if table:
            print(f"找到表格: tag={table.tag}, class={table.attr('class')}")
            headers = table.eles('th, thead td', timeout=2)
            print(f"表头列数: {len(headers)}")
            for i, h in enumerate(headers):
                print(f"  列{i}: {h.text[:20]}")
            rows = table.eles('tr', timeout=2)
            if len(rows) > 1:
                first_row = rows[1]
                cells = first_row.eles('td, th', timeout=2)
                print(f"首行单元格数: {len(cells)}")
                for i, c in enumerate(cells):
                    print(f"  单元格{i}: {c.text[:30]}")
        else:
            print("未找到表格")
    else:
        print("未找到回复记录tab")
except Exception as e:
    print(f"回复记录操作失败: {e}")

# 也查看投递记录区域
print("\n=== 查找投递记录区域 ===")
try:
    # 查看所有tab
    tabs = page.eles('.tab, [role=tab], .nav-tab', timeout=2)
    print(f"找到 {len(tabs)} 个tab")
    for t in tabs:
        print(f"  tab: {t.text[:20]}")
except Exception as e:
    print(f"tab查找失败: {e}")

page.quit()
print("\n截图完成")