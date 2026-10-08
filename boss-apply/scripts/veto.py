# -*- coding: utf-8 -*-
"""硬否决：有些岗位不是"分低"，是压根不该主动去沟通。

使用者 2026-10-07 的原话："有些主播销售，工厂啥的直接拒绝就行"、
"我只要线上的兼职，遇到线下的直接拒绝"。这条判据在投递之前就得分出来——
发完招呼语再拒，HR 已经收到一条他不想要的消息。

为什么做成脚本而不是写在 SKILL.md 里让 agent 自己看：三条口径光靠文字描述容易走样，
而每走样一次都是真发出去的一条消息。
1. **岗位类型词查标题；标题没交代岗位是什么时连 JD 一起查**——
   "要标注快递场景录音"是我们自己要的数据标注岗（标题写着"标注"，正文就不查类型词），
   而「山姆新仓开业大量招人」这种标题一个字没说岗位，性质全在 JD 里，
   只查标题就等于没判据（使用者 2026-10-08："你到底有没有解析岗位jd来判断是什么岗位啊"）；
2. **用户自己的否决词查标题也查正文**，但**认否定式**——
   "（线上）不坐班""无需坐班""不需要坐班"是在保证没这条限制，不是在提这条限制；
3. **岗位类型词不看否定式**——"不露脸主播""无需露脸"都还是主播，"直招非中介"更是派遣岗的卖点；
4. **合伙人/老师/家教这一族只查标题**——正文里"有带教老师一对一辅导"是培养机制，
   "招募项目合伙人"是招商话术，都不是这份工的岗位。
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
    "电动车", "站点直招", "站点", "电瓶车", "货运", "理货", "拣货", "搬运",
    "信贷", "贷款", "催收", "抵押", "放款", "信用卡推广",
    "保洁", "环卫", "保安", "门卫", "店员", "导购", "服务员", "收银", "传菜",
    "洗碗", "保姆", "月嫂", "钟点工", "足疗", "按摩", "KTV",
    "销售", "电销", "电话销售", "课程顾问", "教育顾问", "招生",
    "合伙人", "老师", "助教", "家教", "讲师", "速记", "伴读",
)
# 压过职业白名单的类型词：标题里再带"数据/分析"也不给放行。
# 「AI+数据合伙人」被"数据"放过一次，使用者原话是"这种合伙人的一看就是骗子不要"。
NO_EXEMPT = frozenset({"合伙人", "老师", "助教", "家教", "讲师", "速记", "伴读",
                       "主播", "主包", "团播", "语音厅", "陪聊", "情感互动"})
# 只在标题定罪的词：写进正文时它们在说别的——"有带教老师一对一辅导"是培养机制，
# "我们招募项目合伙人"是招商话术，都不是"这份工是老师/合伙人"。
TITLE_ONLY = frozenset({"合伙人", "老师", "助教", "家教", "讲师", "速记", "伴读"})
# 主播族里能在正文定罪的几个（BOSS 把"不用露脸、居家语音厅"只写在正文），
# 查的时候不认否定式。"口播"不在这一组：剪辑岗的 JD 会写"类目包括：口播、信息流…"，
# 那是素材类型；主播岗自己会把"口播"写进标题，标题那一遍拦。
ANCHOR_IN_BODY = frozenset({"露脸", "不露脸", "主包", "语音厅", "团播", "带播",
                            "互动播", "陪聊", "情感互动", "聊天室", "场控"})
KEEP_IN_BODY = frozenset({"销售", "中介", "派遣职位", "电销", "电话销售"})
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


def title_is_target_shaped(title: str, exempt=DEFAULT_EXEMPT) -> bool:
    """标题说没说得出"这是他要的那类活"（数据/标注/客服/剪辑…）。"""
    body = tight(title)
    return bool(body) and any(tight(w) in body for w in exempt)


def title_veto_hit(title: str, words=DEFAULT_TITLE_VETO) -> str:
    """岗位标题里的岗位类型词，命中返回那一条；带职业白名单词就放行。

    NO_EXEMPT 那几个词压过白名单：「AI+数据合伙人」里的"数据"不是它是数据岗的理由。
    """
    body = tight(title)
    if not body:
        return ""
    硬拦 = keyword_hit([w for w in words if w in NO_EXEMPT], body, honor_negation=False)
    if 硬拦:
        return 硬拦
    if title_is_target_shaped(body):
        return ""
    return keyword_hit([w for w in words if w not in NO_EXEMPT], body,
                       honor_negation=False)


def jd_type_hit(title: str, jd: str, words=DEFAULT_TITLE_VETO) -> str:
    """标题没交代岗位是什么时，类型词连 JD 一起量。

    "类型词只查标题"的前提是标题说明白了这是什么岗。盘上的反例：
    标题「山姆新仓开业大量招人8-9K长沙」一个字都没交代，岗位性质全在
    JD/HR 第一句「工作内容骑电瓶车配送山姆超市日常用品」里——只查标题时
    这一单一条都拦不着，于是配送岗一路聊到"我很有兴趣"。
    标题带职业白名单词（数据/标注/客服…）的仍然只查标题：
    「兼职·数据标注」的 JD 写"标注快递收发的画面"讲的是内容，不是岗位。
    销售/中介那一族走 否决词 那条认否定式的路，这里排除掉免得绕开豁免；
    老师/合伙人族写进正文时在说"带教老师""项目合伙人"，也排除。
    """
    标题, 正文 = tight(title), tight(jd)
    if not 标题 or not 正文 or title_is_target_shaped(标题):
        return ""
    return keyword_hit([w for w in words if w not in ANCHOR_IN_BODY
                        and w not in KEEP_IN_BODY and w not in TITLE_ONLY], 正文)


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
    if not hit:
        hit = jd_type_hit(title, jd, 类型词)
        where = "jd_type"
    if not hit:
        主播词 = sorted(set(类型词) & ANCHOR_IN_BODY)
        hit = keyword_hit(主播词, jd, honor_negation=False)
        where = "jd_anchor"
    return {"veto": bool(hit), "hit": hit, "where": where if hit else "",
            "title": title, "为什么": {
                "title_type": "标题写着岗位类型本身（普工/主播/快递/保洁/销售这一类）",
                "title_veto": "标题写着使用者不要的条件（坐班/包吃住/到场这一类）",
                "jd_veto": "JD 正文写着使用者不要的条件",
                "jd_type": "标题没说这是干什么的，JD 里写着岗位类型（配送/骑手/保洁…）",
                "jd_anchor": "JD 里写着主播族的说法（不露脸/语音厅/团播…）",
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
