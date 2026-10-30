# -*- coding: utf-8 -*-
"""岗位候选池界面的源码锁（2026-10-19 定的「先抓岗位、再选择性投递」）。

这一层的毛病全是"点了没反应"那一类：tab 切过去是空的、勾选投递其实没投、
轮询把正在打的备注冲掉、改错了号。JS 跑不了单测，所以按本仓库既有做法锁形状，
改回去就红；真机点击另有一套（tests/e2e_job_pool_ui.py）。
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


def test_池tab接进切换函数():
    """只加按钮不接 switchRecordTab，点上去 tab 高亮了但表永远不出来"""
    body = _body("switchRecordTab")
    assert "pool: 'poolFilterBar'" in body, "筛选栏没接进切换：切到池 tab 时筛选栏不出现"
    assert "loadJobPool(true)" in body and "tab === 'pool'" in body, "切过去不拉数据"
    assert "loadAiScreenDoc()" in body, "切到池 tab 时不读 AI 筛选要求，那个框会一直是空的"
    assert "tab === 'pool'" in body, "「清空」按钮在池 tab 下还留着 —— 点下去清的是回复记录"


def test_换数据范围一定重拉池():
    """池按号分开存文件，范围换了不重拉，屏上还是上一个号的池"""
    body = _body("applyScopeToView")
    assert "if (poolTabActive()) loadJobPool(true);" in body
    assert "poolTabActive" in body


def test_表头列数与渲染的格子数对得上():
    """少一格就整行错位：他看到的薪资其实是公司"""
    th = re.search(r'id="poolTable".*?</thead>', HTML, re.S).group(0)
    第一行 = th[:th.index("</tr>")]
    列数 = len(re.findall(r"<th[\s>]", 第一行))      # 只数标题那一行，<thead> 不算
    格数 = _body("renderJobPool").count("+ '<td")
    assert 列数 == 格数, f"表头 {列数} 列，行只画 {格数} 格"


def test_筛选行与标题行一样宽():
    """逐列筛选那一行插在同一个 <thead> 里：少一格整张表错位，
    他填的"薪资"其实是"公司"那一列的框"""
    th = re.search(r'id="poolTable".*?</thead>', HTML, re.S).group(0)
    标题行 = th[:th.index("</tr>")]
    筛选 = re.search(r'<tr class="pool-filter-row">.*?</tr>', th, re.S).group(0)
    列数 = len(re.findall(r"<th[\s>]", 标题行))
    格数 = len(re.findall(r"<th[\s>]", 筛选))
    assert 格数 == 列数, f"标题 {列数} 列，筛选行只有 {格数} 格"
    assert 'class="pool-filter-row"' in th, "筛选行没挂在 thead 里，轮询重画 tbody 会把它一起冲掉"


def test_勾选投递只写状态不动浏览器():
    """HTTP 线程当场去点开详情页，会和抓取线程抢同一张搜索页（这条修过一轮）"""
    body = _body("poolBatch")
    assert "/api/job_pool/batch" in body
    assert "selected" in body
    for 禁 in ("send_greeting", "navigate", "location.href"):
        assert 禁 not in body, f"池接口里不许有浏览器动作：{禁}"


def test_没启动时勾选要说清还没投():
    """投出去是打招呼线程下一轮的事。不说明这一层，就是「我点了它没动」。"""
    body = _body("poolBatch")
    assert "scopeRunning()" in body
    assert "没启动" in body


def test_所有写接口都带上是哪一个号():
    """默认成 0 的话，他看着账号2 却把勾写进主账号的池 —— 两个号岗位名一样，发现不了"""
    assert "payload.account = 号" in _body("poolPost")
    assert "poolAccount()" in _body("poolPost")
    assert "dataScope === 'all' ? null" in _body("poolAccount")
    # 「全部账号」是把两号的池并成一张表来看（开机就停在这个范围上）：
    # 写回时用这一行自己的号，不许拿全局范围去猜
    assert "dataScope === 'all' ? 'all'" in _body("loadJobPool")
    for 名 in ("poolRowStatus", "poolRowNote", "poolRowPin", "poolRowDelete"):
        assert "poolRowAccount(r)" in _body(名), f"{名} 写回没带上这一行自己的号"
    assert ".split('|')" in _body("poolGroupSelected"), "批量写按号拆开时用的是勾选键里的号"


def test_合并视图要认得出是哪个号():
    """两号的池并成一张表时，行上得标着号；勾选键也得带号（同一条链接 id 一样）"""
    assert "pool-acc-tag" in _body("renderJobPool")
    assert "dataScope === 'all'" in _body("renderJobPool")
    assert "String(poolRowAccount(r)) + '|'" in _body("poolRowKey"), "勾选键没带号，会勾中两个号里同 id 的那条"
    assert "每行标着是哪个号" in HTML, "池页说明要讲清合并视图的行是怎么认号的"


def test_轮询内容没变就不重画():
    """近三千行重建一次要好几秒：每 20 秒跳一次、还卡一下，就是他在看表时被挪走"""
    body = _body("loadJobPool")
    assert "poolRowsSig" in body and "poolShownSig" in body
    assert "要重画" in body
    assert "正在读取岗位池" in body, "几千条要读几秒，空白页面看着就像一条都没抓到"
    sig = _body("poolRowsSig")
    assert "last_seen_at" in sig, "抓到时那一列得按档位跟上，不然永远停在第一次画的那一版"
    assert "status" in sig and "note" in sig and "判于" in sig, "状态/备注/AI 结论变了必须重画"


def test_正在打字时不整块重画():
    """轮询把 innerHTML 重建一次，光标和已打的字就没了（这条他点名说过）"""
    assert "poolIsEditing()" in _body("loadJobPool")
    assert "poolBusy" in _body("loadJobPool")
    assert "addEventListener('focusout'" in HTML, "打完字离开这一格要补上被推迟的那次重画"
    # 只在池 tab 打开时才轮询，不然三个 tab 都在偷偷打接口
    assert re.search(r"if \(poolTabActive\(\)\) loadJobPool\(\)", HTML)


def test_删行要先问一句():
    """删掉会把他的备注一起没了；批量授权不等于同意删数据"""
    assert "confirm(" in _body("poolDeleteSelected")
    assert "confirm(" in _body("poolRowDelete")


def test_每一类改动都打回接口再重读():
    """改完不重读盘就报「已更新」，正是他骂过的那类「看着改了」。"""
    for 名 in ("poolRowStatus", "poolRowNote", "poolRowPin", "poolAddManual"):
        assert "/api/job_pool/" in _body(名), f"{名} 没打接口"
    assert "loadJobPool(true)" in _body("poolRowPin")


def test_备注没改字就别写盘():
    assert "新 === String(r.note || '')" in _body("poolRowNote"), "每次失焦都写一遍盘"


def test_只抓不投开关三端齐():
    """前端留个不生效的开关 = 假开关。四个出口都得有：输入框、回填、切换、保存"""
    assert 'id="advCaptureOnly"' in HTML
    assert "setEl('advCaptureOnly'" in _body("renderAdvSettings")
    assert '"capture_only"' in _body("toggleAdvBool")
    assert "config.greet.capture_only = document.getElementById('advCaptureOnly')" in _body("onAdvChange")


def test_精筛额度输入框三端齐():
    assert 'id="aiScreenMaxPerRound"' in HTML
    assert "setVal('aiScreenMaxPerRound'" in _body("renderAiConfig")
    assert "config.ai.screen_max_per_round" in _body("onAiChange")
    assert HTML.count("screen_max_per_round:40") == 2, "两处默认值（渲染兜底 + loadConfig 兜底）都得有"


def test_AI结论与本地粗筛都看得见():
    """他要的「AI 自定义筛选这些拿来的岗位」= 判了多少分、像不像、为什么，都得在行上"""
    ai = _body("poolAiCell")
    assert "未判" in ai, "没轮到的那条要说明是没判，不是 0 分"
    assert "接口" in ai and "否决" in ai
    gate = _body("poolGateCell")
    assert "方向命中" in gate and "没判过" in gate
    assert "词表没读到" in gate, "词表一份都没读到时不许画成「没判过」——那是两道不同的空"
    assert "标题词表为空" in gate, "标题那一遍没参与时要单独亮出来（10-22 两行家教/老师漏标的形状）"
    assert "pool-reason" in _body("renderJobPool")


def test_三千行不许逐个换图标节点():
    """近三千行 × 2 个图标：KIcon.createIcons 是逐个 <i> 换节点，实测整表多花好几秒、
    期间页面点不动（10-28 线上实测最长一次卡 8.4 秒）。行内图标改成 innerHTML 里
    直接出成品 svg，并让屏外的行跳过排版；退回逐个换节点这条会红。"""
    body = _body("renderJobPool")
    assert "poolIcon('link')" in body and "poolIcon('delete')" in body
    assert "data-ki" not in body, "行里又出现 <i data-ki> 了：三千行会被逐个替换，卡几秒"
    assert "KIcon.createIcons" not in body
    assert "#i-" in _body("poolIcon"), "成品 svg 要指向本地雪碧图的 symbol"
    assert re.search(r"#poolTableBody tr\s*\{[^}]*content-visibility:\s*auto", HTML), \
        "屏外的行要跳过排版，否则整表一次排版几秒"


def test_三千行分批铺不许一次卡住():
    """整表一次性 insert，三千行排版实测长任务 6.5~9 秒，期间页面点不动。
    先铺头一批、其余趁空闲补；滚动容器是 tab 内容区（window 根本不滚）。"""
    body = _body("renderJobPool")
    assert "requestIdleCallback" in body, "剩下的行要趁空闲补，不许卡着主线程"
    assert "insertAdjacentHTML('beforeend'" in body
    assert "poolRenderGen" in body, "重画时要能叫停上一批，否则会贴出重复行"
    assert "poolTabContent" in body and "scrollTop" in body, "要还的是那个容器的位置"
    assert "window.scrollTo" not in _body("loadJobPool"), "window 不是滚动容器，存它等于没存"
