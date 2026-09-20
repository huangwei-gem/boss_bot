"""全量测试截图验证：前端打招呼记录和回复记录表格"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'flask-version'))

from DrissionPage import ChromiumPage, ChromiumOptions

co = ChromiumOptions()
co.auto_port()
page = ChromiumPage(co)
page.get('http://127.0.0.1:5000')
time.sleep(5)

# 截图整体页面
page.get_screenshot(path='logs/final_full_page.png', full_page=True)
print("整体页面截图已保存")

# 查找并点击打招呼记录tab
print("\n=== 打招呼记录 ===")
try:
    result = page.run_js('(function(){var tabs=document.querySelectorAll(".record-tab,.tab-btn,[role=tab],button,.nav-item");var found=[];for(var i=0;i<tabs.length;i++){var t=tabs[i].textContent.trim();if(t.indexOf("打招呼")>=0||t.indexOf("投递记录")>=0){tabs[i].click();found.push(t.substring(0,20));}}return JSON.stringify(found);})()', as_expr=True)
    print(f"点击打招呼tab: {result}")
    time.sleep(2)
    page.get_screenshot(path='logs/final_greet_records.png', full_page=True)
    print("打招呼记录截图已保存")
except Exception as e:
    print(f"打招呼记录失败: {e}")

# 查找并点击回复记录tab
print("\n=== 回复记录 ===")
try:
    result = page.run_js('(function(){var tabs=document.querySelectorAll(".record-tab,.tab-btn,[role=tab],button,.nav-item");var found=[];for(var i=0;i<tabs.length;i++){var t=tabs[i].textContent.trim();if(t.indexOf("回复记录")>=0){tabs[i].click();found.push(t.substring(0,20));}}return JSON.stringify(found);})()', as_expr=True)
    print(f"点击回复tab: {result}")
    time.sleep(2)
    page.get_screenshot(path='logs/final_reply_records.png', full_page=True)
    print("回复记录截图已保存")
except Exception as e:
    print(f"回复记录失败: {e}")

# 检查表格数据
print("\n=== 表格数据检查 ===")
try:
    result = page.run_js('(function(){var r={};var gb=document.getElementById("greetTableBody");r.greet_rows=gb?gb.children.length:0;if(gb&&gb.children.length>0){var fr=gb.children[0];r.greet_first=[];for(var i=0;i<fr.children.length;i++)r.greet_first.push(fr.children[i].textContent.trim().substring(0,25));}var rb=document.getElementById("replyTableBody");r.reply_rows=rb?rb.children.length:0;if(rb&&rb.children.length>0){var fr=rb.children[0];r.reply_first=[];for(var i=0;i<fr.children.length;i++)r.reply_first.push(fr.children[i].textContent.trim().substring(0,25));}var gc=document.getElementById("greetTabCount");var rc=document.getElementById("replyTabCount");r.greet_tab_count=gc?gc.textContent:"not found";r.reply_tab_count=rc?rc.textContent:"not found";return JSON.stringify(r);})()', as_expr=True)
    print(f"表格数据: {result}")
except Exception as e:
    print(f"表格检查失败: {e}")

page.quit()
print("\n截图验证完成")
