# -*- coding: utf-8 -*-
"""喂给 AI 的那段对话：系统卡不许冒充 HR，主动跟进那句不许重说一遍。

现盘实测（本轮现算，messages/*.json 共 2779 份会话存档）：
- 窗口里取到的 13452 句中有 651 句（4.8%）是 BOSS 的系统卡片
  （"对方已查看了您的附件简历""对方已同意，您的附件简历已发送给对方"），
  364 个会话吃到过；**107 个会话里"对方最新那句"根本就是张系统卡**——
  机器把平台的回执当成 HR 在说话，回过去的话自然对不上。
- 1136 个有两句以上我方气泡的会话里，163 个出现过重复句、共 329 句；
  按发出来源分：跟进 27、补发简历 51、诈骗筛 18、意图直回 16、换联系方式 9、AI 3。
  被动回复那条腿早有 `_is_duplicate_reply` 拦着，跟进这条腿是直接把现编那句发出去的。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.prompts import 对话窗口, build_conversation_history  # noqa: E402
from boss_bot.reply_engine import ReplyEngine  # noqa: E402

_split_messages = ReplyEngine._split_messages


def 句(n, 我=False, 系统=False, 前缀=""):
    out = {"text": f"{前缀}第{n}句", "is_mine": 我, "mid": str(1000 + n)}
    if 系统:
        out["is_system"] = True
        out["text"] = "对方已查看了您的附件简历"
    return out


class Test系统卡不占对话名额:
    def test_系统行不吃那三十句的名额(self):
        ms = [句(i, 我=(i % 2 == 0)) for i in range(1, 26)]
        ms += [句(0, 系统=True) for _ in range(9)]        # 中间夹九张平台回执
        窗 = 对话窗口(ms)
        真句 = [m for m in 窗 if not m.get("is_system")]
        系统行 = [m for m in 窗 if m.get("is_system")]
        assert len(真句) >= 25, "把真人说的话挤掉了"
        assert len(真句) + len(系统行) == len(窗)

    def test_系统卡标成系统不许画成对方说的话(self):
        文 = build_conversation_history([句(1), 句(0, 系统=True), 句(2, 我=True)])
        assert "对方: 对方已查看了您的附件简历" not in 文, 文
        assert any(线.startswith("系统:") for 线 in 文.splitlines()), 文

    def test_只有系统卡的会话读不出对方那句(self):
        """读不出就说读不出：把"对方已查看简历"当成 HR 的新消息去回，
        就是 107 个会话里那种答非所问。"""
        latest, 历史 = _split_messages([句(0, 系统=True)])
        assert latest == ""
        assert len(历史) == 1, "历史还在，只是那句不该被当成要回的话"

    def test_系统卡后面那句真话照样算最新消息(self):
        ms = [句(9, 前缀="薪资"), 句(0, 系统=True), 句(11, 前缀="什么时候")]
        latest, _ = _split_messages(ms)
        assert latest.startswith("什么时候"), latest


class Test跟进不再重说一遍:
    def _引擎(self, 依次给出):
        e = ReplyEngine.__new__(ReplyEngine)
        e._log = lambda *a, **k: None
        叫 = []

        def 假ask(message, boss_name, job_name, history=None, 指令="", 用缓存=True):
            叫.append(指令)
            return 依次给出[len(叫) - 1] if len(叫) <= len(依次给出) else ""

        e._ask_ai = 假ask
        e._跟进调用 = 叫
        return e

    def _对话(self, 已发):
        对方 = [句(i) for i in range(1, 4)]
        我 = [{"text": t, "is_mine": True, "mid": str(5000 + i)}
             for i, t in enumerate(已发)]
        return 对方 + 我

    def test_第一稿重复就重问一次(self):
        对话 = self._对话(["这个岗位还在招吗，方便的话约个时间聊聊"])
        e = self._引擎(["这个岗位还在招吗，方便的话约个时间聊聊", "您上次说在忙，现在方便聊聊吗"])
        got = e.现编跟进(对话, "王女士", "数据分析", 兜底句="兜底一句")
        assert got == "您上次说在忙，现在方便聊聊吗", got
        assert len(e._跟进调用) == 2, "只发了一句重复的，没重问"
        assert "说过" in e._跟进调用[1] or "换" in e._跟进调用[1], e._跟进调用[1]

    def test_重问还重复就干脆不发(self):
        """跟进轮不发等于这一轮没损失；发一句一模一样的，就是 HR 眼里的机器人。"""
        对话 = self._对话(["这个岗位还在招吗"])
        e = self._引擎(["这个岗位还在招吗", "这个岗位还在招吗"])
        assert e.现编跟进(对话, "王女士", "数据分析", 兜底句="") == ""

    def test_兜底那句重复也不发(self):
        对话 = self._对话(["这个岗位还在招吗"])
        e = self._引擎(["", "这个岗位还在招吗"])       # AI 没答上来 → 退回兜底
        assert e.现编跟进(对话, "王女士", "数据分析", 兜底句="这个岗位还在招吗") == ""

    def test_没发过话的会话不拦(self):
        """反向哨兵：判据不能一上来就把所有跟进都堵死，那这条腿就白装了。"""
        对话 = [句(i) for i in range(1, 4)]
        e = self._引擎(["之前聊到的岗位我还挺有兴趣，方便约个时间吗"])
        got = e.现编跟进(对话, "王女士", "数据分析", 兜底句="兜底一句")
        assert got.startswith("之前聊到的岗位"), got
        assert len(e._跟进调用) == 1, "不该重问第二趟"

    def test_不一样的那句就该照常发(self):
        """第二条反向哨兵，打在阈值方向上：这段对话里我们说过一句，但新那句和它
        不像（实测 Jaccard 远不到 0.8），照常发。把阈值调成 0 之类"永远算重复"的
        改法要在这里被抓到——不然跟进这条腿会一句都发不出去，还看上去很安全。"""
        对话 = self._对话(["这个岗位还在招吗，方便的话约个时间聊聊"])
        e = self._引擎(["您好，我这边随时可以线上先聊，方便发份作品看看吗"])
        got = e.现编跟进(对话, "王女士", "数据分析", 兜底句="")
        assert got.startswith("您好，我这边"), got
        assert len(e._跟进调用) == 1, "不该为一句不像的话重问"

    def test_照旧不占缓存(self):
        对话 = self._对话(["别的句子"])
        e = self._引擎(["全新的的一句"])
        记下 = []

        def 假ask(message, boss_name, job_name, history=None, 指令="", 用缓存=True):
            记下.append(用缓存)
            return "全新的的一句"

        e._ask_ai = 假ask
        e.现编跟进(对话, "王女士", "数据分析", 兜底句="")
        assert 记下 and 记下[0] is False, "跟进必须现编，不能拿别的会话缓存过的那句"


class Test跟进空句不出门:
    def test_主循环拿到空句就跳过这一单(self):
        """判据打在调用处：引擎回空串是"这句我们说过"，主循环必须不发出去。"""
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        块 = 源.split("text = self._followup_text(item, name, job_name, live)")[1][:700]
        assert "if not text" in 块, "空句没被接住，等于把\"\"发给 HR"

    def test_整段都是系统卡时根本不进AI(self):
        """只改 _split_messages 不够：latest 空着往下走，AI 会自己去"猜该说什么"，
        猜出来的就是"稍后把简历发您"那种凭空承诺。这里要的是当场收住。"""
        e = ReplyEngine.__new__(ReplyEngine)
        e._message_store = None
        e._log = lambda *a, **k: None
        记过 = []
        e._log_decision = lambda *a, **k: 记过.append(a)
        e._ask_ai = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该调 AI"))
        动作, 内容, meta = e.get_reply(
            [{"text": "对方已查看了您的附件简历", "is_mine": False,
              "is_system": True, "mid": "1"}], chat_name="胡女士")
        assert 动作 == "none" and 内容 is None, (动作, 内容)
        assert meta.get("source") == "system_notice", meta
        assert 记过, "拦下了却不留痕，界面与日志都看不见这一单被跳过过"
