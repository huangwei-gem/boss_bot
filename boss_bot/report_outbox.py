"""要汇报给本人的事，先落成一个待发队列，由 Qoder 这边送进微信。

用户 2026-10-08：「以后有面试邀请，或者给了什么联系方式都给我汇报一声，
汇报的内容包括它的 JD、姓名、岗位、公司等基本基本信息，以及你再末尾的一句 AI 总结」。

为什么不在机器人进程里直接发微信：微信 PC 客户端没有对外接口，能稳定驱动它的
是 Qoder 的 computer-use（桌面窗口自动化）。所以机器人只负责**判事**——把
"该汇报了"这件事写成一条待发送记录；发送由 Qoder 侧定时读队列、贴进微信、
再回来标记已发。这样机器人不用碰微信，微信那边也不用装任何插件。

判据全部复用台账那一份（contact_rows），不另起一套口径：
台账说"给了号码"，汇报就说给了号码；台账说"卡片已同意"，汇报就跟着说。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from boss_bot.contact_ledger import contact_rows
from boss_bot.intent import classify_interview_invite, title_veto_hit

# 已发过什么，记在这里；只增不删（超过 _SEEN_CAP 丢最旧的）
SEEN_FILE = Path(__file__).resolve().parent.parent / "data" / "wechat_reported.json"
_SEEN_CAP = 3000

# 一条汇报要凑齐的字段（缺的就空着，不编）
_FIELDS = ("account_index", "chat_name", "company", "job_title", "salary", "city",
           "phones", "wechats", "hr_last_message", "matched_quote", "last_activity")


def _key(kind: str, row: dict, payload: str = "") -> str:
    """同一条会话的同一件事只报一次：账号+姓名+公司+事件(+内容指纹)。

    联系方式要带指纹——HR 先给微信再给电话，是两件事，都该报。
    """
    base = "%s|%s|%s|%s|%s" % (row.get("account_index"), row.get("chat_name"),
                               (row.get("company") or "").strip(), kind, payload)
    return hashlib.md5(base.encode("utf-8")).hexdigest()[:16]


def _load_seen(path: Path) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("keys") or []) if isinstance(data, dict) else list(data)
    except FileNotFoundError:
        return []
    except Exception:
        return []


def _save_seen(keys: list, path: Path) -> None:
    """原子写：这个文件记的是"已经汇报过什么"，写坏了就会把同一件事再报一遍。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"keys": keys[-_SEEN_CAP:]}, f, ensure_ascii=False)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _row_view(row: dict) -> dict:
    return {k: row.get(k) for k in _FIELDS if row.get(k) not in (None, "", [])}


def _veto_tables():
    """岗位类型否决词表：汇报里不念这一类，他说过"看到这种就 pass"。
    读不到配置就返回空表（宁可多报一条，也不因为配置炸了就不汇报）。"""
    try:
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig.load()
        return list(cfg.ai.title_veto_keywords), list(cfg.ai.custom_filter_keywords)
    except Exception:
        return [], []


def new_reports(chats, greet_rows=None, interview_chats=None, seen=None):
    """→ (要汇报的列表, 新的 seen 列表)

    interview_chats: [{account_index, chat_name, company, job_name, message}]
      面试邀请由调用方按会话扫出来传进来（这里不碰浏览器也不读存档结构）。
    """
    标题词, 正文词 = _veto_tables()
    seen = list(seen if seen is not None else _load_seen(SEEN_FILE))
    seen_set = set(seen)
    out = []

    for row in contact_rows(chats, greet_rows) or []:
        if title_veto_hit(标题词, row.get("job_title") or ""):
            continue        # 普工/主播/快递这一类不往微信上刷
        号码 = list(row.get("phones") or []) + list(row.get("wechats") or [])
        if 号码:
            k = _key("联系方式", row, ",".join(sorted(号码)))
            if k not in seen_set:
                seen_set.add(k); seen.append(k)
                out.append(dict(_k=k, event="联系方式", 标题="HR 给了联系方式",
                                **_row_view(row), contacts=号码))
        if row.get("contact_kind") == "发起交换请求":
            k = _key("交换请求", row)
            if k not in seen_set:
                seen_set.add(k); seen.append(k)
                out.append(dict(_k=k, event="交换请求", 标题="HR 发起交换微信/电话",
                                **_row_view(row)))
        if row.get("resume_sent"):
            k = _key("简历送达", row, str(row.get("last_activity")))
            if k not in seen_set:
                seen_set.add(k); seen.append(k)
                out.append(dict(_k=k, event="简历送达", 标题="简历已送到 HR 手里",
                                **_row_view(row)))

    for row in interview_chats or []:
        if title_veto_hit(标题词, row.get("job_name") or ""):
            continue        # 这一类岗位的面试邀请不报（机器已按口径拒）
        kind = classify_interview_invite(row.get("message") or "")
        if kind == "unknown":
            continue        # 说不清的不报，报错了是拿他的面试开玩笑
        k = _key("面试", row, str(row.get("message")))
        if k in seen_set:
            continue
        seen_set.add(k); seen.append(k)
        out.append(dict(_k=k, event="面试邀请", kind=kind,
                        标题=("线下面试邀请（按口径已拒/待你决定）" if kind == "offline"
                              else "线上面试邀请"),
                        account_index=row.get("account_index"),
                        chat_name=row.get("chat_name"), company=row.get("company"),
                        job_title=row.get("job_name"), hr_last_message=row.get("message"),
                        last_activity=row.get("last_activity")))
    return out, seen


def format_report(r: dict) -> str:
    """一条汇报的正文（末尾那句 AI 总结由发送方补，这里只给事实）。"""
    行 = [f"【{r.get('标题') or r.get('event')}】"]
    行.append(f"{r.get('chat_name') or '?'}｜{r.get('company') or '公司未记'}")
    if r.get("job_title"):
        行.append(f"岗位：{str(r['job_title'])[:40]}")
    薪 = " · ".join(x for x in (r.get("salary"), r.get("city")) if x)
    if 薪:
        行.append(f"薪资·城市：{薪}")
    if r.get("contacts"):
        行.append("联系方式：" + "、".join(r["contacts"]))
    if r.get("hr_last_message"):
        行.append(f"对方原话：{str(r['hr_last_message'])[:70]}")
    行.append(f"账号：{r.get('account_index')}｜时间：{r.get('last_activity') or ''}")
    return "\n".join(行)


def scan_interviews(chats, account_index=None):
    """从会话存档里挑出面试邀请（取每个会话最后一条对方说的话）。"""
    from boss_bot.contact_ledger import _is_from_hr, _message_text
    out = []
    for chat in chats or []:
        if account_index is not None and chat.get("account_index") != account_index:
            continue
        msgs = chat.get("messages") or []
        for m in reversed(msgs):
            if not _is_from_hr(m):
                continue
            文 = _message_text(m)
            if not 文:
                continue
            if classify_interview_invite(文) != "unknown":
                out.append({"account_index": chat.get("account_index"),
                            "chat_name": chat.get("chat_name"),
                            "company": chat.get("company"),
                            "job_name": chat.get("job_name"),
                            "message": 文,
                            "last_activity": chat.get("updated_at") or ""})
            break
    return out


def commit_seen(keys: list, path: Path = SEEN_FILE) -> None:
    """发送方贴完微信后调用：把这些键记成已汇报。"""
    合 = _load_seen(path) + list(keys)
    去重 = list(dict.fromkeys(合))
    _save_seen(去重[-_SEEN_CAP:], path)


def digest_text(reports, stamp="") -> str:
    """把一批汇报打成一条微信消息（一行一条，末尾一句总结）。

    一条一消息会把他的微信刷爆；分三段、每段一行一条，他扫一眼就知道要接哪个。
    """
    if not reports:
        return ""
    组 = {}
    for r in reports:
        组.setdefault(r.get("event") or "其他", []).append(r)
    行 = [f"BOSS 汇报 {stamp}".strip()]
    顺序 = ("面试邀请", "联系方式", "交换请求", "简历送达")
    for 事件 in 顺序:
        条 = 组.get(事件) or []
        if not 条:
            continue
        行.append("\n【{}】{} 条".format(事件, len(条)))
        for r in 条[:40]:
            摘 = "｜".join(x for x in (r.get("chat_name"), r.get("company"),
                                     str(r.get("job_title") or "")[:18]) if x)
            尾 = ""
            if 事件 == "联系方式" and r.get("contacts"):
                尾 = " → " + "、".join(r["contacts"])
            elif r.get("hr_last_message"):
                尾 = " ← " + str(r["hr_last_message"])[:26]
            行.append(f"{摘}{尾}")
        if len(条) > 40:
            行.append(f"…另有 {len(条) - 40} 条")
    面 = len(组.get("面试邀请") or [])
    号 = len(组.get("联系方式") or [])
    行.append("\n总结：{} 个面试要盯（线下的已按口径拒），{} 个 HR 给了联系方式待你加。"
              .format(面, 号))
    return "\n".join(行)
