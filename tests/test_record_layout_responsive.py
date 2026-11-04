"""记录页响应式的三条硬规则。

这些值没有别的出口：CSS 里少一条，只有把窗口拖窄才看得出来（表被压成 0 高、
筛选栏把 tab 条撑成 244px），pytest 全绿也发现不了。所以按源码锁死。

实测（临时面板 + 无头 cloakbrowser，1680/1280/1024/768/480 五档）：
  修前 1024 记录区高 140px、600 以下 0px；修后 269 / 254px，五档都不横向溢出。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")


def 收白(s: str) -> str:
    """换行和缩进折叠成一个空格，比较时不用管排版"""
    return re.sub(r"\s+", " ", s)


def 取媒体块(上限: int, 源码: str = "") -> str:
    """按花括号配平取出这一档，避免把隔壁档的规则当成本档的"""
    平 = 收白(源码 or HTML)
    头 = "@media screen and (max-width: %dpx) {" % 上限
    i = 平.index(头)
    j = i + len(头)
    depth = 1
    while depth:
        if 平[j] == "{":
            depth += 1
        elif 平[j] == "}":
            depth -= 1
        j += 1
    return 平[i:j]


def 缺(块: str, 需要) -> list:
    return [r for r in 需要 if r not in 块]


BANNED = [
    # 统计卡 + 操作栏一折行就把记录面板挤成 0 高
    ".content { overflow-y: auto; overflow-x: hidden; }",
    ".panel-row { flex: 0 0 auto; }",
    ".panel-full { min-height: 460px; }",
]


class 窄窗记录区Test:
    def test_768档让内容区纵向滚并给记录卡留高度(self):
        assert 缺(取媒体块(768), BANNED) == [], "窄窗又会被压成 0 高"

    def test_768档排在基础规则后面(self):
        # 同优先级靠顺序生效：放到 .content{overflow:hidden} 前面就等于没写
        平 = 收白(HTML)
        assert 平.index("@media screen and (max-width: 768px) {") > \
            平.index(".content{flex:1;overflow:hidden;")

    def test_1200档把筛选栏挪出tab那一行(self):
        assert 缺(取媒体块(1200), [
            ".record-tabs { flex-wrap: wrap !important;",
            "#greetFilterBar, #replyFilterBar, #poolFilterBar { flex: 1 1 100% !important;",
        ]) == []
        # tab 那行是行内 style（display:flex;…;flex-wrap:nowrap），
        # 不加 !important 这条规则永远输
        assert "flex-wrap:nowrap" in HTML
        assert "flex-wrap: wrap !important" in 取媒体块(1200)

    def test_三张表各有min_width且记录区能滚(self):
        平 = 收白(HTML)
        for 规则 in ("#greetTable{min-width:980px}", "#contactTable{min-width:1200px}",
                     "#poolTable{min-width:1440px}",
                     ".record-tab-content{display:none;flex:1;overflow:auto}"):
            assert 规则 in 平, 规则


class 门自身Test:
    def test_缺一条就判红(self):
        """把规则改回旧写法，门必须报出来——不然它只是摆设"""
        坏 = HTML.replace(".content { overflow-y: auto; overflow-x: hidden; }",
                          ".content { overflow-y: hidden; overflow-x: hidden; }")
        assert 坏 != HTML, "变异没落到源码上"
        assert 缺(取媒体块(768, 坏), BANNED), "变异没被抓住"


# ── 列宽：富余只许给"字最长的那一列" ─────────────────────────────────────────
# 2026-10-31 他指着投递记录说「岗位那里空这么多」。那张表 `table-layout:fixed`，
# 只有"岗位"一列没写宽度，于是容器比固定列宽之和多出来的部分**全归它**：
# 1920 窗口下实测岗位列 210~335px，而那一列正文中位只有 92px、p90 210px；
# 同一张表里真正会长的是"跳过原因"（中位 185px、最长 964px），却被钉死 250px 换行。
# 台账那张更明显：吃富余的是"号码 / 微信号"（一串手机号 ~130px），
# 而"依据原话"被钉 240px。
# 规则因此是两条：① 每一列都写宽度，只有长文本列不写（由它吃富余）；
# ② 岗位/公司这类中等长度的列收窄并允许换两行（.cell-wrap），别拿空白换一行不截字。
长文本列 = ("跳过原因", "判分理由", "依据原话")


def css_定了宽的类(源码: str = None):
    """像 `.greet-table th.pool-sel{width:44px}` 这种在 CSS 里给过宽度的类名。
    勾选列没写行内 width，靠的就是这一条，不能算成"吃富余的列"。"""
    平 = 收白(源码 if 源码 is not None else HTML)
    集 = set()
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", 平):
        if re.search(r"(?:^|[;\s])width:", m.group(2)):
            集.update(re.findall(r"\.([\w-]+)", m.group(1)))
    return 集


def css_类宽度(源码: str = None):
    """类名 → CSS 里给它设的 px 宽度（同一个类多处出现取最大）。"""
    平 = 收白(源码 if 源码 is not None else HTML)
    表 = {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", 平):
        值 = re.search(r"(?:^|[;\s])width:\s*(\d+(?:\.\d+)?)px", m.group(2))
        if not 值:
            continue
        for 类 in re.findall(r"\.([\w-]+)", m.group(1)):
            表[类] = max(表.get(类, 0), float(值.group(1)))
    return 表


def 首行单元格(表id: str, 源码: str = None):
    """取 thead 里第一行 `<tr>` 的每个 `<th>`（属性段 + 纯文字）。

    两个坑：① `<thead>` 自己就以 `<th` 开头，正则必须要求 `<th` 后面跟空格或 `>`；
    ② `table-layout:fixed` 下列宽只由第一行决定，岗位池那张表的第二行是逐列筛选框，
    把它们当"没设宽度的列"数进来就是虚报。"""
    平 = 收白(源码 if 源码 is not None else HTML)
    段 = 平[平.index('id="%s"' % 表id):]
    段 = 段[段.index("<thead>") + len("<thead>"):段.index("</thead>")]
    行 = 段[段.index("<tr"):段.index("</tr>")]
    元组 = []
    for m in re.finditer(r"<th(\s[^>]*)?>(.*?)</th>", 行):
        属性 = m.group(1) or ""
        文字 = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        元组.append((属性, 文字))
    return 元组


def 列宽像素(表id: str, 源码: str = None):
    """列名 → 宽度 px；没宽度的（=吃富余的）记成 None。"""
    类宽 = css_类宽度(源码)
    出 = []
    for 属性, 文字 in 首行单元格(表id, 源码):
        行内 = re.search(r"width:\s*(\d+(?:\.\d+)?)px", 属性)
        类 = re.search(r'class="([^"]*)"', 属性)
        借 = [类宽[c] for c in (类.group(1).split() if 类 else []) if c in 类宽]
        出.append((文字, float(行内.group(1)) if 行内 else (max(借) if 借 else None)))
    return 出


def 不设宽度的列(表id: str, 源码: str = None):
    return [名 for 名, 宽 in 列宽像素(表id, 源码) if 宽 is None]


def 最小宽度(表id: str, 源码: str = None) -> float:
    m = re.search(r"#%s\s*\{[^{}]*?min-width:\s*(\d+(?:\.\d+)?)px" % 表id,
                  收白(源码 if 源码 is not None else HTML))
    assert m, "%s 没有 min-width" % 表id
    return float(m.group(1))


class 列宽Test:
    # 每张表"吃容器富余"的那几列，逐张钉死：其余列一律有宽度（行内或 CSS 类）
    该吃富余的列 = {
        "greetTable": ["跳过原因"],
        "contactTable": ["依据原话"],
        "poolTable": ["岗位", "判分理由"],
    }

    def test_吃富余的列逐张钉死(self):
        for 表, 期望 in self.该吃富余的列.items():
            光 = 不设宽度的列(表)
            assert 光 == 期望, "%s 分富余的列是 %s，期望 %s" % (表, 光, 期望)

    def test_吃富余的列必须是长文本列(self):
        for 表, 光 in self.该吃富余的列.items():
            assert any(x in 长文本列 for x in 光), \
                "%s 吃富余的是 %s，没有一列长文本列" % (表, 光)

    def test_最窄档下吃富余的列每列至少200px(self):
        """列宽与 min-width 是相互约束的两个数：固定列加起来越接近 min-width，
        富余越少，岗位池那张表就会把"判分理由"折成一竖条。这条把两者钉在一起。"""
        for 表, 光 in self.该吃富余的列.items():
            已定 = sum(宽 for _, 宽 in 列宽像素(表) if 宽 is not None)
            每列 = (最小宽度(表) - 已定) / len(光)
            assert 每列 >= 200, "%s 固定列合计 %s，min-width %s，摊到 %s 每列只剩 %spx" \
                % (表, 已定, 最小宽度(表), "、".join(光), 每列)

    def test_收窄的岗位公司列要能换两行(self):
        """列宽从 210/132 收到 150/140 的前提是这两格允许换行——
        td 本身是 nowrap+ellipsis，没有 .cell-wrap 就变成"少一大片空、多一屏省略号"。
        四格：投递记录的岗位/公司、台账的岗位/依据原话。"""
        assert ".cell-wrap>div{" in 收白(HTML)
        挂上的 = re.findall(r'<td[^>]*class="[^"]*\bcell-wrap\b', HTML)
        assert len(挂上的) == 4, "现挂 cell-wrap 的 td 是 %d 处：%s" % (len(挂上的), 挂上的)

    def test_勾选列靠css宽度不吃富余(self):
        """岗位池第一格是复选框，没有行内 width。它必须被 CSS 里的 44px 认下来，
        否则这条门会把它误当成"吃富余的列"，也说明真布局里它确实在抢宽度。"""
        assert "pool-sel" in css_定了宽的类()
        assert css_类宽度().get("pool-sel") == 44.0

    def test_最小宽度每张表只许写一处(self):
        """10-31 动列宽时发现 #poolTable 的 min-width 有两条（1180 和 1240），
        前一条被后一条盖掉、谁也看不出。两处数字一改就只剩一处生效，所以钉成一条。"""
        平 = 收白(HTML)
        for 表 in self.该吃富余的列:
            n = len(re.findall(r"#%s\s*\{[^{}]*min-width:" % 表, 平))
            assert n == 1, "#%s 的 min-width 写了 %d 处" % (表, n)

    def test_门自身能红(self):
        """把投递记录退回旧写法（岗位不写宽度、跳过原因钉死），两条门都要报红。"""
        坏 = HTML.replace('<th scope="col" style="width:150px">岗位</th>', '<th>岗位</th>', 1)
        坏 = 坏.replace('<th scope="col">跳过原因</th>', '<th scope="col" style="width:250px">跳过原因</th>', 1)
        assert 坏 != HTML and "width:250px" in 坏, "变异没落到源码上"
        assert 不设宽度的列("greetTable", 坏) == ["岗位"]
        # 反向：给短列不设宽度也要抓住
        坏2 = HTML.replace('<th scope="col" style="width:108px">城市</th>', '<th>城市</th>', 1)
        assert 坏2 != HTML, "变异没落到源码上"
        assert 不设宽度的列("greetTable", 坏2) == ["城市", "跳过原因"]
        # 把岗位池 min-width 改回 1240（固定列 1028 不变）→ 每列只剩 106px，那条门要判红
        坏3 = HTML.replace("#poolTable{min-width:1440px}", "#poolTable{min-width:1240px}")
        assert 坏3 != HTML, "变异没落到源码上"
        每列 = (最小宽度("poolTable", 坏3)
                - sum(宽 for _, 宽 in 列宽像素("poolTable", 坏3) if 宽 is not None)) / 2
        assert 每列 < 200, "变异没让那条门变红：%s" % 每列
