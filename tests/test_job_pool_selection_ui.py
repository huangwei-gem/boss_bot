# -*- coding: utf-8 -*-
"""岗位池「勾选」这一列的源码锁（2026-10-29）。

他截图指着行首那一列说两件事：框太小、要有全选。查下来根因是形状问题：
`.pool-sel{width:26px}` 减掉 `td{padding:7px 10px}` 的左右各 10px，内容框只剩
6px，17px 的勾选框被裁成一个点 —— 全选框一直都在，只是他也看不见。

这里锁的是成熟表格该有的那套形状（真机点击在 tests/e2e_job_pool_ui.py）：
- 列宽 44px、内边距归零、整格包在 label 里（点格子任意位置都算点中）；
- 表头三态：全选 / 勾了一部分（indeterminate）/ 没勾；
- 已勾条数上屏，批量按钮没勾时必须是灰的（不许亮着点下去说"没勾"）；
- 改勾只动 DOM 上的勾选框，不重建三千行（重建一次几十秒）；
- Shift+点行首能连续勾一段。
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


def _规则(选择器):
    m = re.search(re.escape(选择器) + r"\{([^}]*)\}", HTML)
    assert m, f"CSS 里没有 {选择器} 这条"
    return m.group(1)


def test_勾选列不再被内边距裁成一个点():
    """26px 减去左右内边距只剩 6px —— 这就是他看到的"小框"。列宽与内边距两边都得改。"""
    assert ".pool-sel{width:26px" not in HTML, "还在用会把框裁掉的老宽度"
    宽 = re.search(r"width:\s*(\d+)px", _规则(".greet-table th.pool-sel,.greet-table td.pool-sel"))
    assert 宽 and int(宽.group(1)) >= 40, "勾选列还是太窄，17px 的框放不平"
    assert "padding:0" in _规则(".greet-table th.pool-sel,.greet-table td.pool-sel"), \
        "内边距没归零，列宽再大也会被吃掉"
    box = _规则("#poolTable input[type=checkbox]")
    assert re.search(r"width:\s*1[5-9]px", box) and re.search(r"height:\s*1[5-9]px", box), \
        "勾选框自己的尺寸没放大（浏览器默认 13px 左右）"


def test_整格都是可点区域():
    """框放大了但只有框本身能点，鼠标还是很难瞄准；整格包 label 才是成熟做法"""
    行 = re.search(r"'<td class=\"pool-sel\"><label><input type=\"checkbox\" class=\"pool-acc\"[^']*'", HTML)
    assert 行, "行首格子没有包在 label 里"
    head = re.search(r'<th[^>]*class="pool-sel"[^>]*>.*?</th>', HTML, re.S)
    assert head and "<label" in head.group(0) and "poolCheckAll" in head.group(0), \
        "表头全选框没包在 label 里"
    assert ".pool-sel label{" in HTML and "cursor:pointer" in _规则(".pool-sel label"), \
        "label 没做成可点的手型"


def test_表头三态_全选与勾一半要分得出来():
    body = _body("poolSyncSelection")
    assert "all.checked" in body, "表头从来不反映全选"
    assert "all.indeterminate" in body, \
        "勾了一半表头还显示全选/没选，下一次点它是反的（成熟表格都是三态）"
    assert "hit === rows.length" in body, "判断全选没按当前筛出的行数算"


def test_改勾不许重建整张表():
    """三千行重画一次几十秒：勾一条 / 全选 / 取消都得就地改 DOM"""
    for fn in ("poolRowCheck", "poolCheckAll", "poolClearChecked"):
        assert "renderJobPool(" not in _body(fn), f"{fn} 还在整表重画"
    assert "poolPaintChecks();" in _body("poolCheckAll")
    paint = _body("poolPaintChecks")
    assert "input.pool-acc" in paint and ".checked" in paint, "没按 data-key 把勾回填到已有行上"
    assert "innerHTML" not in paint, "回填勾选框不该重建行"


def test_已勾条数与批量按钮联动():
    body = _body("poolSyncSelection")
    assert "poolSelCount" in body, "已勾了多少条不上屏，他没法确认自己勾对了没有"
    for bid in ("poolBatchSelected", "poolBatchNew", "poolBatchRejected",
                "poolDeleteSelected", "poolClearChecked"):
        assert f"'{bid}'" in body, f"{bid} 没接进可用状态"
        assert f'id="{bid}"' in HTML, f"工具栏里没有 {bid} 这个按钮"
    assert re.search(r"b\.disabled = total === 0", body), "没勾任何行时批量按钮还是亮的"
    for bid in ("poolBatchSelected", "poolBatchNew", "poolBatchRejected",
                "poolDeleteSelected", "poolClearChecked"):
        m = re.search(r'<button[^>]*id="' + bid + r'"[^>]*>', HTML)
        assert m and "disabled" in m.group(0), f"{bid} 首屏不是灰的（还没勾任何行）"


def test_表头全选只管当前筛出来的那些():
    body = _body("poolCheckAll")
    assert "poolRows" in body, "全选没绑在当前筛出的结果上"
    assert "poolLastCheckIdx = -1" in body, "换了一批结果还留着旧的 Shift 锚点，下一段会勾到看不见的行"


def test_Shift点行首能连着勾一段():
    assert "this.checked, event" in HTML, "行勾选框没把事件传进来，拿不到 shiftKey"
    body = _body("poolRowCheck")
    assert "shiftKey" in body and "poolLastCheckIdx" in body, "没有 Shift 连续勾"
    assert "Math.min" in body and "Math.max" in body, "范围没按上下界算（往上点就勾不到）"


def test_同步入口在数据到达与铺行结束都跑到():
    """表是分批铺的：等最后一批铺完再更新按钮，前几十秒按钮状态是假的"""
    assert "poolSyncSelection();" in _body("loadJobPool"), "数据一到没同步勾选状态"
    assert "poolSyncSelection();" in _body("renderJobPool"), "铺行结束没同步表头与按钮"


def test_宽表不许把列挤没():
    m = re.search(r"#poolTable\{min-width:(\d+)px\}", HTML)
    assert m and int(m.group(1)) >= 1000, "没有最小宽度，窗口窄一点 12 列会挤成一团"
    assert "overflow:auto" in re.search(r'id="poolTabContent"[^>]*>', HTML).group(0), \
        "表格比容器宽时要能横向滚，否则只能压列"


def test_勾选框有无障碍名字():
    # 分页以后那一勾只管本页，读屏念出来的也该是「本页」（措辞的门在 test_panel_speed）
    assert HTML.count('aria-label="全选本页筛出的岗位"') == 1
    assert 'aria-label="勾选这条岗位"' in HTML


def test_筛空时说清是筛选没命中_不是池子空():
    """池里有货、只是这组条件一条都过不了：再说「池子还是空的」就是撒谎，
    他会以为抓取腿没干活。两条路都要有（筛空 ← 筛选栏；真空 ← 去启动）。"""
    body = _body("renderJobPool")
    assert "poolFilterOn()" in body, "空表时没分清「筛空」和「池空」"
    assert "没有符合当前筛选的岗位" in body, "筛空时没有这句实话"
    assert "重置" in body, "筛空时没告诉他怎么回到全部"
    assert "这个号的池子还是空的" in body or "池子都还是空的" in body, \
        "真·空池提示被一起删了"
    assert body.index("poolFilterOn()") < body.index("没有符合当前筛选"), \
        "顺序反了：先按池空写死再判断，等于没判断"


def test_池空判定只认筛选不认排序():
    """排序永远有个值：把它算进筛选条件，没筛任何东西也会说「没有符合当前筛选」。
    逐列那几格现在由 POOL_FILTERS 一张表管着——判据必须跟着表走，
    不然新加一格筛了东西，空表那句「没有符合当前筛选」却不说（逐格的门在
    tests/test_job_pool_filters.py）。"""
    body = _body("poolFilterOn")
    assert "POOL_FILTERS.some" in body, "「有没有在筛」没跟着那张表：新加的一格筛空了也不解释"
    assert "poolFilterSort" not in body, "排序被当成筛选条件，全部账号下永远显示筛空"


def test_换数据范围先把勾撤掉():
    """池的勾跟着行走：范围一换屏上就是另一批行，上一批的勾再留着，
    「删除选中的」就对着他看不见的行动手。必须在重画之前清。"""
    body = _body("setDataScope")
    assert "poolSelected = {}" in body, "换范围不清勾，旧勾会跟着到新范围"
    assert "poolLastCheckIdx = -1" in body, "换范围还留着 Shift 锚点，下一次连选会跨范围"
    assert "poolSyncSelection()" in body, "清了内存没刷新界面，勾选框和计数还是旧的"
    assert body.index("poolSelected = {}") < body.index("applyScopeToView()"), \
        "清勾排在重拉之后：新表已经按旧勾画过一遍"


def test_理由格必须还是表格单元():
    """他截图那团「悬停的空方块」就在这一列：display:-webkit-box 挂到 <td> 上，
    这一格就不再是表格单元，高度只剩文本那两行；行悬停时整列空出一块没被涂到的
    深色方块（实测像素 (35,39,58)，同一行别处是 (54,58,75)）。
    两行截断要挪进里面的 div，td 的 white-space:nowrap 也得在内层改回 normal。"""
    td规则 = _规则(".pool-reason")
    assert "display:-webkit-box" not in td规则, "两行截断又挂回 <td> 上了：格子会缩成文本高度"
    assert "-webkit-line-clamp" not in td规则, "截断属性挂在 td 上，td 就不是表格单元了"
    内层 = _规则(".pool-reason>div")
    assert "display:-webkit-box" in 内层 and re.search(r"-webkit-line-clamp:\s*2", 内层), \
        "两行截断丢了（长理由会铺满整行）"
    assert "white-space:normal" in 内层, \
        "td 的 white-space:nowrap 会继承下来，内层不改回 normal 就永远只有一行"


def test_理由文本画在内层div里():
    """CSS 挂在内层、DOM 里却没有那个 div 的话，截断和换行一条都不生效；
    标题还留在格子上，鼠标停住仍能看全文。"""
    起 = HTML.index("'<td class=\"pool-reason\"")
    格 = HTML[起:起 + 400]
    assert "</td>'" in 格, "理由格在这 400 字里都没收口，结构变了"
    格 = 格[:格.index("</td>'") + len("</td>'")]
    assert 'title="' in 格, "理由格没有 title，全文就再也看不到了"
    assert '"><div>' in 格, "理由文本没包在被 .pool-reason>div 选中的 div 里"
    assert 格.rstrip().endswith("</div></td>'"), "理由格的 div 没闭合，后面的列会全被吞进去"
