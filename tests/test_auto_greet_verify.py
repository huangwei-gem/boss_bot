# -*- coding: utf-8 -*-
"""BOSS 自动发送之后，要真能进会话把本号那句补上。

现场（2026-10-05 21:33–21:41，主账号）：BOSS 对每个「立即沟通」都走第二种机制——
平台自己发一句"您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"再弹确认框。
只读核对侧栏确认这些确实发出去了（21:39/21:40 一排新会话），但记录里全是
"未能进会话核对文案（建议抽查）"：点了「继续沟通」只等 2–3 秒就读气泡，会话还没渲染
出来就判 none，于是我们 AI 现编的那句从来没补上去过。用户要的是转化，
对着 HR 念平台默认文案等于白投。

这里锁三件事：进会话改成"轮询到气泡真出来"；有预算上限，别把一单重新拖成两分钟；
读不到会话就绝不盲发（对着搜索页打字比不补发更糟）。
"""
from boss_bot.greet_engine import (CHAT_OPEN_POLL_SEC, CHAT_OPEN_POLLS,
                                   GreetEngine)


class FakeTab:
    def __init__(self, reads):
        self.reads = list(reads)
        self.typed = []
        self.clicked = []

    def ele(self, sel, timeout=None):
        return self

    def input(self, text):
        self.typed.append(text)

    def click(self):
        self.clicked.append("send")


def _engine(reads_per_tab, dialog=None):
    eng = GreetEngine.__new__(GreetEngine)
    eng.running = True
    eng._log = lambda *a, **k: None
    eng._random_delay = lambda a, b: None
    eng._interruptible_sleep = lambda s: None
    tabs = [FakeTab(r) for r in reads_per_tab]
    eng._greet_tab_candidates = lambda instance: tabs
    def fake_read(tab):
        if not tab.reads:
            return {}
        return tab.reads.pop(0)
    eng._read_last_outgoing = fake_read
    eng._auto_greet_dialog = lambda instance: dialog if dialog is not None else {
        "text": "已向BOSS发送消息", "cls": "dialog-container",
        "buttons": ["留在此页|btn btn-outline btn-cancel", "继续沟通|btn btn-sure"]}
    inst = FakeTab([])
    return eng, tabs, inst


def test_会话渲染慢也要等到气泡出来():
    """点「继续沟通」后前两三次读不到气泡是常态，原来只等 2~3 秒就放弃。"""
    eng, tabs, inst = _engine([[{}, {}, {"chat_page": True, "mine": 1,
                                        "head": "李女士 某某科技 HR 更多",
                                        "text": "您好，看到您的招聘信息，我很感兴趣。"}]])
    assert eng._auto_greet_followup(inst, "本号招呼语", eng._auto_greet_dialog(inst),
                                    {"company": "某某科技"}) == "sent"
    assert tabs[0].typed == ["本号招呼语"], "进了会话就该把本号那句补上"


def test_补发前先确认不是同一句():
    eng, tabs, inst = _engine([[{"chat_page": True, "mine": 1, "text": "本号 招呼语"}]])
    assert eng._auto_greet_followup(inst, "本号 招呼语", eng._auto_greet_dialog(inst)) == "matched"
    assert tabs[0].typed == [], "已经是本号那句就不能再发一遍"


def test_始终进不去会话就不盲发():
    eng, tabs, inst = _engine([[{"chat_page": False}] * (CHAT_OPEN_POLLS + 2)])
    got = eng._auto_greet_followup(inst, "本号招呼语", eng._auto_greet_dialog(inst))
    assert got == "none"
    assert tabs[0].typed == [], "读不到气泡还打字 = 对着搜索页输入"


def test_轮询要有预算上限():
    """一单的核对时间不能超过 8 秒：实测会话气泡只出现在回复引擎那个标签页
    （打招呼标签页永远读不到），6 次 × 2 秒换来的是每个岗位多等 12 秒，
    30 单就是 6 分钟白扔。留 4 次是因为抽屉真的会在某些岗位上出现。"""
    assert CHAT_OPEN_POLLS * CHAT_OPEN_POLL_SEC <= 8
    assert CHAT_OPEN_POLLS >= 3, "只读一次等于没改"


def test_没有继续沟通按钮时不进会话():
    eng, tabs, inst = _engine([[{"chat_page": True, "mine": 1, "text": "x"}]],
                              dialog={"text": "已向BOSS发送消息", "cls": "d",
                                      "buttons": ["留在此页|btn btn-cancel"]})
    assert eng._auto_greet_followup(inst, "本号招呼语", eng._auto_greet_dialog(inst)) == "none"
