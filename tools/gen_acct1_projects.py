# -*- coding: utf-8 -*-
"""生成「号2 补两条数据分析项目」的步骤脚本（一条命令跑完两段，省两次开页面）。

为什么要生成而不是手写：两段项目除了名称和正文一模一样，手写就是把自己
在同一件事上敲两遍，改的时候只改得到一处。
"""
import io
import json

OUT = "data/resume_steps/acct1_add_two_projects.json"

格 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const c = [...document.querySelectorAll('.cell')].filter((x) => vis(x) && clean(x.innerText) === '__要__'
             && !/disabled/.test((x.className || '').toString()))[0];
  if (!c) return 'NO CELL __要__';
  c.click(); return '点了 __要__';
})()"""


def 格点(要, 等):
    return {"do": "js", "limit": 300, "wait": 等, "code": 格.replace("__要__", 要)}


闸 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 找 = (p) => [...document.querySelectorAll('input,textarea')].filter((x) => vis(x) && (x.placeholder || '').includes(p));
  const 名 = 找('直聘网'), 角色 = 找('UI 设计师'), 时间 = 找('选择年月'), 描述 = 找('描述该项目');
  const 问 = [];
  if (!名.length || clean(名[0].value) !== '__名__') 问.push('名称=' + (名.length ? clean(名[0].value) : '空'));
  if (!角色.length || !clean(角色[0].value)) 问.push('角色=空');
  if (时间.length < 2 || !clean(时间[0].value) || !clean(时间[1].value)) 问.push('时间=' + 时间.map((x) => clean(x.value)).join('~'));
  if (!描述.length || 描述[0].value.length < 60) 问.push('描述=' + (描述.length ? 描述[0].value.length : 0) + '字');
  const 报错 = [...document.querySelectorAll('.form-error-tip')].filter(vis).map((x) => clean(x.innerText));
  if (报错.length) 问.push('页面报错=' + 报错.join('/'));
  if (问.length) return '不保存，缺：' + 问.join('；');
  const ok = [...document.querySelectorAll('[ka$="_ok"]')].filter(vis)[0]
          || [...document.querySelectorAll('span,a,button')].filter((x) => vis(x) && clean(x.innerText) === '完成')[0];
  if (!ok) return '全对但没找到完成按钮';
  ok.click();
  return '闸门全过，已点完成：' + clean(名[0].value) + ' / ' + clean(角色[0].value) + ' / ' + clean(时间[0].value)
       + '-' + clean(时间[1].value) + ' / 描述' + 描述[0].value.length + '字 / ka=' + (ok.getAttribute('ka') || '按文字');
})()"""

收尾 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 框 = [...document.querySelectorAll('input')].filter((x) => vis(x) && (x.placeholder || '').includes('直聘网'))[0];
  const b = document.getElementById('project');
  return {表单还开着: !!框, 报错: [...document.querySelectorAll('.form-error-tip')].filter(vis)
    .map((x) => clean(x.innerText)).slice(0, 4), 板块: b ? clean(b.innerText).slice(0, 240) : 'NO BLOCK'};
})()"""

回读 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const b = document.getElementById('project');
  return b ? clean(b.innerText).slice(0, 1500) : 'NO BLOCK';
})()"""


def 一段(名称, 描述):
    return [
        {"do": "open"},
        {"do": "mark", "at": {"ph": "直聘网", "覆盖": True}},
        {"do": "typein", "text": 名称, "wait": 1.5},
        {"do": "mark", "at": {"ph": "UI 设计师", "覆盖": True}},
        {"do": "typein", "text": "数据分析", "wait": 1.5},
        {"do": "openfield", "field": {"ph": "选择年月", "nth": 0}, "wait": 2.0},
        格点("2024", 1.5), 格点("七月", 2.0),
        {"do": "openfield", "field": {"ph": "选择年月", "nth": 1}, "wait": 2.0},
        格点("2025", 1.5), 格点("一月", 2.0),
        {"do": "mark", "at": {"ph": "描述该项目", "覆盖": True}},
        {"do": "typein", "text": 描述, "wait": 3.0},
        {"do": "js", "limit": 900, "wait": 5.0, "code": 闸.replace("__名__", 名称)},
        {"do": "js", "limit": 500, "wait": 2.0, "code": 收尾},
    ]


A = ("项目描述：短期快速提高销售额，做一个'黑五活动'方案，针对行业常见指标对用户进行分类，为运营部门提供数据支持，"
     "从用户消费行为特征分析如何优化运营和销售策略，帮助运营部门实现精细化运营。\n客户画像：\n"
     "1.从运营部门获取用户行为数据进行数据清洗;\n2.用户消费特征(整体消费,个人消费);\n3.用户消费周期分析(购买周期,生命周期);\n"
     "4.用户价值度分析(RFM 模型)，通过用户画像，挖掘高价值用户，实现精准引流;\n行为分析：\n"
     "1.用户活跃度分析(分析单位时间下用户组成的变化),如新用户,活跃用户,回流用户,不活跃用户;\n"
     "2.用户行为分析,从用户的留存率，回购率和复购率等方面进行分析,提供策略提高用户的转化率，减少流失率；\n"
     "3.用户贡献率分析,较少用户贡献率较大的KPI;\n项目结论与成果:\n"
     "1.对于八类客户给到不同的活动方案，用户领取率百分之80，变现转化率百分之20\n"
     "相关技术：Pandas、Numpy、Matplotlib、Seaborn、Python、RFM 用户价值模型,K-Means 等;")

B = ("项目描述: 通过分析用户的搜索内容进行分析用户喜好，改善自己产品信息的关键字，提高曝光率，让自己的产品链接更容易被客户搜索\n"
     "项目技术与职责:\n1.从数据库获取数据，并且利用numpy、pandas进行数据清洗，筛选；\n"
     "2.针对用户的网站搜索关键词，以及筛选条件，各界面的停留时长等，有针对性的发布更受用户喜爱的标题及内容，"
     "例如根据搜索关键词分析，将搜索频率高的进行加大投放；\n3.利用matplotlib、Tableau等可视化工具生成可视化分析报告。\n"
     "项目结论与成果:\n1.通过大量数据，得知了用户更喜爱的搜索方式，从而进行产品标题关键字的修改，从而提高客户点击概率。\n"
     "2.根据停留时长的分析，进行版块的填充以及扩展。从而提升客户购买的概率。")

步 = (一段("用户画像及行为分析(RFM 分群)", A)
       + 一段("用户搜索关键词分析", B)
       + [{"do": "reload", "sec": 8}, {"do": "js", "limit": 1600, "code": 回读}])

json.dump(步, io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("写出", OUT, len(步), "步")
