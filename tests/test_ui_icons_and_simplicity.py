# -*- coding: utf-8 -*-
"""这一屏"图标太小 / 左栏字太多 / BOSS 视图没换 svg"的源码锁。

用户 2026-10-04 原话："svg 图标太小了，而且 Boss 直聘端的那边没有换成 svg，
还有就是左边配置栏的文字太多了…或者把它默认隐藏起来，不然看起来不够简洁"。
真机像素由 tools/e2e_dashboard.py 量，这里只锁"改回去就红"的形状。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
SPRITE = (ROOT / "flask-version" / "static" / "icons" / "ui-sprite.svg").read_text(encoding="utf-8")


def _css(rule):
    m = re.search(r"\n" + re.escape(rule) + r"\{([^}]*)\}", HTML)
    assert m, f"样式表里已经没有 {rule}{{}} 了"
    return m.group(1)


def test_图标有像素下限而不是只跟字号():
    """图标一律 1em，而这一屏大量标签只有 9.5~12px —— 图标就被字号拖到看不清。
    下限写在 .ki 上，比"逐个内联放大"少漏一处。"""
    body = _css(".ki")
    assert "min-width" in body and "min-height" in body, body
    assert re.search(r"min-(width|height):\s*1[45]px", body), f"下限没到 14px: {body}"


def test_没有只剩占位作用的空图标规则():
    """.ki-lg 以前和 .ki 一模一样（1em），写着"大号"其实一点没放大，
    留着只会让人以为改它有用。要么真放大，要么删掉。"""
    assert ".ki-lg{" not in HTML


def test_轻提示的图标颜色不再挂在已经消失的i标签上():
    """<i data-ki> 换成 <svg class=ki> 之后 `.toast i{color:...}` 一条都不中，
    成功/失败提示的图标颜色全退回正文色。"""
    assert ".toast i{" not in HTML
    assert ".toast.ok .ki{" in HTML and ".greet-alert .ki{" in HTML


def test_boss消息视图里的文字占位都换成了图标():
    """回复记录是照着 BOSS 消息界面 1:1 画的，这几处还挂着纯文字/方括号，
    用户一眼就看出"那边没换成 svg"。"""
    assert '<i data-ki="reset"></i>刷新' in HTML
    assert "[图片]" not in HTML
    assert '<i data-ki="image"></i> 图片' in HTML
    assert '<i data-ki="briefcase"></i> 职位卡片' in HTML
    assert '<i data-ki="check-circle"></i>已读' in HTML


def test_雪碧图里真有模板引用的新符号():
    """模板写了 data-ki="image" 而雪碧图没这个 symbol，页面上就是一个空洞——
    比原来的方括号更难看。生成器和模板要一起改。"""
    for sym in ("i-image", "i-briefcase"):
        assert f'id="{sym}"' in SPRITE, f"ui-sprite.svg 缺 {sym}，跑一遍 tools/build_ui_icons.py"


def test_左栏长说明默认收起():
    """配置预览整份摊开会把要填的表单挤出首屏；按号独立/全局共享那六大行同理：
    问的人少，占的地方大。两处都要默认收起、点标题才展开。"""
    assert 'id="configPreviewList" class="notif-list" style="display:none' in HTML
    assert 'class="adv-section" id="scopeNoteBox"' in HTML
    assert 'class="adv-section open" id="scopeNoteBox"' not in HTML
    assert "toggleBlock(" in HTML, "折叠没有通用开关函数"


def test_来源标签不会把内部键念给用户():
    """截图里出现过 scam_filter 直接印在气泡旁边：盘上真实存在的来源必须有中文名，
    认不出的也不能原样回显。"""
    body = HTML[HTML.index("function getReplySourceLabel"):HTML.index("function getReplySourceClass")]
    assert "scam_filter" in body and "backfill" in body
    assert "return replySource;" not in body, "兜底还在原样回显内部键"
    # 筛选下拉要能筛到这几类，否则记录存在却查不出来
    for opt in ('value="scam_filter"', 'value="backfill"', 'value="rejection"'):
        assert opt in HTML, f"来源筛选缺 {opt}"


def test_首帧就按上次的主题画():
    """HTML 写死 light、深色要等 /api/config 回来才改，默认深色的人每次开屏先白一下。
    预置脚本必须在 </head> 之前，晚了就等于没有。"""
    head = HTML[:HTML.index("</head>")]
    assert "localStorage.getItem('boss-theme')" in head
    assert re.search(r"<script>\s*//[^\n]*\ntry\{document\.documentElement", head), \
        "主题预置脚本不在了"


# ── 2026-10-04 第二轮："你自己看你左边的配置栏乱成啥样了" ──

def test_左栏字段行是两列grid():
    """标签按自然宽度排，一栏里就量出 8 种控件左边缘（真机 80/87/98/104/108/109/115/125px）。
    两列 grid 才能不管窗口多宽都对到同一条竖线上。"""
    for rule in (".acc-field-row", ".adv-field-row"):
        body = _css(rule)
        assert "display:grid" in body, f"{rule} 又退回 flex 了：{body}"
        assert "var(--side-label-w)" in body, f"{rule} 的标签列没走变量：{body}"
        assert "minmax(0,1fr)" in body, f"{rule} 的控件列会被长内容撑破：{body}"


def test_说明行横跨两列而不是挤在复选框后面():
    """.side-hint 跟在复选框后面时只有 88px 宽，一句短话排成三行。"""
    assert re.search(r"\.acc-field-row>\.side-hint[^{]*\{[^}]*grid-column:1/-1", HTML), \
        "说明行没横跨两列"


def test_侧栏宽度收在变量里():
    """以前 @media 里的 .side{width:280px} 被 flex-basis:360px 压住，从不生效。"""
    body = _css(".side")
    assert "var(--side-w)" in body, body
    assert "@media(max-width:1180px){:root{--side-w:" in HTML, "窄窗口没有收侧栏这一档"
    assert ".side{width:280px}" not in HTML, "又写回那条被 flex-basis 压住的死规则了"


def test_logo用的是图标库里的图形():
    """左上角以前是手写的 rect+加号：既不是图标库画风，也说明不了这是个招聘工具。"""
    assert '<div class="logo-wrap"><i data-ki="briefcase"></i></div>' in HTML
    assert '<rect x="2" y="3"' not in HTML, "手写的占位 svg 又回来了"


def test_启动按钮的文字只拼一处():
    """四处各写一遍 innerHTML，漏一处图标就从 play 变成 chevron（真就是这样跑了一周）。"""
    assert HTML.count("runBtnHtml(") >= 6, "又有人直接给启动按钮拼 innerHTML 了"
    assert 'chevron-right" style="font-size:12px"></i> 启动' not in HTML


def test_运行控制是胶囊不是实心色块():
    """实心绿块/红块和旁边一排玻璃按钮完全不搭（用户："停止和开启的这个UI不太好看"）。"""
    body = _css(".btn-run")
    assert "border-radius:999px" in body, body
    assert 'class="btn-run" id="btnStartAll"' in HTML
    assert 'class="btn-run stop" id="btnStopAll"' in HTML
    for bid in ("btnPauseGreet", "btnResumeGreet", "btnPauseReply", "btnResumeReply"):
        line = [ln for ln in HTML.splitlines() if f'id="{bid}"' in ln and "<button" in ln]
        assert line and "btn-tint" in line[0], f"{bid} 还是整块实心色"
        assert "btn-success" not in line[0] and "btn-warn" not in line[0], line[0]


def test_改按钮文字不吞掉图标():
    """updatePauseButtons 以前给整颗按钮赋 textContent，前面的 <svg> 图标一起被抹掉。"""
    body = HTML[HTML.index("function updatePauseButtons"):HTML.index("// ── 下载回复/打招呼记录")]
    assert "querySelector('.btn-label')" in body
    assert "b.textContent =" not in body, "又在整颗按钮上赋 textContent"
    assert HTML.count('<span class="btn-label">') >= 4


def test_导出图标不是宽扁的透视桌():
    """table 那张画布是 247x146，缩到 14px 只剩几道竖线，看着不像表格。"""
    m = re.search(r'id="i-export"[^>]*viewBox="0 0 ([\d.]+) ([\d.]+)"', SPRITE)
    assert m, "雪碧图里没有 i-export"
    w, h = float(m.group(1)), float(m.group(2))
    assert 0 < w <= h, f"导出图标画布 {w}x{h} 还是横扁的"
    src = (ROOT / "tools" / "build_ui_icons.py").read_text(encoding="utf-8")
    assert '"export": "table"' not in src


def test_全部账号下撞车身份要挂账号标记():
    """同一个 姓名+公司 可能是两个号各自的对话（实测 陆女士@深圳小智两号都在聊）。
    「全部账号」列表里两行长得完全一样，点哪条全凭运气——撞车的身份必须标出号几。"""
    body = HTML[HTML.index("function renderBossChatList"):HTML.index("// 会话身份：BOSS 侧栏一行显示")]
    assert "boss-chat-item-acc" in body, "列表行里没有账号标记"
    assert "dataScope === 'all'" in body, "标记不分范围，单账号下也会挂出噪声"
    assert ".boss-chat-item-acc{" in HTML, "账号标记没有样式，会跟正文糊在一起"
