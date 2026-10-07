# -*- coding: utf-8 -*-
"""硬否决：有些岗位不是"分低"，是压根不该主动去沟通。

使用者 2026-10-07 的原话："有些主播销售，工厂啥的直接拒绝就行"、
"我只要线上的兼职，遇到线下的直接拒绝"。这条判据在投递之前就得分出来——
发完招呼语再拒，HR 已经收到一条他不想要的消息。

为什么做成脚本而不是写在 SKILL.md 里让 agent 自己看：三条口径光靠文字描述容易走样，
而每走样一次都是真发出去的一条消息。
1. **岗位类型词只查标题**，不查 JD 正文——"要标注快递场景录音"是我们自己要的数据标注岗，
   量正文就把它杀了；
2. **用户自己的否决词查标题也查正文**，但**认否定式**——
   "（线上）不坐班""无需坐班""不需要坐班"是在保证没这条限制，不是在提这条限制；
3. **岗位类型词不看否定式**——"不露脸主播""无需露脸"都还是主播，"直招非中介"更是派遣岗的卖点。
另外 BOSS 会把敏感字拆开写（"免 费 提 供电动车食住"、"哈⁢啰⁢出⁢行"中间还有不可见字符），
匹配前先抹掉这些，否则一条都匹不上。

跑法：
    python scripts/veto.py check --title "普工包吃住 18 元一小时"
    python scripts/veto.py check --title "线上数据标注（不坐班）" --jd "按件结算，居家完成"
    echo '{"title":"...","jd":"..."}' | python scripts/veto.py check --stdin
"""
import argparse
import json
import re
import sys

# 岗位类型词（只查标题）。取的是盘上真出现过的标题形状，不是凭空想的分类学。
DEFAULT_TITLE_VETO = (
    "普工", "操作工", "技工", "焊工", "钳工", "学徒工", "工厂", "进厂", "车间",
    "流水线", "电子厂", "汽配厂", "分拣", "打包", "装卸", "贴标", "仓管",
    "倒班", "两班倒", "白班", "夜班", "坐岗", "小时工", "临时工", "派遣职位",
    "主播", "主包", "直播", "互动播", "团播", "带播", "口播", "带货", "语音厅",
    "场控", "露脸", "陪聊", "情感互动", "聊天室",
    "快递", "驿站", "骑手", "外卖", "配送", "跑腿", "司机", "代驾", "网约车",
    "电动车", "站点直招",
    "保洁", "环卫", "保安", "门卫", "店员", "导购", "服务员", "收银", "传菜",
    "洗碗", "保姆", "月嫂", "钟点工", "足疗", "按摩", "KTV",
    "销售", "电销", "电话销售", "课程顾问", "教育顾问", "招生",
)
# 标题里同时出现这些职业词就放行："直播运营助理""电商客服""电动车数据标注"
# 是挂着类型皮的线上活，是使用者点名要的方向。
# 刻意不放"线上/居家/兼职"——每个兼职标题都写着它们，放进去等于这张表失效。
DEFAULT_EXEMPT = (
    "客服", "运营", "助理", "标注", "数据", "分析", "剪辑", "后期", "设计",
    "文案", "翻译", "审核", "录入", "资料", "文员", "会计", "程序", "代码",
    "测试", "建模", "画图", "策划", "编辑", "招聘", "代练", "打手", "陪玩",
)
# 用户自己填的否决词（标题和正文都查，认否定式）。默认给最常见的几种"要到场"说法。
DEFAULT_BODY_VETO = (
    "仅线下", "不接受远程", "需坐班", "坐班打卡", "线下驻场", "驻场", "外勤",
    "到店", "需到岗", "到岗", "到公司办公", "线下办公", "线下培训", "到岗面试",
    "包吃住", "包吃包住", "住宿", "中介", "地推", "刷单", "打字员", "手工活",
    "无门槛", "零门槛",
)

_SEP_RE = re.compile(r"[\s ᠎‍-‏⁠-⁯﻿]+")
_NEGATED_RE = re.compile(r"[不无没][得有要需用不没]{0,2}$")
_NEGATED_CLAUSE_RE = re.compile(r"^[不无没][得有要需用]")
_CLAUSE_SEPS = "，,。.;；、!！?？:：\n（）()【】[]/\\|｜~～ "


def tight(text: str) -> str:
    """抹掉空白与不可见分隔符：BOSS 会把敏感字拆开写。"""
    return _SEP_RE.sub("", str(text or ""))


def _negated(body: str, idx: int) -> bool:
    if _NEGATED_RE.search(body[:idx]):
        return True
    start = max([body.rfind(sep, 0, idx) for sep in _CLAUSE_SEPS] + [-1]) + 1
    return bool(_NEGATED_CLAUSE_RE.match(body[start:idx]))


def keyword_hit(words, text: str, honor_negation: bool = True) -> str:
    """返回命中的那一条词（没命中返回空串）。

    honor_negation=False 给岗位类型词用：那里的否定式不改变岗位性质。
    同一个词出现两次（"不用坐班，但周末要坐班"）逐处看，有一处不是否定式就算命中。
    """
    body = tight(text)
    if not body or not words:
        return ""
    for kw in words:
        word = tight(kw)
        if not word:
            continue
        start = 0
        while True:
            i = body.find(word, start)
            if i < 0:
                break
            if not honor_negation or not _negated(body, i):
                return str(kw).strip()
            start = i + len(word)
    return ""


def title_veto_hit(title: str, words=DEFAULT_TITLE_VETO) -> str:
    """岗位标题里的岗位类型词，命中返回那一条；带职业白名单词就放行。"""
    body = tight(title)
    if not body:
        return ""
    if any(tight(w) in body for w in DEFAULT_EXEMPT):
        return ""
    return keyword_hit(words, body, honor_negation=False)


def check(title: str = "", jd: str = "", rules: dict = None) -> dict:
    """这一单该不该主动去沟通。veto=True 就别发招呼语，直接落 skipped_hard_veto。"""
    rules = rules or {}
    类型词 = tuple(rules.get("reject_title_keywords") or DEFAULT_TITLE_VETO)
    否决词 = tuple(rules.get("reject_keywords") or DEFAULT_BODY_VETO)
    hit = title_veto_hit(title, 类型词)
    where = "title_type"
    if not hit:
        hit = keyword_hit(否决词, title)
        where = "title_veto"
    if not hit:
        hit = keyword_hit(否决词, jd)
        where = "jd_veto"
    return {"veto": bool(hit), "hit": hit, "where": where if hit else "",
            "title": title, "为什么": {
                "title_type": "标题写着岗位类型本身（普工/主播/快递/保洁/销售这一类）",
                "title_veto": "标题写着使用者不要的条件（坐班/包吃住/到场这一类）",
                "jd_veto": "JD 正文写着使用者不要的条件",
            }.get(where, "")}


def _load_rules(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--title", default="")
    ap.add_argument("--jd", default="")
    ap.add_argument("--rules", default="")
    ap.add_argument("--stdin", action="store_true",
                    help="从标准输入读 {\"title\":...,\"jd\":...}")
    a = ap.parse_args(argv)
    title, jd = a.title, a.jd
    if a.stdin:
        data = json.loads(sys.stdin.read() or "{}")
        title = data.get("title", title)
        jd = data.get("jd", jd)
    rules = _load_rules(a.rules) if a.rules else {}
    print(json.dumps(check(title, jd, rules), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
