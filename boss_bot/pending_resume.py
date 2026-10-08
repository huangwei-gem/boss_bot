"""挑出"HR 在要简历、但我们还没发出去"的会话。

回复轮只点未读会话，而这些会话早就被点开回过一句文字，红点没了 ——
不主动补扫，那句"稍后把简历整理好发给您"就永远欠着（2026-10-02 江女士 | 孤波）。

这里只做纯文本判定，不碰浏览器：输入是 message_store 的会话存档形状，
输出是要补发的会话清单，由主循环拿它去侧栏定位并走正常回复链发送。
"""
from __future__ import annotations

from boss_bot.intent import is_resume_request

# 简历确实送到对方手里的痕迹：**只有 BOSS 侧的卡片算证据**。
# 实测这些卡 is_mine 为 False，所以只看内容不看方向。
#
# 以前这里还认 "[简历已发送]"——那是我们发送成功后自己写的一行。
# 2026-10-08 按号现数：状态里标了"已发"的会话 82 个（号0 34 / 号1 48），
# 同一账号存档里真有附件卡片的 76 个，剩下 6 个（刘先生、张墨晗、段中帅、
# 李女士、楚仪可、葛先生）压根没卡片，全是旧送达判据（在消息里找"简历"两个字）
# 判成功的。拿自己写的行自证，等于"我说发了就算发了"，欠的简历永远补不回来。
RESUME_SENT_MARKS = (
    "附件简历已发送",
    "附件简历请求已发送",
    "已发送给Boss",
    "点击预览附件简历",
)

# 我们自记的动作行：只在界面上当"机器动过手"的标记用，不作送达证据
RESUME_SELF_CLAIM = "[简历已发送]"


def _body(msg: dict) -> str:
    return ((msg.get("text") or "") or (msg.get("card_text") or "")).strip()


def latest_other_message(messages) -> str:
    """最后一条对方消息（卡片正文在 card_text，只看 text 会把卡片当成没说话）"""
    for msg in reversed(messages or []):
        if msg.get("is_mine"):
            continue
        body = _body(msg)
        if body:
            return body
    return ""


def resume_already_sent(messages) -> bool:
    return any(any(k in _body(m) for k in RESUME_SENT_MARKS)
               for m in (messages or []))


def pending_resume_asks(conversations) -> list:
    """conversations: [{chat_name, job_name, company, messages}]
    → [{chat_name, job_name, company, ask}]，ask 是该会话最后一条对方消息。
    """
    pending = []
    for conv in conversations or []:
        messages = conv.get("messages") or []
        ask = latest_other_message(messages)
        if not ask or not is_resume_request(ask):
            continue
        if resume_already_sent(messages):
            continue
        pending.append({
            "chat_name": conv.get("chat_name", ""),
            "job_name": conv.get("job_name", ""),
            "company": conv.get("company", ""),
            "ask": ask,
        })
    return pending
