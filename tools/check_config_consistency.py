"""截图查看前端配置区域"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'flask-version'))

from DrissionPage import ChromiumPage, ChromiumOptions

co = ChromiumOptions()
co.auto_port()
page = ChromiumPage(co)
page.get('http://127.0.0.1:5000')
time.sleep(5)

# 截图整体页面
page.get_screenshot(path='logs/config_check_full.png', full_page=True)
print("整体页面截图已保存")

# 检查前端显示的配置
print("\n=== 前端配置检查 ===")
result = page.run_js('(function(){var r={};var t=document.body.textContent;\
r.has_greeting = t.indexOf("您好，我是双一流的本科")>=0;\
r.has_salary_reply = t.indexOf("我的期望薪资是")>=0;\
r.has_greeting_reply = t.indexOf("我对这个岗位很感兴趣")>=0;\
r.has_default_reply = t.indexOf("好的，感谢您的消息")>=0;\
r.has_fallback = t.indexOf("兜底回复")>=0;\
r.ai_switch = document.querySelector("[id*=ai][id*=enable], [id*=ai][id*=switch], .ai-switch") ? "found" : "not found";\
var inputs=document.querySelectorAll("textarea, input[type=text]");\
r.input_count=inputs.length;\
r.greeting_input="";\
for(var i=0;i<inputs.length;i++){var v=inputs[i].value||inputs[i].textContent||"";if(v.indexOf("您好，我是")>=0){r.greeting_input=v.substring(0,80);break;}}\
return JSON.stringify(r);})()', as_expr=True)
print(f"配置检查: {result}")

# 查找岗位管理区域
print("\n=== 岗位管理区域 ===")
result2 = page.run_js('(function(){var r={};\
var jobCards=document.querySelectorAll("[class*=job], [class*=position], [id*=job]");\
r.job_card_count=jobCards.length;\
var greetingAreas=document.querySelectorAll("textarea");\
r.textarea_count=greetingAreas.length;\
r.textareas=[];\
for(var i=0;i<greetingAreas.length;i++){var v=greetingAreas[i].value||"";if(v.length>10)r.textareas.push(v.substring(0,60));}\
return JSON.stringify(r);})()', as_expr=True)
print(f"岗位管理: {result2}")

page.quit()
print("\n检查完成")