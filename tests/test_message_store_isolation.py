# -*- coding: utf-8 -*-
"""两个账号的会话存档必须各写各的。

现场（2026-10-04）：全目录 330 个 `a1_` 文件里，16 个与账号0 的同名文件逐条相同
（连 data-mid 都一样）。根因是 `_candidates` 兜底时会 glob 整个目录，
`_read_path` 于是能把别的账号的文件当"已有历史"返回，而 `merge_messages`
读的是那个文件、写的却是本账号的文件——账号1 的一次同步就把账号0 的整段对话
复制进了自己的存档。存档一污染，界面、AI 上下文、回复归属全都对不上线上。
"""
import json

from boss_bot.message_store import MessageStore


def _msg(text, mid, mine=False):
    return {"text": text, "type": "text", "mid": str(mid), "is_mine": mine,
            "time": "10:00", "timestamp": "2026-10-04 10:00:00"}


def _read(tmp_path, store, name):
    with open(store._path(name) if not store.account_index
              else tmp_path / f"a{store.account_index}_{name}.json",
              encoding="utf-8") as f:
        return json.load(f)


def test_账号1同步不得把账号0的对话并进自己存档(tmp_path):
    a0 = MessageStore(base_dir=tmp_path, account_index=0)
    a1 = MessageStore(base_dir=tmp_path, account_index=1)
    a0.merge_messages("刘女士", [_msg("在吗", 100)], company="微算互联")

    a1.merge_messages("刘女士", [_msg("你好", 200)], company="微算互联")

    stored = [m["content"] for m in a1.get_messages("刘女士", company="微算互联")]
    assert stored == ["你好"]


def test_账号1读不到账号0的会话(tmp_path):
    MessageStore(base_dir=tmp_path, account_index=0).merge_messages(
        "刘女士", [_msg("在吗", 100)], company="微算互联")

    assert MessageStore(base_dir=tmp_path, account_index=1).get_messages(
        "刘女士", company="微算互联") == []


def test_空同步不得在账号1名下凭空造出存档(tmp_path):
    """回复轮对没聊过的会话也会调 merge_messages([])，此时若兜底命中账号0 的文件，
    就会把那段对话原样写成账号1 的文件——一条新消息都没读到却多出一份存档。"""
    a0 = MessageStore(base_dir=tmp_path, account_index=0)
    a0.merge_messages("陈女士", [_msg("方便电话吗", 300)], company="艾秒广告")

    a1 = MessageStore(base_dir=tmp_path, account_index=1)
    assert a1.merge_messages("陈女士", [], company="艾秒广告") == 0
    assert not list(tmp_path.glob("a1_*.json"))


def test_账号1标已读不得改账号0的存档(tmp_path):
    a0 = MessageStore(base_dir=tmp_path, account_index=0)
    a0.merge_messages("陈女士", [_msg("方便电话吗", 300)], company="艾秒广告")
    before = json.dumps(_read(tmp_path, a0, "陈女士#艾秒广告"), ensure_ascii=False,
                        sort_keys=True)

    a1 = MessageStore(base_dir=tmp_path, account_index=1)
    a1.set_boss_unread("陈女士", 0, company="艾秒广告")
    a1.mark_chat_read("陈女士", company="艾秒广告")

    after = json.dumps(_read(tmp_path, a0, "陈女士#艾秒广告"), ensure_ascii=False,
                       sort_keys=True)
    assert after == before


def test_同账号老存档只有昵称时仍能对上(tmp_path):
    """own-only 不能顺手砍掉同账号内的身份兜底：历史文件是按昵称存的，
    现在按 姓名+公司 定位，靠 loose 匹配才能接着读到老对话。"""
    a0 = MessageStore(base_dir=tmp_path, account_index=0)
    a0.merge_messages("陈女士", [_msg("你好", 1)])

    msgs = a0.get_messages("陈女士", company="艾秒广告")

    assert [m["content"] for m in msgs] == ["你好"]


def test_面板列会话仍然跨账号全扫(tmp_path):
    """隔离的是"按号读写"，不是"看得见"：面板不带账号时要列出两个号的会话，
    靠的是全目录扫描 + 每条标注 account_index。"""
    MessageStore(base_dir=tmp_path, account_index=0).merge_messages(
        "刘女士", [_msg("在吗", 100)], company="微算互联")
    MessageStore(base_dir=tmp_path, account_index=1).merge_messages(
        "刘女士", [_msg("你好", 200)], company="微算互联")

    chats = MessageStore(base_dir=tmp_path, account_index=0).get_all_chats_detail()

    assert sorted(c["account_index"] for c in chats) == [0, 1]
