# -*- coding: utf-8 -*-
"""只读：站在那个求职者自己的位置上，把每一单重新判一遍"这单我到底投不投"，
再跟机器当时真做的动作对表——**漏掉的单**（该投没投）和**白烧额度的单**（不该投却投了）。

为什么不能再拿生产词表自查：词表判的是"字面像不像普工/主播"，而求职者判的是
"这活我能不能在线上做完、是不是我要攒经验的那一类、这家是不是在骗钱"。
两套判据长得像，但漏的地方不一样：这里的"该投没投"直接等于他少投了一单。

口径写死在本文件里（不 import 生产词表），要改判据先改这里、再改代码，
两边对不上的地方就是这个工具存在的意义。

跑法：python tools/audit_jobseeker_view.py [--apply-note]
"""
import collections
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.intent import keyword_hit, veto_hit_anywhere  # 只借"认否定式"这一口径
from boss_bot.unified_config import UnifiedConfig

# ——— 求职者自己要的方向 ———
# 只有 点名 那三族算"该投"。以前把客服/剪辑/代练也算成方向，方向闸拦下它们就被
# 记成"误杀"，于是整轮优化都在往这张表加词——可他自己的口径写在
# user_profile.json 的 position 里：「线上兼职（数据标注/AI数据标注、数据分析、
# 线上运营助理）」。别的行当不是不能做，是这张闸门由他点头才能放宽（10-09 那条
# "别有人嫌投得少去松这张闸"的警示），所以这里判"不投"，让它们离开"少投了多少单"的账。
方向 = {
    "数据标注": ["标注", "质检", "审核", "语料", "采集数据", "地图数据"],
    "数据分析": ["数据分析", "数据挖掘", "数据治理", "数据建模", "商业分析", "经营分析",
               "数据仓库", "数据清洗", "指标", "BI", "SQL", "Excel", "报表", "爬虫",
               "数据支持", "数据整理", "资料整理", "信息收集", "问卷"],
    "运营助理": ["运营助理"],
    "别的行当": ["运营", "新媒体", "内容运营", "用户运营", "电商运营", "社群",
               "客服", "售后", "在线客服", "接待",
               "剪辑", "后期", "图文", "文案", "写作", "翻译", "校对", "PPT", "设计", "排版",
               "代练", "打手", "游戏陪", "护航"],
}
点名 = ("数据标注", "数据分析", "运营助理")
# 这五类方向之外，"分析"这个词裸着出现的多半是化学/财务/机械分析，不是他的方向
裸分析要排除的形状 = ["化学", "实验", "检测", "药物", "财务", "预算", "风控", "电机", "结构"]

# ——— 到场才能做的说法（他要的是线上兼职）———
到场 = ["坐班", "需坐班", "到岗", "到场", "到店", "驻场", "外勤", "现场面试", "线下面试",
       "线下办公", "到公司", "需到公司", "全天班", "轮班", "两班倒", "倒班", "白班", "夜班"]
# ——— 求职者一眼就退的形状（蓝领/主播/家教/销售/信贷）———
不要的岗 = {
    "蓝领/工厂": ["普工", "操作工", "技工", "焊工", "车间", "流水线", "电子厂", "分拣", "打包",
               "装卸", "仓管", "贴标", "组装", "进厂", "作业员"],
    "配送/跑腿": ["快递", "骑手", "外卖", "配送", "送餐", "司机", "代驾", "网约车", "货运", "搬运"],
    "主播/直播": ["主播", "团播", "语音厅", "出镜", "带货", "直播助理", "场控"],
    "服务行业": ["保安", "保洁", "服务员", "收银", "传菜", "店员", "导购", "保姆", "月嫂", "足疗"],
    "家教/培训": ["教师", "老师", "家教", "辅导", "督学", "助教", "讲师", "课程顾问", "招生", "单词"],
    "销售/中介": ["销售", "地推", "业务员", "客户经理", "BD", "中介", "房产", "保险", "理财", "信贷", "贷款"],
}
# ——— 骗钱/引流形状：求职者最怕的是先掏钱或白跑 ——
骗形状 = ["押金", "保证金", "培训费", "交钱", "缴费", "体检费", "服装费", "刷单", "垫付",
         "无门槛", "零门槛", "日入", "拉人", "合伙人", "招代理", "加盟", "兼职群", "进群",
         "纯佣", "无底薪", "在家日入", "手机电脑都行", "副业天花板"]
# ——— 能线上做完的说法 ———
线上 = ["线上", "远程", "居家", "在家", "可线上", "在线办公", "不用到公司", "兼职", "实习",
        "日结", "元/时", "元/天", "按条", "按单", "接单"]

到场_RE = re.compile("|".join(到场))
线上_RE = re.compile("|".join(re.escape(w) for w in 线上))


def 取记录():
    行 = {}
    for p in sorted(glob.glob("data/archive/*/greet_records.json")) + ["data/greet_records.json"]:
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        for r in (d.get("records") if isinstance(d, dict) else d) or []:
            键 = (r.get("job_url") or "").strip() or (r.get("job_name", "") + r.get("company", ""))
            if 键 and 键 not in 行:          # 同一个岗位看多次，只算一次决定
                行[键] = r
    return list(行.values())


def 求职者判(r):
    """返回 (结论, 理由)。结论：投 / 不投 / 看不出来。"""
    标题 = str(r.get("job_name") or "")
    正文 = " ".join(str(r.get(k) or "") for k in
                  ("job_description", "job_requirements", "description", "requirements"))
    全 = 标题 + " " + 正文

    for 类, 词们 in 不要的岗.items():
        if any(w in 标题 for w in 词们):
            return "不投", "岗位本身是%s（写在标题里）" % 类
    # 「无押金」「不收费」这种否定式不能算骗——和生产那一遍共用同一个认否定的口径
    骗 = keyword_hit(骗形状, 全, honor_negation=True)
    if 骗:
        return "不投", "碰到收钱/引流形状「%s」" % 骗
    if keyword_hit(到场, 标题, honor_negation=True) and not 线上_RE.search(标题):
        return "不投", "标题写明到场而没给线上说法"

    命中方向 = [类 for 类, 词们 in 方向.items() if any(w in 全 for w in 词们)]
    if "数据分析" not in 命中方向 and any(w in 标题 for w in ("分析",)):
        if not any(w in 全 for w in 方向["数据分析"] + ["标注"]):
            命中方向 = [类 for 类 in 命中方向 if 类 != "数据分析"]
    if any(w in 标题 for w in 裸分析要排除的形状) and 命中方向 == ["数据分析"]:
        return "不投", "标题里的「分析」是化学/财务/机械那种分析"
    if 命中方向 and not any(类 in 点名 for 类 in 命中方向):
        return "不投", "不是他点名的方向（%s）" % "、".join(命中方向[:2])

    if not 命中方向:
        return "看不出来", "没读出他要的方向"
    # 方向对上之后还要再看一遍标题：「数据标注兼职（可转正-线下面试和到岗）」这种
    # 自己写着要来公司的，对求职者来说就是不能投——不能因为"可转正"像线上就放过。
    if keyword_hit(到场, 标题, honor_negation=True):
        return "不投", "方向对但标题写了到场（%s）" % keyword_hit(到场, 标题, honor_negation=True)
    if not 线上_RE.search(全):
        return "投", "方向对（%s），但通篇没写线上/远程——只能先投了再问" % "、".join(命中方向[:2])
    return "投", "方向对（%s）且有线上/兼职形状" % "、".join(命中方向[:2])


def 机器动作(r):
    if r.get("is_greeted") or r.get("status") == "applied":
        return "投了", ""
    if r.get("status") == "failed":
        return "没投成", r.get("skip_reason") or "页面/抽屉失败"
    return "没投", (r.get("skip_reason") or "没写原因")


def 主():
    行 = 取记录()
    print("去重后机器看过的岗位 %d 个" % len(行))
    格 = collections.Counter()
    该投没投 = []
    不该投却投 = []
    for r in 行:
        我判, 我的理由 = 求职者判(r)
        动作, 原因 = 机器动作(r)
        格[(我判, 动作)] += 1
        if 我判 == "投" and 动作 != "投了":
            该投没投.append((r, 我的理由, 原因))
        elif 我判 == "不投" and 动作 == "投了":
            不该投却投.append((r, 我的理由))

    投总数 = sum(v for (类, 动), v in 格.items() if 动 == "投了")
    print("\n投了 %d 个 / 没投 %d 个 / 没投成 %d 个" % (
        投总数, sum(v for (类, 动), v in 格.items() if 动 == "没投"),
        sum(v for (类, 动), v in 格.items() if 动 == "没投成")))

    print("\n=== 求职者视角=投、机器却没投（这就是少掉的单）%d 个 ===" % len(该投没投))
    按原因 = collections.Counter(原.split("（")[0][:34] for _, _, 原 in 该投没投)
    for k, v in 按原因.most_common(12):
        print("  %4d  %s" % (v, k))
    print("  样本：")
    for r, 我的理由, 原因 in 该投没投[:8]:
        print("    %s｜%s｜我说：%s｜机器：%s" % (
            (r.get("job_name") or "")[:30], (r.get("company") or "")[:12],
            我的理由[:40], 原因[:44]))

    print("\n=== 求职者视角=不投、机器却投了（白烧额度）%d 个 ===" % len(不该投却投))
    按类 = collections.Counter(理由 for _, 理由 in 不该投却投)
    for k, v in 按类.most_common(8):
        print("  %4d  %s" % (v, k))
    for r, 理由 in 不该投却投[:8]:
        print("    %s｜%s｜%s" % ((r.get("job_name") or "")[:30],
                              (r.get("company") or "")[:12], 理由[:40]))

    # 这 87 个大多是"当时词表还没这个词"投出去的。拿现在的生产判据重放一遍，
    # 才能分出"今晚已经修掉的"和"现在照样会漏的"——后者才是还剩的活。
    cfg = UnifiedConfig.load()
    表 = list(cfg.ai.title_veto_keywords or [])
    正文表 = list(cfg.ai.custom_filter_keywords or [])
    已修 = 仍漏 = 0
    仍漏样本 = []
    for r, 理由 in 不该投却投:
        中 = veto_hit_anywhere(表, 正文表, title=str(r.get("job_name") or ""),
                               text=str(r.get("job_description") or ""))
        if 中:
            已修 += 1
        else:
            仍漏 += 1
            仍漏样本.append((r, 理由))
    print("\n  拿现在的判据（%d 条词）重放：已被词表修掉 %d 个，**现在照样会漏 %d 个**"
          % (len(表), 已修, 仍漏))
    for r, 理由 in 仍漏样本[:10]:
        print("    仍漏 %s｜%s｜%s" % ((r.get("job_name") or "")[:32],
                                  (r.get("company") or "")[:12], 理由[:36]))

    print("\n=== 该投却没投，按「是谁杀的」归类 ===")
    谁杀 = collections.Counter()
    方向闸样本 = []
    for r, 我的理由, 原因 in 该投没投:
        if 原因.startswith("已沟通过") or "冷却" in 原因:
            谁杀["去重/冷却（本来就该跳）"] += 1
        elif ("页面加载失败" in 原因 or "抽屉" in 原因 or "没有可点的沟通按钮" in 原因
              or "超时" in 原因 or "验证码" in 原因):
            谁杀["页面/浏览器这一腿没走通（稳定性，不是判据）"] += 1
        elif "AI 分析异常" in 原因 or "AI判定不匹配: AI" in 原因:
            谁杀["AI 接口这次没给出判断（正文被截断/超时）"] += 1
        elif "不是要投的方向" in 原因:
            谁杀["方向闸（正向门槛）"] += 1
            方向闸样本.append((r, 我的理由))
        elif "命中否决词" in 原因 or "硬性筛选条件" in 原因:
            谁杀["否决词表/AI 按否决词判死"] += 1
        elif "没写具体工作内容" in 原因:
            谁杀["JD 太薄（空泛岗）"] += 1
        else:
            谁杀["其它：" + 原因[:18]] += 1
    for k, v in 谁杀.most_common(10):
        print("  %4d  %s" % (v, k))
    print("  方向闸杀掉的 %d 个，标题属于哪些方向：" % len(方向闸样本))
    按向 = collections.Counter()
    for r, 我的理由 in 方向闸样本:
        按向[我的理由.split("（")[-1].split("）")[0][:22]] += 1
    for k, v in 按向.most_common(8):
        print("     %3d  %s" % (v, k))
    for r, 我的理由 in 方向闸样本[:10]:
        print("     × %s｜%s｜%s" % ((r.get("job_name") or "")[:34],
                                 (r.get("company") or "")[:12], 我的理由[:34]))

    # 上面这些账里，很多是"当时词表/闸还没改"的历史账。全部拿今天的判据重放一遍，
    # 才分得清"已经修好了"和"现在照样少投"——后者才是今晚要动的活。
    from boss_bot.greet_engine import jd_gate, target_job_hit, veto_keyword_hit
    cfg2 = UnifiedConfig.load()
    闸方向 = list(cfg2.ai.target_job_keywords or [])
    今杀 = collections.Counter()
    今仍杀样本 = []
    for r, 我的理由, 原因 in 该投没投:
        if 原因.startswith("已沟通过") or "冷却" in 原因:
            continue                       # 本来就该跳
        if ("页面加载失败" in 原因 or "抽屉" in 原因 or "没有可点的沟通按钮" in 原因
                or "超时" in 原因 or "验证码" in 原因 or "断开" in 原因):
            continue                       # 工程问题，不是判据
        判 = jd_gate(正文表, r, title_keywords=表, target_keywords=闸方向)
        if 判:
            今杀[判.split("（")[0][:26]] += 1
            今仍杀样本.append((r, 判, 我的理由))
    总今杀 = sum(今杀.values())
    print("\n=== 拿今天的判据重放这些「该投没投」：现在仍会被杀掉 %d 个 ===" % 总今杀)
    for k, v in 今杀.most_common(10):
        print("  %4d  %s" % (v, k))
    # 方向闸还杀着的那几十条要按"我自己判的方向"摊开：配置那张表是一串词，
    # 标题里写的是另一串说法（陪玩/代肝/打手 vs 游戏代练）。
    # 不看这个分布就不知道表还差哪些写法，只会一句"再加点词"。
    按族 = collections.Counter()
    for r, 判, 我的理由 in 今仍杀样本:
        if 判.startswith("不是要投的方向"):
            按族[我的理由.split("（")[-1].split("）")[0].split("，")[0][:14]] += 1
    print("  仍被方向闸杀掉的，按我判的方向：")
    for k, v in 按族.most_common():
        print("    %3d  %s" % (v, k))
    print("  这些的标题写法：")
    for k, v in sorted(collections.Counter(
            (r.get("job_name") or "")[:16] for r, 判, _ in 今仍杀样本
            if 判.startswith("不是要投的方向")).items(), key=lambda x: -x[1])[:18]:
        print("    %3d  %s" % (v, k))
    for r, 判, 我的理由 in 今仍杀样本[:14]:
        print("    × %s｜%s｜今天：%s｜我：%s" % (
            (r.get("job_name") or "")[:32], (r.get("company") or "")[:10],
            判[:44], 我的理由[:26]))

    # 误杀要取证：光看"命中了哪个词"判不了对错，得看那个词在句子里说的是这份工，
    # 还是被分析/被标注的业务场景。"分析地推业务的数据"和"负责地推"是两回事。
    print("\n=== 判死最多的那几个词，命中处的上下文 ===")
    词序 = [k.split("「")[-1].rstrip("」")[:6] for k, _ in 今杀.most_common(6)]
    for 词 in 词序:
        例 = []
        for r, 判, _ in 今仍杀样本:
            if 词 not in 判:
                continue
            标题 = str(r.get("job_name") or "")
            正文 = str(r.get("job_description") or r.get("description") or "")
            文 = 标题 + " " + 正文
            i = 文.find(词)
            窗 = 文[max(0, i - 22):i + 26].replace("\n", " ") if i >= 0 else ""
            例.append((标题[:26], 窗))
        if 例:
            print("  「%s」杀 %d 个：" % (词, 今杀[[k for k in 今杀 if 词 in k][0]]))
            for 标题, 窗 in 例[:4]:
                print("     %s ‖ …%s…" % (标题, 窗))




    print("\n=== 对表全图 ===")
    print("%-10s %8s %8s %8s" % ("我判\\机器", "投了", "没投", "没投成"))
    for 类 in ("投", "不投", "看不出来"):
        print("%-12s %6d %8d %8d" % (类, 格[(类, "投了")], 格[(类, "没投")], 格[(类, "没投成")]))


if __name__ == "__main__":
    主()
