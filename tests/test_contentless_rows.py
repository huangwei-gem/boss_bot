# -*- coding: utf-8 -*-
"""读不出正文的线上行，不能在界面上长成一行假消息。

现场（2026-10-17 三端一致性实测 `tools/verify_three_way.py --chats 3 --account 1`）：
`欧阳先生#长沙县榔梨街道芝___` 线上 15 / 后端 15 / **前端 16**。差的那一条是
`messages/a1_欧阳先生_长沙县榔梨街道芝___.json` 的第 7 行：
`kind=card`、`text`/`content`/`card_text` 全空、`block="20:09"`、`mid=393827869446656`。
`block` 是 `read_all_messages` 取的整条 `innerText`——这一行线上唯一的文字就是它自己的
时间标签。前端 `cardText = msg.card_text || msg.block || content` 把 "20:09" 当成卡片
正文，于是画出一个 BOSS 上没有的卡片。

全量扫 2265 份存档：这种行有 **110 条**（39 条 block 只剩时间标签、71 条连时间都没有），
`kind` 分布是 card 39 / bubble 71。

所以判据只留一份：`message_store.线上读不出正文()`。归一化时顺手把 block 里的时间标签
剥掉（否则 `card_text || block` 这条退路会把它捞回来），合并时不收，前端兜最后一层
（存量文件里已经躺着的那些行，等下一次同步才会被清掉，界面不能先骗人）。

至于这些行在线上古装到底是什么（图片？表情？撤回？），本轮不猜：读回来的时候把
DOM 形状写进日志，攒到证据再说。
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.message_store import MessageStore  # noqa: E402

INDEX_HTML = ROOT / "flask-version" / "templates" / "index.html"

# 欧阳先生第 7 行的原样形状（read_all_messages 刚读回来、还没归一化）
只剩时间标签的那条 = {"text": "", "time": "20:09", "isFriend": True, "is_mine": False,
                      "is_system": False, "mid": "393827869446656", "block": "20:09",
                      "sender": "hr"}
连时间都没有的那条 = {"text": "", "time": "", "isFriend": True, "is_mine": False,
                      "is_system": False, "mid": "393827869446600", "block": "",
                      "sender": "hr"}
真卡片那条 = {"text": "", "time": "08:56", "is_mine": False, "mid": "900",
              "block": "08:56 你与该职位竞争者PK情况 共人投递"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from boss_bot import message_store
    monkeypatch.setattr(message_store, "_greet_records_path",
                        lambda: tmp_path / "no_greet.json")
    monkeypatch.setattr(message_store, "_reply_records_path",
                        lambda: tmp_path / "no_reply.json")
    return MessageStore(base_dir=str(tmp_path / "messages"), account_index=0)


class 时间标签不算正文Test:

    def test_只剩时间标签的一条不进存档(self, store):
        store.merge_messages("欧阳先生", [dict(只剩时间标签的那条),
                                         {"text": "OK", "time": "11:10",
                                          "is_mine": False, "mid": "999"}],
                             job_name="短视频剪辑", company="长沙县榔梨街道芝")
        got = store.get_messages("欧阳先生", job_name="短视频剪辑", company="长沙县榔梨街道芝")
        assert [m["text"] for m in got] == ["OK"], "读不出正文的行也进存档，界面就比线上一行"

    def test_连时间都没有的一条也不进存档(self, store):
        store.merge_messages("吴女士", [dict(连时间都没有的那条),
                                        {"text": "在吗", "time": "", "is_mine": False,
                                         "mid": "998"}],
                             job_name="数据标注")
        got = store.get_messages("吴女士", job_name="数据标注")
        assert [m["text"] for m in got] == ["在吗"]

    def test_block里不把时间标签留给前端退路(self, store):
        """前端画卡片取的是 card_text || block：block 里留着 "20:09"
        就等于把时间标签当正文，界面凭空多一行"""
        norm = store._norm_msg(dict(只剩时间标签的那条))
        assert norm["card_text"] == ""
        assert norm["block"] == "", "block 还写着时间标签，退路会把它捞回来"

    def test_真卡片照旧留下(self, store):
        """反向哨兵：卡片正文去掉时间标签后还有内容，那种必须留——
        把判据写宽了就会连简历/PK 卡片一起清掉"""
        store.merge_messages("陈女士", [真卡片那条], job_name="数据分析师")
        got = store.get_messages("陈女士", job_name="数据分析师")
        assert len(got) == 1
        assert got[0]["kind"] == "card"
        assert got[0]["card_text"] == "你与该职位竞争者PK情况 共人投递"

    def test_普通气泡照旧留下(self, store):
        store.merge_messages("陈女士", [{"text": "不考虑实习生哦", "time": "09:00",
                                         "is_mine": False, "mid": "901"}],
                             job_name="数据分析师")
        got = store.get_messages("陈女士", job_name="数据分析师")
        assert [m["text"] for m in got] == ["不考虑实习生哦"]

    def test_存量里的空正文条目下次同步自动消失(self, store):
        """已经躺在存档里的 110 条不能靠人工清：合并把已有条目也走一遍判据。
        存量文件只能手工造——修好之后的 merge 已经不会再收这种行了"""
        path = store._path("欧阳先生", "长沙县榔梨街道芝", "短视频剪辑")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "chat_name": "欧阳先生",
            "chat_id": store.chat_id("欧阳先生", "长沙县榔梨街道芝", "短视频剪辑"),
            "account_index": 0,
            "job_name": "短视频剪辑", "company": "长沙县榔梨街道芝",
            "messages": [dict(只剩时间标签的那条),
                         {"text": "能接受调动吗", "time": "19:13", "is_mine": False,
                          "mid": "997"}],
        }, ensure_ascii=False), encoding="utf-8")
        assert len(json.loads(path.read_text(encoding="utf-8"))["messages"]) == 2

        store.merge_messages("欧阳先生", [{"text": "好的", "time": "19:20",
                                          "is_mine": True, "mid": "998"}],
                             job_name="短视频剪辑", company="长沙县榔梨街道芝")
        got = store.get_messages("欧阳先生", job_name="短视频剪辑", company="长沙县榔梨街道芝")
        assert [m["text"] for m in got] == ["能接受调动吗", "好的"]


class 判据只留一份Test:

    def test_合并与读取共用同一个判据(self):
        存 = (ROOT / "boss_bot" / "message_store.py").read_text(encoding="utf-8")
        读 = (ROOT / "boss_bot" / "page_handler.py").read_text(encoding="utf-8")
        assert "线上读不出正文" in 存, "判据没落成一处函数"
        assert "线上读不出正文" in 读, "读消息那一路自己抄了一份判空条件"
        # 时间标签当正文这条退路只能出现在判据函数里
        assert 存.count("startswith(lbl)") == 1

    def test_判据对三种形状各判一次(self):
        assert MessageStore.线上读不出正文(dict(只剩时间标签的那条)) is True
        assert MessageStore.线上读不出正文(dict(连时间都没有的那条)) is True
        assert MessageStore.线上读不出正文(dict(真卡片那条)) is False
        assert MessageStore.线上读不出正文({"text": "在吗", "time": "09:00"}) is False


def _抽函数(src, name):
    m = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", src)
    assert m, "index.html 里找不到 %s()" % name
    start = src.index("{", m.start())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError("%s() 大括号没配对" % name)


def 渲染前端(msgs):
    """把 index.html 里真实的 renderBossMessageBubble 交给 node 跑，
    纯字符串扫描证明不了"这一条到底画没画出一行"。"""
    if shutil.which("node") is None:
        pytest.skip("需要 node 才能真跑前端函数")
    src = INDEX_HTML.read_text(encoding="utf-8")
    js = """
const CASES = %s;
function escHtml(s){return String(s==null?"":s);}
function escAttr(s){return String(s==null?"":s);}
function formatFullTimestamp(s){return String(s||"");}
function formatHumanTime(s){return String(s||"");}
function getReplySourceLabel(s){return "";}
function getReplySourceClass(s){return "";}
%s
const out = CASES.map(function(m){ return renderBossMessageBubble(m, "欧阳先生"); });
console.log(JSON.stringify(out));
""" % (json.dumps(msgs, ensure_ascii=False), _抽函数(src, "renderBossMessageBubble"))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        path = f.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)
    assert proc.returncode == 0, "node 执行失败:\n%s" % proc.stderr
    return json.loads(proc.stdout.strip())


class 前端不画读不出的那行Test:

    def test_存量那种只剩时间标签的行画出来是空的(self):
        got = 渲染前端([dict(只剩时间标签的那条)])
        assert got[0] == "", "前端还在用 block 里的时间标签画一张线上没有的卡片"

    def test_正文全空的气泡画出来也是空的(self):
        got = 渲染前端([dict(连时间都没有的那条)])
        assert got[0] == ""

    def test_真卡片仍然画(self):
        """反向哨兵：判据收紧不能把线上真有内容的卡片一起藏掉"""
        卡片 = dict(真卡片那条)
        卡片.update({"kind": "card", "content": "", "text": "",
                     "card_text": "你与该职位竞争者PK情况 共人投递", "block": ""})
        got = 渲染前端([卡片])
        assert "boss-msg-card" in got[0]
        assert "竞争者" in got[0]

    def test_普通气泡仍然画(self):
        got = 渲染前端([{"kind": "bubble", "text": "OK", "content": "OK",
                         "time": "11:10", "is_mine": False, "sender": "hr"}])
        assert "boss-msg-bubble" in got[0]
        assert "OK" in got[0]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
