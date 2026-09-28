# -*- coding: utf-8 -*-
"""回复记录与 BOSS 线上对齐的存储层测试。

起因是用户的直线反馈："回复记录依旧没有照搬 BOSS 上面的，顺序和内容都不一样"。
真机实测（tools/compare_boss_chat.py，账号0，2026-09-28）拿到四条根因：

1. 侧栏 34 个会话里 4 组重名（陈女士/唐女士/刘女士/易女士各 2 个），
   两个"陈女士"分别是「数据分析 8-12K」和「运营实习生 120-150/天」，
   但都写进同一个 `messages/陈女士.json` —— 内容必然和线上对不上。
2. 线上每条 `.message-item` 都有 `data-mid` 且自上而下单调递增，
   我们却按"时间标签"重排：`昨天 21:54`、`09-23 21:37` 这两种格式解析不了（返回 0.0），
   `HH:MM` 一律当今天，而且 306 条存量里有 123 条根本没有时间标签。
3. HR 重复发同一句话时，content+time 的退路键会把两条并成一条（线上是两条）。
4. 卡片消息（线上是空 .text-content 的卡片）被当成正文气泡存，
   而 `[简历已发送]` 这种 bot 自记条目混进气泡序列，线上根本没有这条。
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def store():
    from boss_bot.message_store import MessageStore
    return MessageStore(base_dir=tempfile.mkdtemp(), account_index=0)


def msg(text, time="", mine=False, mid=""):
    m = {"text": text, "time": time, "is_mine": mine, "isFriend": not mine}
    if mid:
        m["mid"] = str(mid)
    return m


class ChatIdentityTest:
    """同昵称是两段对话：身份 = 姓名 +（公司 或 岗位）

    实测（tools/probe_chat_identity.py，账号0 侧栏 34 行，2026-09-28）：
    只用姓名 30/34 唯一（陈女士/唐女士/刘女士/易女士 各 2 个），
    加上"公司"后 34/34 唯一 —— 而公司就挂在侧栏那一行的 .name-box 里，
    不用点开就能读到，所以身份不依赖"点开的到底是谁"。
    """

    def test_同昵称不同岗位标识不同(self, store):
        a = store.chat_id("陈女士", "双休+五险一金+数据分析师8-12K长沙")
        b = store.chat_id("陈女士", "运营实习生（长沙）120-150元/天长沙")
        assert a != b

    def test_同昵称同岗位但不同公司也要分开(self, store):
        """岗位可能读不到或恰好同名，公司才是侧栏上的分人依据"""
        a = store.chat_id("陈女士", company="成都市小智时代科技")
        b = store.chat_id("陈女士", company="艾秒广告")
        assert a != b

    def test_公司优先当身份(self, store):
        assert (store.chat_id("陈女士", company="艾秒广告", job_name="数据分析师")
                == store.chat_id("陈女士", company="艾秒广告", job_name="换了个岗位名"))

    def test_公司取不到时退到岗位(self, store):
        assert (store.chat_id("陈女士", "", "数据分析师")
                == store.chat_id("陈女士", "数据分析师"))

    def test_同一个人同一岗位标识稳定(self, store):
        assert (store.chat_id("陈女士", "数据分析师 8-12K 长沙")
                == store.chat_id("陈女士", "数据分析师 8-12K 长沙"))

    def test_两个同昵称会话内容不互相污染(self, store):
        store.merge_messages("陈女士", [msg("招熟手，不考虑实习生", mid="11")],
                             job_name="数据分析师")
        store.merge_messages("陈女士", [msg("这个实习岗要长期在岗", mid="22")],
                             job_name="运营实习生")
        a = store.get_messages("陈女士", job_name="数据分析师")
        b = store.get_messages("陈女士", job_name="运营实习生")
        assert [m["text"] for m in a] == ["招熟手，不考虑实习生"]
        assert [m["text"] for m in b] == ["这个实习岗要长期在岗"]

    def test_按公司分路存取(self, store):
        store.merge_messages("陈女士", [msg("招熟手", mid="11")],
                             company="成都市小智时代科技")
        store.merge_messages("陈女士", [msg("来聊聊运营实习", mid="22")],
                             company="艾秒广告")
        a = store.get_messages("陈女士", company="成都市小智时代科技")
        b = store.get_messages("陈女士", company="艾秒广告")
        assert [m["text"] for m in a] == ["招熟手"]
        assert [m["text"] for m in b] == ["来聊聊运营实习"]

    def test_会话列表按身份分开两条(self, store):
        store.merge_messages("陈女士", [msg("A", mid="1")], job_name="数据分析师")
        store.merge_messages("陈女士", [msg("B", mid="2")], job_name="运营实习生")
        chats = store.get_chat_list()
        assert len(chats) == 2
        assert {c["job_name"] for c in chats} == {"数据分析师", "运营实习生"}
        # 列表要把身份带回去，前端点进去才能对上同一个文件
        assert all(c.get("chat_id") for c in chats)

    def test_列表把公司带回前端(self, store):
        store.merge_messages("刘女士", [msg("你好", mid="5")], company="微算互联")
        got = store.get_chat_list()[0]
        assert got["company"] == "微算互联"
        assert got["chat_id"] == "刘女士#微算互联"

    def test_老昵称单文件仍然读得到(self, store):
        """迁移前不假装数据没了：按昵称存的旧文件要还能查（岗位对得上时）"""
        store.save_messages("杨女士", [msg("旧数据", time="10:00")])
        got = store.get_messages("杨女士")
        assert len(got) == 1 and got[0]["text"] == "旧数据"


class ChatSwitchGateTest:
    """点开哪一段对话不能靠假设：侧栏会重排，身份要以"真正 selected 的那一行"为准

    用户的质疑是"万一点的时候侧栏变了、但选中没变呢"。实测（--bind 模式，8 次点击）
    selected 落在被点行的概率是 8/8，但这是概率不是保证；旧代码又只比顶栏"姓名"，
    而两个陈女士姓名一样 —— 点错了照样"校验通过"。所以现在要求三方对齐。
    """

    def _handler(self, selected, header_name=""):
        from boss_bot.page_handler import BossChatHandler

        class FakePage:
            def run_js(self, script, as_expr=False):
                s = script
                if "indexOf(\"selected\")" in s:
                    return json.dumps(selected) if selected else ""
                if "#chat-input" in s:
                    return "ready"
                if "top-info-content" in s:
                    return header_name
                if "want.n" in s:
                    return "ok"
                return "[]"

            def get(self, url):
                pass

        h = BossChatHandler.__new__(BossChatHandler)
        h.page = FakePage()
        return h

    def test_selected行与目标一致才算切换成功(self):
        h = self._handler({"index": 3, "name": "陈女士",
                           "company": "艾秒广告", "title": "人事专员"})
        assert h.enter_chat({"index": 3, "name": "陈女士",
                             "company": "艾秒广告"}) is True

    def test_同名但不同公司必须判为失败(self):
        """顶栏姓名一样也会过旧校验 —— 这正是把消息记到别人会话里的路"""
        h = self._handler({"index": 27, "name": "陈女士",
                           "company": "成都市小智时代科技", "title": "HR"},
                          header_name="陈女士")
        assert h.enter_chat({"index": 1, "name": "陈女士",
                             "company": "艾秒广告"}) is False

    def test_selected落在别的行要判失败(self):
        h = self._handler({"index": 9, "name": "李女士", "company": "某公司"})
        assert h.enter_chat({"index": 2, "name": "唐女士", "company": "仟传"}) is False

    def test_读不到selected行时按名字回落(self):
        """BOSS 偶尔不给 selected 类：这时不能整轮都罢工，用顶栏姓名核对"""
        h = self._handler(None, header_name="唐女士")
        assert h.enter_chat({"index": 15, "name": "唐女士", "company": ""}) is True


class MidOrderTest:
    """顺序键只能是 data-mid：线上每条都有、单调递增；时间标签又稀疏又解析不动"""

    def test_昨天的消息不排到今天之后(self, store):
        # 昨天 21:54（解析不出时间）在前，今天 10:43 在后，按 mid 应该保持线上顺序
        store.merge_messages("唐女士", [
            msg("我是唐女士，如果你感兴趣", time="昨天 21:54", mid="100"),
            msg("好的，我看看", time="10:43", mine=True, mid="200"),
        ], job_name="数据分析")
        texts = [m["text"] for m in store.get_messages("唐女士", job_name="数据分析")]
        assert texts == ["我是唐女士，如果你感兴趣", "好的，我看看"]

    def test_merge不按时钟重排(self, store):
        """mid 小的必须在前，哪怕它的 time 字面更大（月日混排时时间不可信）"""
        store.merge_messages("刘女士", [
            msg("早", time="09-23 21:37", mid="10"),
            msg("晚", time="08:00", mid="20"),
        ], job_name="岗位A")
        store.merge_messages("刘女士", [
            msg("晚", time="08:00", mid="20"),
            msg("早", time="09-23 21:37", mid="10"),
        ], job_name="岗位A")
        got = store.get_messages("刘女士", job_name="岗位A")
        assert [m["mid"] for m in got] == ["10", "20"]

    def test_新批次追加后仍按mid升序(self, store):
        store.merge_messages("易女士", [msg("第一条", mid="5")], job_name="岗位B")
        store.merge_messages("易女士", [msg("第三条", mid="9"),
                                        msg("第二条", mid="7")], job_name="岗位B")
        assert [m["text"] for m in store.get_messages("易女士", job_name="岗位B")] == \
            ["第一条", "第二条", "第三条"]


class MidDedupTest:
    """HR 连发两句同样的话，线上是两条，我们只能靠 mid 分开"""

    def test_同样内容不同mid保留两条(self, store):
        store.merge_messages("王女士", [msg("好的", time="10:00", mid="1"),
                                        msg("好的", time="10:02", mid="2")],
                             job_name="岗位C")
        got = store.get_messages("王女士", job_name="岗位C")
        assert len(got) == 2

    def test_同一mid不重复入库(self, store):
        store.merge_messages("王女士", [msg("你好", mid="77")], job_name="岗位C")
        store.merge_messages("王女士", [msg("你好", mid="77")], job_name="岗位C")
        assert len(store.get_messages("王女士", job_name="岗位C")) == 1

    def test_没有时间标签也能靠mid存住(self, store):
        """线上只有分段处显示时间，正文消息大量没有 time"""
        store.merge_messages("彭先生", [msg("薪资面谈", mid="300")], job_name="岗位D")
        store.merge_messages("彭先生", [msg("方便发简历吗", mid="301", mine=True)],
                             job_name="岗位D")
        got = store.get_messages("彭先生", job_name="岗位D")
        assert [m["text"] for m in got] == ["薪资面谈", "方便发简历吗"]


    def test_卡片文本重复时间标签不再复活(self, store):
        """归一化过的消息再走一次 _norm_msg，不能从 block 把时间标签捡回来"""
        raw = {"text": "", "time": "09:04", "mid": "930", "is_mine": False,
               "block": "09:04 你与该职位竞争者PK情况 共人投递"}
        once = store._norm_msg(raw)
        twice = store._norm_msg(once)
        assert once["card_text"] == "你与该职位竞争者PK情况 共人投递"
        assert twice["card_text"] == once["card_text"]

    def test_renormalize把旧文件刷成当前规则(self, tmp_path):
        """存量文件里的时间标签重复要能一次性刷掉，且不丢消息、可重复执行"""
        from boss_bot.message_store import MessageStore
        from boss_bot.unified_config import write_json_atomic
        old = {
            "chat_name": "姜先生", "chat_id": "姜先生#天目咨询",
            "account_index": 0, "company": "天目咨询", "job_name": "",
            "updated_at": "2026-09-28 09:04:00",
            "messages": [
                {"text": "", "content": "", "time": "09:04", "mid": "940",
                 "is_mine": False, "isFriend": True, "kind": "card",
                 "card_text": "09:04 你与该职位竞争者PK情况 共人投递",
                 "block": "09:04 你与该职位竞争者PK情况 共人投递"},
                {"text": "方便发一份简历过来吗", "content": "方便发一份简历过来吗",
                 "time": "", "mid": "941", "is_mine": False, "isFriend": True,
                 "kind": "bubble"},
            ],
        }
        write_json_atomic(tmp_path / "姜先生_天目咨询.json", old)
        store = MessageStore(base_dir=tmp_path, account_index=0)
        assert store.renormalize() == 1
        got = store.get_messages("姜先生", company="天目咨询")
        assert len(got) == 2
        assert got[0]["card_text"] == "你与该职位竞争者PK情况 共人投递"
        assert got[1]["text"] == "方便发一份简历过来吗"
        # 再刷一次不该有改动（幂等），否则每次启动都在重写文件
        assert store.renormalize() == 0


class ApiChatIdentityTest:
    """接口层要能按 姓名+岗位 定位会话，否则前端点开的还是合并后的那一份"""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import sys
        from boss_bot import unified_config as UC
        from boss_bot import config as CFG
        # MessageStore 默认落在 BASE_DIR/messages（生产目录），测试必须整体改道
        monkeypatch.setattr(CFG, "BASE_DIR", tmp_path)
        monkeypatch.setattr(UC, "BOT_CONFIG_FILE", tmp_path / "bot_config.json")
        monkeypatch.setattr(UC, "OVERRIDES_FILE", tmp_path / "config_overrides.json")
        monkeypatch.setattr(UC, "USER_PROFILE_FILE", tmp_path / "user_profile.json")

        flask_dir = str(Path(__file__).parent.parent / "flask-version")
        if flask_dir not in sys.path:
            sys.path.insert(0, flask_dir)
        # 先把模块 import 完，再替换 MessageStore：
        # 在补丁生效时 import，main_loop/app 会把假类抓进自己的命名空间，
        # 测试结束后也还留在 sys.modules 里，后面的用例就拿到别人 tmp 目录的会话
        from boss_bot.message_store import MessageStore
        with patch("boss_bot.main_loop.BrowserManager"), patch("app.UnifiedBotLoop"):
            import app as FLASK_APP
            import boss_bot.main_loop as ML

            store = MessageStore(base_dir=tmp_path / "messages", account_index=0)
            store.merge_messages("陈女士",
                                 [msg("不考虑实习生", time="10:29", mid="11")],
                                 job_name="数据分析师")
            store.merge_messages("陈女士",
                                 [msg("实习要长期在岗", time="昨天 20:11", mid="21")],
                                 job_name="运营实习生")

            def _fake_store(base_dir=None, account_index=0):
                if base_dir is not None:
                    assert str(Path(base_dir).resolve()).startswith(str(tmp_path)), \
                        "会话接口测试必须落在临时目录，不能读写生产 messages/"
                return store

            monkeypatch.setattr(FLASK_APP, "MessageStore", _fake_store)
            monkeypatch.setattr(ML, "MessageStore", _fake_store)
            FLASK_APP.app.config["TESTING"] = True
            with FLASK_APP.app.test_client() as c:
                yield c
            # 断言真的没往生产目录写东西
            assert not (Path(__file__).parent.parent / "messages").joinpath(
                "陈女士_数据分析师.json").exists()

    def test_会话列表给出两条身份(self, client):
        chats = client.get("/api/chats").get_json()["chats"]
        names = [c["chat_name"] for c in chats]
        assert names.count("陈女士") == 2
        assert all(c.get("chat_id") for c in chats)

    def test_详情接口按岗位定位(self, client):
        d = client.get("/api/chats/陈女士?job=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90%E5%B8%88"
                       ).get_json()["chat"]
        assert [m["text"] for m in d["messages"]] == ["不考虑实习生"]

    def test_标记已读也按岗位定位(self, client):
        client.post("/api/chats/陈女士/mark_read?job=%E8%BF%90%E8%90%A5%E5%AE%9E%E4%B9%A0%E7%94%9F")
        a = client.get("/api/chats/陈女士?job=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90%E5%B8%88"
                       ).get_json()["chat"]["messages"]
        assert all(not m.get("is_read") for m in a)


class FrontendChatIdentityTest:
    """前端聊天面板：会话按身份选择，气泡按线上形态渲染，时间用线上文本"""

    def _html(self):
        return Path("flask-version/templates/index.html").read_text(encoding="utf-8")

    def test_会话缓存按身份索引(self):
        html = self._html()
        assert "chat_id" in html, "前端还在用昵称当会话 key，同昵称的两个 HR 会串"

    def test_详情与已读都带岗位参数(self):
        html = self._html()
        assert "chatParams" in html, "没统一的会话身份参数构造函数"
        # 详情 GET 与 mark_read POST 都必须走同一个带 job 的参数构造，
        # 漏一个就会出现"看的是岗位甲、已读打到岗位乙"
        assert html.count("+chatParams(") >= 2, "详情或已读漏传岗位参数"
        assert "&job=" in html

    def test_按kind分支渲染(self):
        html = self._html()
        for kind in ("card", "action", "system", "bubble"):
            assert f"'{kind}'" in html or f'"{kind}"' in html, f"渲染没处理 kind={kind}"

    def test_线上时间文本原样显示(self):
        """BOSS 写"昨天 21:54"就显示"昨天 21:54"，不要前端自己换算成日期"""
        html = self._html()
        assert "verbatim" in html or "rawTime" in html or "msg.time" in html

    def test_不再有空气泡(self):
        """正文为空又没按卡片渲染，就会画出线上看不到的空气泡"""
        html = self._html()
        assert "boss-msg-bubble\">' + escHtml(content)" in html or "kind === 'bubble'" in html


class MessageKindTest:
    """卡片和 bot 自记条目不能伪装成线上气泡"""

    def test_卡片消息保留类型与卡片文本(self, store):
        card = {"text": "", "time": "08:56", "mid": "900", "is_mine": False,
                "kind": "card", "card_text": "你与该职位竞争者PK情况 共人投递"}
        store.merge_messages("陈女士", [card, msg("抱歉，不考虑实习生哦", mid="901")],
                             job_name="数据分析师")
        got = store.get_messages("陈女士", job_name="数据分析师")
        assert [m["kind"] for m in got] == ["card", "bubble"]
        assert got[0]["card_text"].startswith("你与该职位竞争者")

    def test_卡片整块文本不重复时间标签(self, store):
        """线上 innerText 会把分段的时间一起带进来（"08:56 你与该职位…PK…"），
        时间是时间是单独一行显示的，卡片文本再留一遍就和线上不一致了"""
        store.merge_messages("陈女士", [{"text": "", "time": "08:56", "mid": "915",
                                         "block": "08:56 你与该职位竞争者PK情况 共人投递",
                                         "is_mine": False}],
                             job_name="数据分析师")
        got = store.get_messages("陈女士", job_name="数据分析师")
        assert got[0]["kind"] == "card"
        assert got[0]["card_text"] == "你与该职位竞争者PK情况 共人投递"
        assert got[0]["time"] == "08:56"

    def test_正文缺失但有整块文本时按卡片存(self, store):
        """线上一条 .text-content 为空的卡片，innerText 却有内容 —— 不能变成空气泡"""
        store.merge_messages("陈女士", [{"text": "", "block": "简历已发送 附件简历",
                                         "mid": "910", "is_mine": True}],
                             job_name="数据分析师")
        got = store.get_messages("陈女士", job_name="数据分析师")
        assert got and got[0]["kind"] in ("card", "action")

    def test_bot动作标记为action(self, store):
        """线上没有"[简历已发送]"这条气泡，它是引擎自记的动作（main_loop 传 action=resume）"""
        store.append_bot_message("陈女士", "[简历已发送]", job_name="数据分析师",
                                 action="resume")
        got = store.get_messages("陈女士", job_name="数据分析师")
        assert got[-1]["kind"] == "action"

    def test_真发出去的文字仍是气泡(self, store):
        store.append_bot_message("陈女士", "好的，我明天到岗", job_name="数据分析师",
                                 action="text")
        got = store.get_messages("陈女士", job_name="数据分析师")
        assert got[-1]["kind"] == "bubble"
