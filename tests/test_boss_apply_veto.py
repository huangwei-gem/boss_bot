# -*- coding: utf-8 -*-
"""skill 版本里那层硬否决：判据必须是代码，不能是"agent 自己看着办"。

使用者 2026-10-07："有些主播销售，工厂啥的直接拒绝就行。"
生产机上一共踩过三次同源的坑，都出在口径靠文字描述：
- 类型词拿去查 JD 正文 → "要标注快递场景录音"这种他要的岗被杀；
- 否定式不认 → 标题写"（线上）不坐班"的剪辑/运营岗被当成坐班岗拒掉（盘上 14 处"坐班"
  命中里 9 处是这种）；
- 类型词去认否定式 → "不露脸主播""直招非中介"被放行，而那正是要拒的。
所以这三条在这里各配一条断言，锁死。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "boss-apply"
sys.path.insert(0, str(PKG / "scripts"))

import veto  # noqa: E402

要拒的标题 = [
    "长白班普工包吃住18元一小时（代招职位）",
    "急招（在家就行）哔哩哔哩兼职不露脸主播",
    "长沙市岳麓区快递员8-12K",
    "桶装水配送无需装卸8-9K",
    "电商仓库打包员包吃住6000+",
    "销售专员4-9K长沙",
    "无责4K-课程顾问-周内双休12-20K",
    "足疗按摩师 保底6000",
    "团播艺人 全程带播 高提成",
    "岳麓区坐班临时工/包住宿/可周结（派遣职位）",
]
要的标题 = [
    "兼职·线上短视频剪辑100-300元/时",
    "兼职·数据标注/AI训练师80-100元/天",
    "兼职·线上私域运营不坐班可兼职120-200元/天",
    "兼职·电商客服专员（居家办公 远程）",
    "兼职·线上兼职心理咨询师（全国可投 不用坐班）",
    "直播运营助理（线上）",
    "电动车数据标注员",
    "销售数据分析专员",
]


def test_这些标题命中否决():
    for 标题 in 要拒的标题:
        got = veto.check(title=标题)
        assert got["veto"] is True, f"漏了：{标题} → {got}"


def test_这些标题不许被误杀():
    for 标题 in 要的标题:
        got = veto.check(title=标题)
        assert got["veto"] is False, f"误杀：{标题} → 命中「{got['hit']}」"


def test_类型词不去查JD正文():
    """"标注快递物流场景的录音"是数据标注岗——类型词拿去查正文就会杀他要的岗。"""
    got = veto.check(title="兼职·语音数据标注员",
                     jd="需要标注快递物流场景的录音、直播间语音切片等语料，居家完成，按件结算")
    assert got["veto"] is False, got


def test_否决词查JD正文():
    """这条反过来：JD 里写"需到岗坐班、包吃住"，标题再干净也不该投。"""
    got = veto.check(title="兼职·线上内容审核",
                     jd="入职后需到岗坐班，公司提供包吃住宿舍，周末单休")
    assert got["veto"] is True and got["where"] == "jd_veto", got


def test_标题里的否决词也算():
    """「【白班坐岗】28/H包吃住」这种，条件写在标题里，比正文一句顺带一提硬得多。"""
    got = veto.check(title="28/H包吃住+可预支+不体检（职位）")
    assert got["veto"] is True and got["where"] == "title_veto", got


def test_否定式在这两套词里待遇相反():
    assert veto.keyword_hit(["坐班"], "兼职·线上剪辑（不坐班）") == ""
    assert veto.keyword_hit(["坐班"], "周一至周五需坐班") == "坐班"
    assert veto.title_veto_hit("不露脸主播 招5人") != ""
    assert veto.title_veto_hit("无需露脸 语音厅主播") != ""
    # "非"不当否定词：派遣/工厂岗最爱写"直招非中介"，那条照样要拦
    assert veto.keyword_hit(["中介"], "蓝思直招非中介") == "中介"


def test_小句开头的否定管到整句():
    """"全程远程协作，无需到公司坐班"——否定词离"坐班"隔了四个字，仍然算否定。"""
    assert veto.keyword_hit(["坐班"], "全程远程协作，无需到公司坐班") == ""
    assert veto.keyword_hit(["坐班"], "无需打卡，但周末要坐班") == "坐班"


def 拆字_测试():
    """BOSS 把敏感字拆开写：抹掉空格与不可见字符之前一条都匹不上。"""
    assert veto.tight("免 费 提 供电动车食住") == "免费提供电动车食住"
    assert veto.title_veto_hit("全长沙 免⁢费 提供电动车食住直招") != ""


test_拆字标题也拦得住 = 拆字_测试      # pytest 收集用，函数名不能有标点


def test_命令行能跑通并出JSON(tmp_path):
    规则 = tmp_path / "rules.json"
    规则.write_text(json.dumps({"reject_keywords": ["包吃住"]}), encoding="utf-8")
    out = subprocess.run([sys.executable, str(PKG / "scripts" / "veto.py"), "check",
                         "--title", "普工包吃住 18 元一小时", "--rules", str(规则)],
                        capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["veto"] is True and got["hit"], got


def test_example规则里带得上这两张表():
    rules = json.loads((PKG / "assets" / "rules.example.json").read_text(encoding="utf-8"))
    assert rules.get("reject_keywords"), "示例配置没给否决词，使用者照抄就等于没这层"
    assert rules.get("reject_title_keywords"), "示例配置没给岗位类型词"
    for 词 in ("销售", "主播", "普工", "快递", "保洁"):
        assert any(词 in w for w in rules["reject_title_keywords"]), f"默认表里没有{词}"


def test_SKILL正文写了这层且行数没超():
    正文 = (PKG / "SKILL.md").read_text(encoding="utf-8")
    assert "veto.py" in 正文 and "skipped_hard_veto" in 正文
    assert len(正文.splitlines()) <= 500


def test_合伙人压过数据白名单():
    """「AI+数据合伙人」里的"数据"不是它是数据岗的理由——合伙人本身就是骗局形状。"""
    assert veto.title_veto_hit("AI+数据合伙人") != ""
    assert veto.title_veto_hit("城市合伙人（数据方向）") != ""


def test_老师族只查标题():
    """HR 说"有带教老师一对一辅导"讲的是培养机制，不是岗位。"""
    got = veto.check(title="极氪零售实习生180-200元/天",
                     jd="我们正在招极氪零售实习生，有带教老师一对一辅导，不需要驾照")
    assert got["veto"] is False, got
    assert veto.title_veto_hit("兼职线上英语老师") != ""


def test_标题没交代岗位时拿JD判岗():
    """用户："他不是快递吗？你到底有没有解析岗位jd来判断是什么岗位啊。"

    盘上真形状：标题「山姆新仓开业大量招人8-9K长沙」一个类型词都没有，
    岗位性质只写在 JD/HR 第一句里。这种标题下类型词必须连正文一起量。
    """
    got = veto.check(title="山姆新仓开业大量招人8-9K长沙查看职位",
                     jd="工作内容骑电瓶车配送山姆超市日常用品，固定点取货多点配送，系统派单")
    assert got["veto"] is True and got["where"] == "jd_type", got


def test_目标形状标题的JD仍旧不误杀():
    got = veto.check(title="兼职·数据标注/AI训练师",
                     jd="标注快递收发、超市理货这些真实场景的录音，按条结算，全程线上")
    assert got["veto"] is False, got


def test_信贷类():
    assert veto.title_veto_hit("信贷专员") != ""
    assert veto.title_veto_hit("贷款顾问（线上）") != ""
    assert veto.title_veto_hit("信用卡催收") != ""
    assert veto.title_veto_hit("信贷风控数据分析师") == ""


def test_示例配置带上新词():
    rules = json.loads((PKG / "assets" / "rules.example.json").read_text(encoding="utf-8"))
    表 = rules["reject_title_keywords"]
    for 词 in ("合伙人", "老师", "信贷", "配送", "家教"):
        assert 词 in 表, 词


def test_与线上引擎同判据():
    """skill 那份是抄写的副本，产品这份是本体——两边判得不一样就是分发出去的人在用旧口径。

    用例全取盘上真发生过的形状（messages/*.json、greet_records.json）。
    """
    from boss_bot.intent import veto_hit_anywhere
    from boss_bot.unified_config import TITLE_VETO_KEYWORDS_DEFAULT as T

    对着核 = [
        # (标题, JD/HR 正文, 期望：True=该拒)
        ("山姆新仓开业大量招人8-9K长沙查看职位",
         "工作内容骑电瓶车配送山姆超市日常用品，固定点取货多点配送", True),
        ("AI+数据合伙人", "负责数据清洗，长期合作分成", True),
        ("兼职少儿口才老师（线上居家办公）", "按课结算", True),
        ("极氪零售实习生180-200元/天上海查看职位",
         "有带教老师一对一辅导，不需要驾照", False),
        ("兼职·数据标注/AI训练师80-100元/天",
         "标注快递收发、超市理货这些真实场景的录音，按条结算", False),
        ("兼职·线上短视频剪辑师1000-10000元/月",
         "剪辑类目包括：口播、信息流、电商带货、品牌宣传、切片", False),
        ("信贷风控数据分析师", "评估个人信贷违约概率", False),
        ("信贷专员（线上开发）", "银行信贷产品推介", True),
        ("长白班普工包吃住18元一小时（代招职位）", "两班倒", True),
        ("兼职·线上数据分析-兼职50-200元/时", "用 SQL 出周报，远程交付", False),
    ]
    for 标题, 正文, 期望 in 对着核:
        产品 = bool(veto_hit_anywhere(T, [], title=标题, text=正文))
        分发 = bool(veto.check(title=标题, jd=正文)["veto"])
        assert 产品 == 期望, ("产品侧", 标题, 产品, 期望)
        assert 分发 == 期望, ("分发侧", 标题, 分发, 期望)
