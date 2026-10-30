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
                     "#poolTable{min-width:1240px}",
                     ".record-tab-content{display:none;flex:1;overflow:auto}"):
            assert 规则 in 平, 规则


class 门自身Test:
    def test_缺一条就判红(self):
        """把规则改回旧写法，门必须报出来——不然它只是摆设"""
        坏 = HTML.replace(".content { overflow-y: auto; overflow-x: hidden; }",
                          ".content { overflow-y: hidden; overflow-x: hidden; }")
        assert 坏 != HTML, "变异没落到源码上"
        assert 缺(取媒体块(768, 坏), BANNED), "变异没被抓住"
