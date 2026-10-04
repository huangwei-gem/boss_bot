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
