# -*- coding: utf-8 -*-
"""生成「两个号各补一条天鹅到家实习」的步骤脚本。

机制都是这一趟在号2 项目经历上实测跑通的那一套：
- 普通输入框：先打标（按 placeholder），再 element.input 写进去（键盘敲不行，
  框在视口外时一个字符都不落地；execCommand 只改 DOM 不进 Vue 模型）；
- 所属行业/职位名称这种分类面板：真点打开 → 打标那一格 → 真点那一格；
- 年月：JS 点格子（先年后月，实测有效）；
- 最后闸门：所有值读回来核一遍，缺一律不点完成，把缺的印出来。
"""
import io
import json
import os

格 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const c = [...document.querySelectorAll('.cell')].filter((x) => vis(x) && clean(x.innerText) === '__要__'
             && !/disabled/.test((x.className || '').toString()))[0];
  if (!c) return 'NO CELL __要__';
  c.click(); return '点了 __要__';
})()"""

面板 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const e = document.querySelector('__弹层__ input') || [...document.querySelectorAll('input')]
      .find((x) => (x.placeholder || '').includes('请输入职位关键词'));
  if (!e) return 'NO PANEL SEARCH INPUT';
  e.focus();
  const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  s.call(e, ''); e.dispatchEvent(new Event('input', {bubbles: true}));
  s.call(e, '__筛__'); e.dispatchEvent(new Event('input', {bubbles: true}));
  return '筛了 ' + e.value;
})()"""

闸 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 找 = (p) => [...document.querySelectorAll('input,textarea')].filter((x) => vis(x) && (x.placeholder || '').includes(p));
  const 公司 = 找('北京华品博睿'), 行业 = 找('所属行业'), 职位 = 找('职位名称'), 时间 = 找('在职时间');
  const 描述 = 找('新员工入职培训');
  const 问 = [];
  if (!公司.length || clean(公司[0].value).indexOf('天鹅到家') < 0) 问.push('公司=' + (公司.length ? clean(公司[0].value) : '空'));
  if (!行业.length || !clean(行业[0].value)) 问.push('行业=空');
  if (!职位.length || !clean(职位[0].value)) 问.push('职位=空');
  if (时间.length < 2 || !clean(时间[0].value) || !clean(时间[1].value)) 问.push('时间=' + 时间.map((x) => clean(x.value)).join('~'));
  if (!描述.length || 描述[0].value.length < 100) 问.push('正文=' + (描述.length ? 描述[0].value.length : 0) + '字');
  const 报错 = [...document.querySelectorAll('.form-error-tip')].filter(vis).map((x) => clean(x.innerText));
  __核报错__
  if (问.length) return '不保存，缺：' + 问.join('；');
  const ok = [...document.querySelectorAll('[ka$="_ok"]')].filter(vis)[0];
  if (!ok) return '全对但没找到完成按钮';
  ok.click();
  return '闸门全过，已点完成：' + clean(公司[0].value) + ' / ' + clean(行业[0].value) + ' / ' + clean(职位[0].value)
       + ' / ' + clean(时间[0].value) + '-' + clean(时间[1].value) + ' / 正文' + 描述[0].value.length + '字'
       + ' / 屏幕上的红字=' + (报错.join('/') || '无');
})()"""

收尾 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 框 = [...document.querySelectorAll('input')].filter((x) => vis(x) && (x.placeholder || '').includes('北京华品博睿'))[0];
  const b = document.getElementById('history');
  return {表单还开着: !!框, 报错: [...document.querySelectorAll('.form-error-tip')].filter(vis)
    .map((x) => clean(x.innerText)).slice(0, 4), 板块: b ? clean(b.innerText).slice(0, 500) : 'NO BLOCK'};
})()"""

回读 = """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const b = document.getElementById('history');
  return b ? clean(b.innerText).slice(0, 1400) : 'NO BLOCK';
})()"""


def 格点(要, 等):
    return {"do": "js", "limit": 300, "wait": 等, "code": 格.replace("__要__", 要)}


# 核报错=0 是给这一页留的口子：BOSS 的工作经历表单上「请填写所在公司/请输入工作内容」
# 这两行红字，在框里明明有值的时候也照样挂着（两种写法实测都这样），分不清是
# 真校验还是常驻提示。值全部读回来对上才点完成，红字只印出来不参与拦。
核 = ('if (报错.length) 问.push("页面报错=" + 报错.join("/"));'
     if os.environ.get("CHECK_TIPS", "1") != "0" else " ")


def 一段(职位筛, 职位兜底, 正文):
    return [
        {"do": "open"},
        # 公司名称/工作内容用原生 setter（赋值那条）：这一页真点聚焦再敲字，BOSS 的联想框
        # 会停在"打字中"状态，模型不认，点完成就报"请填写所在公司"（实测两种写法对比出来的）。
        {"do": "setph", "wait": 2.0, "specs": [
            {"ph": "北京华品博睿", "value": "天鹅到家"},
            {"lbl": "工作内容", "value": 正文}]},
        {"do": "js", "limit": 500, "code": """(() => {
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 公司 = [...document.querySelectorAll('input')].find((x) => (x.placeholder || '').includes('北京华品博睿'));
  return {公司: 公司 ? clean(公司.value) : '没框', 报错: [...document.querySelectorAll('.form-error-tip')].filter(vis).map((x) => clean(x.innerText)).slice(0, 4)};
})()"""},
        {"do": "realclick", "css": "div[ka=resume_form_edit_industryName]", "wait": 2.5},
        {"do": "mark", "试几次": 3, "at": {"scope": ".industry-dialog", "want": "生活服务(O2O)",
                                          "隐藏也认": True, "覆盖": True}},
        {"do": "realclick", "css": "[data-orca-tag=hit]", "wait": 2.5},
        {"do": "openfield", "field": {"ph": "选择职位名称"}, "wait": 3.0},
        {"do": "js", "limit": 400, "wait": 2.5, "code": 面板.replace("__弹层__", ".position-dialog").replace("__筛__", 职位筛)},
        {"do": "mark", "试几次": 3, "at": {"scope": ".position-dialog", "tag": "li", "覆盖": True,
                                          "regex": 职位筛 + "|" + 职位兜底, "只要叶子": False}},
        {"do": "realclick", "css": "[data-orca-tag=hit]", "wait": 2.5},
        {"do": "openfield", "field": {"ph": "选择在职时间", "nth": 0}, "wait": 2.0},
        格点("2026", 1.5), 格点("五月", 2.0),
        {"do": "openfield", "field": {"ph": "选择在职时间", "nth": 1}, "wait": 2.0},
        格点("2026", 1.5), 格点("九月", 2.0),
        {"do": "js", "limit": 900, "wait": 5.0, "code": 闸.replace("__核报错__", 核)},
        {"do": "js", "limit": 700, "wait": 2.0, "code": 收尾},
        {"do": "reload", "sec": 8},
        {"do": "js", "limit": 1500, "code": 回读},
    ]


号1 = ("核心工作\n1.参与全链路智能招聘管理系统从0到1设计与落地，覆盖岗位发布、简历收集、AI解析匹配、"
       "AI能力维度初筛（0-5分打分）、面试评估、Offer发放、入职与试用期管理全流程；负责AI初筛规则、"
       "简历智能筛选、批量重新评估等产品方案设计。\n2.将招聘、绩效考核、试用期管理、薪资定级、离职流程等人事业务"
       "线上化、智能化，设计并沉淀招聘沟通话术库，参与AI外呼产品方案与话术设计，推动“人事+AI”场景落地。\n"
       "3.设计飞书多维表格数据同步、机器人卡片消息（面试提醒、招聘日报）等集成方案，实现招聘数据双源聚合与"
       "招聘日报自动生成。\n4.负责JD生成、简历解析、AI初筛、招聘日报等AI提示词模板的设计与持续优化，"
       "参与模型选型与多层降级方案设计（DeepSeek / Llama 等）。\n关键成果\n"
       "1.招聘管理系统已在生产环境稳定运行，AI初筛实现批量自动化评估（单批覆盖数百份简历）。\n"
       "2.构建7级招聘漏斗与HRBP效能看板（周报/月报），支撑招聘决策。\n"
       "3.面试提醒、招聘日报等机器人消息与业务筛选链接（面试官专属、Token鉴权）上线，实现招聘流程线上闭环。")

号2 = ("1、招聘数据体系建设：基于“岗位发布→简历收集→AI初筛→业务筛选→面试→Offer→入职”全链路招聘流程，"
       "梳理招聘指标体系（简历量、初筛通过率、面试转化率、Offer率、入职率等），统一指标口径，"
       "搭建招聘数据看板（周报/月报），持续追踪业务质量数据、定位指标异常并归因；\n"
       "2、AI 面试系统数据分析：参与类北森 AI 智能招聘系统的数据分析侧建设，对 AI 初筛评分、能力维度评估结果"
       "进行回收与校验，分析漏斗转化与各环节耗时，输出优化建议；参与“批量重新评估”等 AI 工具的效果数据分析；\n"
       "3、人事数据专题分析：围绕绩效考核、试用期管理、薪资定级、离职流程等人事场景进行数据整理与分析，"
       "输出人效、试用期通过率、离职原因分布等专题分析，为 HR 决策提供数据支持；\n"
       "4、AI 外呼效果分析：对 AI 外呼的接通率、有效沟通率、意向转化等指标进行数据清洗与可视化分析，"
       "复盘话术效果，推动外呼策略迭代。")

出 = {
    "acct0": 一段("人事实习生", "人力资源专员", 号1),
    "acct1": 一段("数据分析师", "数据分析", 号2),
}
for 名, 步 in 出.items():
    核 = ('if (报错.length) 问.push("页面报错=" + 报错.join("/"));'
          if os.environ.get("CHECK_TIPS", "1") != "0" else " ")
    p = "data/resume_steps/%s_add_work_swan.json" % 名
    json.dump(步, io.open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写出", p, len(步), "步")
