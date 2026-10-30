# -*- coding: utf-8 -*-
"""「谁给了微信 + 他的微信名」这条台账的判据测试（2026-10-27）。

他原话：「有些人微信加了我，我不知道是谁？你能不能在前端统计一下，给了微信的是谁，
以及显示他的微信名，在 boss 端是有微信名的。」

原文全部抄自 messages/*.json 真存档，一个字没改。三处最容易写坏的地方各有哨兵：
① 名字不许拿 BOSS 会话名顶（盘上有 3 张卡片印的名字和会话名不一样）；
② 号码不许被截首字符（卡片写「-Re1ax」，旧判据抓到 "Re1ax"，照着搜加不上人）；
③ 统计条与界面筛选必须是同一份判据（档位），否则"按钮上写 136、点进去 130 行"。
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boss_bot.contact_ledger import (  # noqa: E402
    contact_rows,
    contact_stats,
    extract_contacts,
    微信卡片,
    台账归类,
    台账档位,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "flask-version" / "templates" / "index.html"
APP = ROOT / "flask-version" / "app.py"

# ── 真存档原话（卡片、企业微信、请求、打字给号）──
CARD_NORMAL = "muzang623 傅女士的微信号 muzang623 复制微信号"
CARD_DASH_HEAD = "-Re1ax 袁先生的微信号 -Re1ax 复制微信号"     # 首字符是减号
CARD_DASH_TAIL = "blue0728- 张女士的微信号 blue0728- 复制微信号"   # 尾字符是减号
CARD_WXID = "wxid_0dmr4fhf4y8122 姜清涛的微信号 wxid_0dmr4fhf4y8122 复制微信号"
CARD_PHONE = "15574975718 兰波的微信号 15574975718 复制微信号"
CARD_WITH_TIME = "昨天 19:49 15574975718 兰波的微信号 15574975718 复制微信号"  # block 里带时间前缀
QYWX_CARD = "BOSS向您发送企业微信，请点击按钮去添加 去添加"
ASK_CARD = "我想要和您交换微信，您是否同意 拒绝 同意"
DECLINE_MARK = "[已拒绝交换联系方式]"
AGREE_MARK = "[已同意交换联系方式]"
# 真误报源：HR 自己打字说"加我这个号码"，号码在正文里，卡片从没出现过
TYPED = "可以接受的话，添加我这个号码的微信 13267043664，备注好姓名+地区，我拉你进业务群"


def _chat(messages, name="傅女士", company="合肥市棱箫琼科技", account=1,
          job="兼职·网络工程python仿真matlab大模型算法线上70-120元/天延安查看职位"):
    return {"chat_name": name, "chat_id": f"{name}#{company}", "account_index": account,
            "job_name": job, "company": company, "updated_at": "2026-10-25 02:24:18",
            "messages": messages}


def _hr_card(card):
    return {"text": "", "card_text": card, "block": card, "isFriend": True,
            "is_mine": False, "is_system": False, "kind": "card", "sender": "hr"}


def _hr_text(text):
    return {"text": text, "card_text": "", "block": text, "isFriend": True,
            "is_mine": False, "is_system": False, "kind": "bubble", "sender": "hr"}


def _ours(text):
    return {"text": text, "card_text": "", "block": text, "isFriend": False,
            "is_mine": True, "is_system": False, "kind": "bubble", "sender": "bot"}


def _一行(messages, **kw):
    rows = contact_rows([_chat(messages, **kw)])
    assert len(rows) == 1, f"应当正好进一行，实际 {len(rows)} 行"
    return rows[0]


# ── ① 卡片上的微信名 ──

def test_卡片同时给出微信号与微信名():
    assert 微信卡片(CARD_NORMAL) == [("muzang623", "傅女士")]


def test_微信名的四种真实形状都取得到():
    for card, 号, 名 in ((CARD_PHONE, "15574975718", "兰波"),
                      (CARD_DASH_TAIL, "blue0728-", "张女士"),
                      (CARD_WXID, "wxid_0dmr4fhf4y8122", "姜清涛"),
                      (CARD_WITH_TIME, "15574975718", "兰波")):
        got = 微信卡片(card)
        assert got == [(号, 名)], f"{card} → {got}"


def test_减号开头的微信号不许被截断():
    """旧判据限死"字母开头或纯数字"，「-Re1ax 袁先生的微信号」被截成 "Re1ax"：
    界面上那串字拿去微信里搜，人加不上。这条只锁这一个 bug。"""
    assert extract_contacts(CARD_DASH_HEAD)["wechats"] == ["-Re1ax"]
    assert 微信卡片(CARD_DASH_HEAD) == [("-Re1ax", "袁先生")]


def test_卡片号码不再重复报成电话():
    """卡片本身是微信号；截断 bug 一修，同一个数字再被手机号正则抓到就成两个联系方式。"""
    got = extract_contacts(CARD_PHONE)
    assert got["wechats"] == ["15574975718"] and got["phones"] == []


def test_台账行带出微信名并可复制():
    row = _一行([_hr_card(CARD_NORMAL)])
    assert row["wechat_names"] == ["傅女士"]
    assert row["wechats"] == ["muzang623"]
    assert row["wechat_given"] is True
    assert row["wechat_name_note"] == "", "取到名字时不许再挂一条'为什么没有'"
    assert row["ledger_bucket"] == "给了微信号"


def test_微信名与会话名不一致时以卡片为准():
    """真存档里就有这种：会话「杨小湖」的微信名印「杨女士」。
    他要在微信里认人，认的是卡片那一串——拿会话名填就等于把答案写成另一个人。"""
    row = _一行([_hr_card("15812368902 杨女士的微信号 15812368902 复制微信号")], name="杨小湖")
    assert row["wechat_names"] == ["杨女士"]
    assert row["chat_name"] == "杨小湖"
    assert "杨小湖" not in row["wechat_names"]


def test_同一会话多张卡片名字都留下():
    row = _一行([_hr_card(CARD_NORMAL), _hr_card(CARD_DASH_HEAD)])
    assert row["wechat_names"] == ["傅女士", "袁先生"]
    assert row["wechats"] == ["muzang623", "-Re1ax"]


# ── ② 取不到名字就照实说，不许编 ──

def test_打字给的微信号有号但没微信名要写明为什么():
    """这一路卡片从没出现过，名字真取不到。写"傅女士"这种会话名上去，
    他会照着去加一个对不上的人。"""
    row = _一行([_hr_text("加我微信 abc12345 详聊")], name="蒲慧芝")
    assert row["wechat_given"] is True and row["wechats"] == ["abc12345"]
    assert row["wechat_names"] == []
    assert "没印微信名" in row["wechat_name_note"], row["wechat_name_note"]
    assert row["ledger_bucket"] == "给了微信号"


def test_只给电话的不算给了微信():
    """他要在微信里认人，电话那一单认不出来；画进"给了微信号"或"只聊简历"都是假话。"""
    row = _一行([_hr_text(TYPED)], name="蒲慧芝")
    assert row["wechat_given"] is False and row["phones"] == ["13267043664"]
    assert row["ledger_bucket"] == "只给了电话"
    assert row["wechat_names"] == []


def test_我方气泡里出现卡片字样不冒充微信名():
    assert contact_rows([_chat([_hr_text("你好"), _ours(CARD_NORMAL)])]) == []


def test_点过同意但存档里没那张号卡要单独一档():
    row = _一行([_hr_card(ASK_CARD), _ours(AGREE_MARK), _hr_text("好的")])
    assert row["wechat_given"] is False
    assert row["ledger_bucket"] == "点过同意没看到号"
    assert "存档里没有" in row["wechat_name_note"]


def test_企业微信卡片单独成档并进台账():
    """「BOSS向您发送企业微信，请点击按钮去添加」也是有人要加他微信，
    盘上 11 个会话有它、其中 4 个别的联系方式证据都没有——不收进来就漏掉这 4 个人。"""
    row = _一行([_hr_card(QYWX_CARD), _hr_text("岗位还合适吗，感兴趣点卡片加下运营老师")])
    assert row["enterprise_wechat"] is True
    assert row["ledger_bucket"] == "发来企业微信卡片"
    assert row["wechat_names"] == []
    assert "企业微信" in row["wechat_name_note"]


def test_只有企业微信卡片也要成行不许漏掉():
    row = _一行([_hr_card(QYWX_CARD)])
    assert row["matched_quote"] and QYWX_CARD in row["matched_quote"]


def test_请求与拒绝各归各档():
    assert _一行([_hr_card(ASK_CARD)])["ledger_bucket"] == "只发起交换"
    assert _一行([_hr_card(ASK_CARD), _ours(DECLINE_MARK)])["ledger_bucket"] == "已拒绝交换"
    assert _一行([_hr_text("方便加个微信详细聊聊")])["ledger_bucket"] == "说要给没留号"


def test_我方自己说加微信不算对方给了号():
    assert contact_rows([_chat([_hr_text("岗位还合适吗"), _ours("方便的话我加您微信")])]) == []


def test_岗位标题里的数字不会被当成联系方式():
    chats = [_chat([_hr_text("美团骑手（超时差评不扣钱）提供吃住+就近5-85元/单长沙查看职位")])]
    assert contact_rows(chats) == [], "JD 里的数字不是微信号，进表就是让人白翻"


def test_没有联系方式也没有简历的会话不进表():
    assert contact_rows([_chat([_hr_text("你好"), _ours("您好，我对岗位感兴趣")])]) == []


# ── ③ 统计条与筛选同源 ──

def test_档位求和等于行数():
    """每个会话只落一档：多算会让他以为"给了微信的人"比实际多，漏算就白翻。"""
    rows = contact_rows([_chat([_hr_card(CARD_NORMAL)]),
                         _chat([_hr_card(ASK_CARD)]),
                         _chat([_hr_card(QYWX_CARD)]),
                         _chat([_hr_text("简历发我一下")])])
    stats = contact_stats(rows)
    assert stats["total_rows"] == len(rows)
    assert sum(b["count"] for b in stats["buckets"]) == stats["total_rows"]
    assert [b["name"] for b in stats["buckets"]] == list(台账档位)


def test_给了微信号的计数与档位行数一致():
    """统计条上那两个数（wechat_given 与 '给了微信号' 档）必须相等，
    它们分别来自 extract_contacts 和 台账归类——判据分叉时这里先红。"""
    rows = contact_rows([_chat([_hr_card(CARD_NORMAL)]), _chat([_hr_text("加我微信 abc12345")]),
                         _chat([_hr_card(ASK_CARD)])])
    stats = contact_stats(rows)
    档 = {b["name"]: b["count"] for b in stats["buckets"]}
    assert stats["wechat_given"] == 档["给了微信号"] == 2
    assert stats["wechat_named"] == 1, "只有卡片那一单印了名字"
    assert stats["wechat_id_only"] == 1


def test_每一行的档位都取自档位表():
    rows = contact_rows([_chat([_hr_card(CARD_NORMAL)]), _chat([_hr_card(QYWX_CARD)]),
                         _chat([_hr_text("方便加个微信吗")]), _chat([_ours("[简历已发送]")])])
    for row in rows:
        assert row["ledger_bucket"] in 台账档位, row
        assert 台账归类(row) == row["ledger_bucket"]


def test_只聊简历的行不能挤进给了微信那一档():
    卡片 = _chat([_hr_card(CARD_NORMAL)], name="甲")
    简历 = _chat([_hr_text("简历发我一下"),
                  _hr_card("您的附件简历 数据分析简历-黄维.docx 已发送给Boss点击查看附件")],
                 name="乙")
    rows = contact_rows([卡片, 简历])
    by = {r["chat_name"]: r["ledger_bucket"] for r in rows}
    assert by == {"甲": "给了微信号", "乙": "只聊简历没聊联系方式"}


# ── 全量存档逐张核对（判据与界面同源的那一端）──

def _盘上的卡片行():
    """[(会话, 微信号, 微信名)]：直接从 messages/*.json 的卡片原文数，一次都不走台账。

    这条是"另一只眼"：判据走的是 微信卡片() 本身，所以它只能证明"卡片形状还在、
    文件还读得到"；台账那一侧另有 contact_rows 的数，两边对得上才说明形状没换。
    """
    出 = []
    for p in sorted((ROOT / "messages").glob("*.json")):
        chat = json.loads(p.read_text(encoding="utf-8"))
        for msg in chat.get("messages") or []:
            for 号, 名 in 微信卡片(str(msg.get("card_text") or "")):
                出.append((chat, 号, 名))
    return 出


def _去重会话(卡片行):
    seen = {}
    for chat, _, _ in 卡片行:
        seen[id(chat)] = chat
    return list(seen.values())


def test_全量存档里卡片号没有一个被改字():
    """逐个会话过一遍：卡片上印的那串字（号与名）必须一字不差落在行上。

    一个会话一个会话单独跑，不拿 (姓名+公司) 去对行——盘上就有两个「肖克」，
    其中一个连公司名都没抓到，用键去对会把"其实取到了"误报成"没进台账"。
    """
    卡片 = _盘上的卡片行()
    assert len(卡片) >= 100, f"存档里微信号卡片应当成批存在，只读到 {len(卡片)} 张"
    按会话 = {}
    for chat, 号, 名 in 卡片:
        按会话.setdefault(id(chat), (chat, []))[1].append((号, 名))
    缺 = []
    for chat, 对 in 按会话.values():
        rows = contact_rows([chat])
        assert len(rows) == 1, f"{chat.get('chat_name')} 有卡片却没进台账"
        行 = rows[0]
        for 号, 名 in 对:
            if 号 not in 行["wechats"] or 名 not in 行["wechat_names"]:
                缺.append((chat.get("chat_name"), 号, 名, 行["wechats"], 行["wechat_names"]))
    assert not 缺, f"卡片上的号/名在台账里变了字：{缺[:5]}"


def test_全量存档给过微信号的会话数与台账档位一致():
    """两头各数一遍：一头直接从存档找卡片，一头走 contact_rows。
    哪天 BOSS 换了印法，这两条数会先分叉。"""
    会话 = _去重会话(_盘上的卡片行())
    rows = contact_rows(会话)
    台账 = [r for r in rows if r["ledger_bucket"] == "给了微信号"]
    assert len(会话) == len(台账), f"盘上 {len(会话)} 个会话有微信号卡片，台账只认到 {len(台账)}"
    带名 = len([r for r in 台账 if r["wechat_names"]])
    assert 带名 == len(台账), "有卡片就应该有名字；缺的那几个说明卡片形状没被读到"


def test_全量存档里名字与会话名不一致的会话有正证():
    """不一致不是 bug，正是"加了微信不知道是谁"的那种；钉住下限，
    哪天判据退化成都拿会话名填，这条会红。"""
    rows = contact_rows(_去重会话(_盘上的卡片行()))
    不一致 = [r for r in rows if r["wechat_names"] and r["chat_name"] not in r["wechat_names"]]
    assert len(不一致) >= 3, f"盘上实测有 3 单不一致，现在只数到 {len(不一致)}"
    for r in 不一致:
        assert r["wechat_given"] is True


# ── 三端：后端字段 ↔ 模板渲染 ↔ 接口 ──

def _渲染段():
    """renderContactLedger 里逐行拼 <td> 那一段。"""
    js = _台账JS()
    起 = js.index("body.innerHTML = rows.map")
    return js[起:js.index("}).join('');", 起)]


def _台账JS():
    """模板里「联系与简历台账」那一段脚本（不含其它 tab）。"""
    html = TEMPLATE.read_text(encoding="utf-8")
    return html[html.index("// ── 联系与简历台账 ──"):html.index("// ── 岗位候选池 ──")]


def test_接口把stats与行一起返回():
    src = APP.read_text(encoding="utf-8")
    assert '"stats": contact_stats(rows)' in src, "接口没算统计，前端那条统计条就是空的"
    导入行 = [l for l in src.splitlines()
                if l.startswith("from boss_bot.contact_ledger import")]
    assert any("contact_stats" in l for l in 导入行), f"没 import：{导入行}"


def test_模板有微信名列且取不到时画破折号():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert ">微信名<" in html, "表头没有微信名这一列"
    渲染 = _渲染段()
    assert "'<td>' + ledgerNameHtml(r) + '</td>'" in 渲染, "行里没渲染微信名那一格"
    assert "wechat_name_note" in html, "空着的时候没写为什么，看起来像漏抓"


def test_微信名走data_copy属性不拼进onclick():
    """名字是 BOSS 那边的外部文本，带个撇号（Bob's）就会把那句内联 JS 打断，
    点了没反应还算轻的；escAttr 又不转义单引号，所以这条路只能换成 data-copy + 委托。"""
    js = _台账JS()
    取名 = js[js.index("function ledgerNameHtml"):js.index("function copyLedgerContact")]
    assert 'data-copy="' in 取名
    assert "onclick=\"copyLedgerContact(" not in 取名, "名字又被拼进 onclick 的字符串里了"
    assert "closest('[data-copy]')" in js, "没有委托，点了不复制"
    assert js.count("addEventListener('click'") == 1, "委托装了多趟"
    assert "body.dataset.copyProxy === '1'" in js, "重画一次就多装一趟监听"


def test_每行渲染的格数与表头列数一致():
    """少一格整张表向右错位——他看到的是"依据原话"那一列变成了微信名。
    只数表头数不出这种错，所以 JS 侧另数一遍。"""
    渲染 = _渲染段()
    格数 = 渲染.count("'<td")
    html = TEMPLATE.read_text(encoding="utf-8")
    表头 = html[html.index('<table class="greet-table" id="contactTable">'):
              html.index('<tbody id="contactTableBody">')]
    assert 表头.count("<th ") + 表头.count("<th>") == 格数, (表头, 格数)
    assert 格数 == 10, 格数


def test_筛选按档位走不自己另写判据():
    """JS 里那一趟筛选只许比较 ledger_bucket；在这儿再写一遍 wechats/contact_kind，
    统计条和点进去的行数就会各说各话。"""
    js = _台账JS()
    过行 = [l for l in js.splitlines() if "contactLedgerRows.filter" in l]
    assert len(过行) == 1, f"台账行该只过一趟筛选：{过行}"
    assert "r.ledger_bucket === contactLedgerFilter" in 过行[0], 过行[0]
    for 漏 in ("r.wechats", "contact_kind", "wechat_given"):
        assert 漏 not in 过行[0], f"筛选里自己写了判据：{漏}"


def test_筛选按钮由后端档位生成():
    html = TEMPLATE.read_text(encoding="utf-8")
    js = html[html.index("function renderLedgerStats"):html.index("function markLedgerFilterActive")]
    assert "s.buckets" in js and "data-ledger-bucket" in js
    # 档位名不许在 HTML 里写死第二份：静态区里只能有"全部"这一个按钮
    起 = html.index('id="contactStatsBar"')
    静态区 = html[起:html.index("</table>", 起)]
    assert 静态区.count("setLedgerFilter(") == 1, 静态区.count("setLedgerFilter(")
    assert "contactFilterChips" in 静态区
    assert "给了微信号" not in 静态区, "档位名被抄进模板第二份了"


def test_统计条没拿到stats时不许显示零():
    """"取不到"印成 0 就是狼来了那条老毛病：他会以为一个人都没给。"""
    js = TEMPLATE.read_text(encoding="utf-8")
    js = js[js.index("function renderLedgerStats"):js.index("function markLedgerFilterActive")]
    assert "没返回 stats" in js
    assert re.search(r"if \(!s\)", js), "少了取不到的分支"


def test_表格列数与占位colspan对齐():
    """加载/失败那几行占位格数少了，表格会错位成"看起来只有一列"。"""
    占位 = set(re.findall(r'colspan="(\d+)"', _台账JS()))
    assert 占位 and 占位 == {"10"}, f"占位行的格数和表头不一致：{占位}"


# ── 认人搜索框：他手上只有微信里那串名字 ──

def test_搜索框在筛选条里且placeholder点明搜什么():
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'id="contactSearchInput"' in html, "没有搜索框，136 行得他自己扫"
    assert 'oninput="setLedgerSearch(this.value)"' in html, "框在但打字不重画"
    assert "搜微信名" in html, "placeholder 没点明这一框是拿微信里那串名字来认人"


def test_搜索面含微信名会话名公司号码():
    js = _台账JS()
    取名 = js[js.index("function ledgerSearchText"):js.index("function setLedgerSearch")]
    for 面 in ("r.wechat_names", "r.chat_name", "r.company", "r.job_name", "r.wechats", "r.phones"):
        assert 面 in 取名, f"搜索面少了 {面}：这一类关键词搜不出来"
    assert "toLowerCase()" in 取名, "大小写敏感，粘进来的英文号搜不到"


def test_搜索与档位取交集但档位判据只有一处():
    """档位管"给到没有"，搜索管"这是谁"，两个轴可以叠；
    但归档判据在 JS 里只能出现一次，否则统计条和点进去的行数各说各话。"""
    js = _台账JS()
    过档 = [l for l in js.splitlines() if "contactLedgerRows.filter" in l]
    assert len(过档) == 1 and "r.ledger_bucket === contactLedgerFilter" in 过档[0], 过档
    assert "scoped.filter(function(r) { return ledgerSearchText(r)" in js, "搜索没叠在档位结果上"


def test_命中数要当场说出来():
    """只有表格变少、没一句话解释，他会以为存档掉了一批。"""
    js = _台账JS()
    assert "markLedgerHit(!!contactLedgerSearch, rows.length, scoped.length)" in js
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'id="ledgerSearchHit"' in html, "命中数没有落脚的地方"
    打点 = js[js.index("function markLedgerHit"):js.index("function ledgerKindHtml")]
    assert "命中" in 打点 and ": ''" in 打点, "空词时得收回这句话，不然挂着个过期的命中数"
    assert "'命中 <b>' + hit" in 打点, "命中数是写死的，屏上行数和它迟早对不上"


def test_搜不到不许画成没捞到():
    """"没命中"和"存档空了"是两件事：前者是这个名字确实不在台账里，后者要他去查采集。"""
    js = _台账JS()
    空行 = js[js.index("var 没中 ="):js.index("var dash =")]
    assert "没命中" in 空行 and "这串名字不在台账里" in 空行
    assert "不是没捞到" in 空行, "两种空态都不许写成没捞到"
    assert "scoped.length" in 空行, "要说清是在多少行里没搜到"


def test_打字不许重拉接口或重建筛选条():
    """重拉接口会把他正看着的那几行换掉；重建筛选条会抢走输入框焦点，第二个字就输了。"""
    js = _台账JS()
    打字 = js[js.index("function setLedgerSearch"):js.index("function markLedgerHit")]
    for 坏 in ("loadContactLedger", "renderLedgerStats", "contactFilterChips"):
        assert 坏 not in 打字, f"打字时动了 {坏}"


def test_接口一次给全量行所以搜索不会漏():
    """搜索是现拉表格上的字符串匹配：接口只回一页的话，没回的那几行搜不到，
    而他看到的现象是"明明加了微信却搜不出来"。"""
    src = APP.read_text(encoding="utf-8")
    端 = src[src.index("def api_contact_ledger"):]
    端 = 端[:端.index("\ndef ")]
    assert '"rows": rows' in 端, "行没整份回给前端"
    for 分页 in ("[:", "limit", "per_page", "offset"):
        assert 分页 not in 端, f"接口只回一页（{分页}），前端搜索会漏行"
