# -*- coding: utf-8 -*-
"""「按地点筛」与「AI 自定义筛选」这两块界面的源码锁（2026-10-28）。

他要的是两件具体的事，缺一样就等于没做：
① 地点能直接下拉选 —— 池里的 city/location 是「长沙·岳麓区·…」这种整段地址，
   所以下拉的选项必须由接口拿整池现算，不能写死几个城市；
② 「AI 自定义筛选」得有一块看得见的框 —— 他把自己的文件丢进去，AI 按那份要求
   把池里的岗位逐条重判，判「合」的勾一下就能挑出来。

这里锁的是形状和接线（JS 跑不了单测，真机点击在 tests/e2e_job_pool_ui.py）。
最要紧的两条硬规矩也钉在这：
- 「只看 AI 匹配」只能认当前这份文件的结论：换了文件旧结论不算数，
  没有文件时这一个勾必须当场退回，不许拿老行糊出一张"匹配"表；
- 保存接口回 started=false 时不许弹绿 —— 那是"存下了但没跑"，他要知道。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")


def _body(fn_name):
    m = re.search(r"\nfunction " + re.escape(fn_name) + r"\s*\(", HTML)
    assert m, f"界面上已经没有 {fn_name}() 了"
    i = HTML.index("{", m.start())
    depth = 0
    for j in range(i, len(HTML)):
        if HTML[j] == "{":
            depth += 1
        elif HTML[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML[i:j + 1]
    raise AssertionError(f"{fn_name} 的花括号没闭合")


def _id次数(name):
    return len(re.findall(r'id="' + re.escape(name) + r'"', HTML))


def test_新控件都在且各只一个():
    """同 id 两份的话 getElementById 只认第一个：另一处永远收不到值"""
    for name in ("poolFilterCity", "poolFilterAiState", "aiScreenPanel",
                 "aiScreenText", "aiScreenFile", "aiScreenSave", "aiScreenStop",
                 "aiScreenRunText", "aiScreenStat", "aiScreenDocName",
                 "aiScreenCollapse", "aiScreenBody"):
        assert _id次数(name) == 1, f"{name} 有 {_id次数(name)} 个"


def test_地点是下拉不是让他打字():
    """他原话就是「地点不能直接筛选」：表头那一行得有一个城市下拉，接 applyPoolFilter"""
    m = re.search(r'<select[^>]*id="poolFilterCity".*?</select>', HTML, re.S)
    assert m, "表头筛选行里没有城市下拉"
    assert "applyPoolFilter()" in m.group(0), "选了城市不重拉表"
    # 选项来自接口现算的 cities，不是写死的几个城市
    assert "poolFillCities(data.cities)" in _body("loadJobPool")
    fill = _body("poolFillCities")
    assert "全部城市" in fill, "得留一个「全部城市」的空值项，否则选完就回不去"
    assert "count" in fill, "选项上要带条数，不然他不知道哪个城市有货（也不能只报 0）"


def test_城市下拉不许被轮询冲掉():
    """20 秒一次的轮询重画一次下拉，正展开着就点不下去（他正在选城市）"""
    fill = _body("poolFillCities")
    assert "document.activeElement === sel" in fill, "下拉开着的时候重建＝选项一闪就没了"
    assert "sel.value = picked" in fill, "重拉后他选的那个城市得留着，不许悄悄回到全部"


def test_筛选参数逐格进了请求():
    """每一格都得由 POOL_FILTERS 那张表驱动：手写的清单会漏掉新加的那一格。
    真跑一遍拼出来的 query 在 tests/test_job_pool_filters.py（node），
    这里只钉"是不是遍历那张表拼的"。"""
    qs = _body("poolFilterQs")
    assert "POOL_FILTERS.map" in qs, "拼参数不再逐格遍历，新加的那格永远进不了请求"
    assert "sort=" in qs, "排序没带上：屏上的顺序跟他刚选的不是一回事"
    reset = _body("resetPoolFilter")
    assert "POOL_FILTERS.forEach" in reset, "重置得逐格清：手写的清单会漏掉新加的那一格"


def test_只看AI匹配没文件时必须当场退回():
    """没有当前文件时把老行当成「按我的要求匹配」，那张表就是假的。
    这里钉的是：选了「判成合」但接口说没有指纹，就退回全部、给一句实话、重拉。"""
    body = _body("loadJobPool")
    assert "poolFilterAiState" in body
    assert "aim.value = ''" in body, "没文件时那一格还停在「判成合」，屏上是空的却不解释"
    assert "return;" in body, "退回之后要停下这次渲染，按筛空的旧参数画表等于又画一遍假的"
    # 空指纹的判定在服务端也要有（界面只是第一道）
    from boss_bot.job_pool import 筛行
    assert 筛行([{"job_name": "x", "ai": {"合": True, "doc": "", "判于": 1.0}}],
                AI档="合") == []


def test_AI筛选框看得见_不是只剩一个按钮():
    """他要的是「框」：能看、能改、能把文件丢进去"""
    panel = re.search(r'<div class="ai-screen-panel" id="aiScreenPanel">.*?\n            </div>', HTML, re.S)
    assert panel, "池 tab 里没有 AI 自定义筛选那块面板"
    块 = panel.group(0)
    assert "AI 自定义筛选" in 块
    assert 'id="aiScreenText"' in 块 and "<textarea" in 块, "没有可编辑的要求正文框"
    assert 'type="file"' in 块 and ".docx" in 块 and ".pdf" in 块, "不能选文件＝他只能手粘"
    assert "保存并匹配" in 块
    # 筛选栏那个按钮要把人带到这块面板上，而不是弹个空提示
    assert "aiScreenFocus()" in HTML
    focus = _body("aiScreenFocus")
    assert "scrollIntoView" in focus and "aiScreenText" in focus, "点了按钮看不见框、光标也不在框里"


def test_选文件走解析接口_读不了要说实话():
    body = _body("aiScreenPickFile")
    assert "/api/ai_screen_doc/parse" in body
    assert "new FormData()" in body and "fd.append('file', f)" in body, "文件没挂到请求上"
    assert "读不了这个文件" in body, "解析失败不吭声，他只会觉得文件传上去了"
    assert "还没保存" in body, "读进框里但没落盘，得说清，否则他以为已经在跑了"


def test_保存后照实说跑没跑():
    """后端存完就开跑；开不起来（上一轮还在跑）时必须说 warn，不许弹绿了事"""
    body = _body("aiScreenSave")
    assert "/api/ai_screen_doc" in body
    assert re.search(r"toast\(d\.started \?", body), "话术没看 d.started：没跑起来也会说「开始匹配」"
    assert "'ok' : 'warn'" in body, "没开起来还报成功＝他以为在匹配、实际一行没判"
    assert "loadJobPool()" in body, "存了新要求不重拉表，屏上还是旧结论"


def test_运行状态与进度都读真数字():
    body = _body("aiScreenRender")
    assert "run.running" in body and "run.judged" in body and "run.matched" in body
    assert "run.failed" in body, "没判成多少条不报，他看着「跑完了」以为都判了"
    assert "stats.pending" in body and "stats.judged" in body
    assert "run.stopped" in body, "他自己停的和崩掉的得说成两件事"
    assert "aiScreenStop" in body, "在跑的时候要能停"
    # 文档名与指纹要上屏，不然他不知道现在按的是哪一份
    assert "doc.name" in body and "doc.hash" in body


def test_跑完要自己把表刷新过来():
    """轮询看见 running→false 不重拉表，新结论得等他再点一次刷新才看得见"""
    body = _body("loadAiScreenDoc")
    assert "上轮.running && !在跑" in body and "loadJobPool()" in body
    assert "startAiScreenPoll()" in body, "在跑的时候要接着轮"
    # 跑的时候别每 5 秒重排三千行
    assert "60000" in body, "跑的过程中按 judged 变化拉表，也必须限量（三千行一次要几十秒）"
    # 池 tab 的 20 秒轮询也要把跑着的进度续上，否则切走再切回来进度就停在那
    assert re.search(r"if \(aiScreenLast && aiScreenLast\.running && !aiScreenTimer\)", HTML)


def test_切到池tab会读AI筛选要求与运行状态():
    body = _body("switchRecordTab")
    assert "loadAiScreenDoc()" in body
