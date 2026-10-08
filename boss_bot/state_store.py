"""
BOSS 自动回复机器人 - 会话状态持久化

记录每个会话的处理状态：
- 已处理过的消息（防重启后重复回复）
- 已发送过简历的会话（防重复发简历）
- 人工接管暂停状态
"""

import json
import hashlib
import threading
from datetime import datetime
from pathlib import Path


class StateStore:
    """会话状态存储（JSON 文件持久化，线程安全）"""

    def __init__(self, path=None):
        from boss_bot.config import STATE_FILE
        self.path = Path(path) if path else Path(STATE_FILE)
        self._lock = threading.Lock()
        self._data = self._load()

    # ---------- 内部 ----------

    def _load(self) -> dict:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"chats": {}, "paused": {"paused": False, "reason": "", "at": "", "chat": ""}}

    def _save(self):
        try:
            from boss_bot.unified_config import write_json_atomic
            write_json_atomic(self.path, self._data)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"保存状态失败: {e}")

    @staticmethod
    def _hash(chat_name: str, message: str) -> str:
        raw = f"{chat_name}||{message.strip()}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    def _chat(self, chat_name: str) -> dict:
        return self._data["chats"].setdefault(chat_name, {
            "last_msg_hash": "",
            "last_msg": "",
            "last_reply_at": "",
            "last_action": "",
            "resume_sent": False,
        })

    # ---------- 消息处理去重 ----------

    def was_handled(self, chat_name: str, message: str) -> bool:
        """该会话的该条消息是否已处理过"""
        with self._lock:
            h = self._hash(chat_name, message)
            return self._chat(chat_name).get("last_msg_hash") == h

    def mark_handled(self, chat_name: str, message: str, action: str):
        """记录已处理的消息"""
        with self._lock:
            chat = self._chat(chat_name)
            chat["last_msg_hash"] = self._hash(chat_name, message)
            chat["last_msg"] = message[:100]
            chat["last_reply_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            chat["last_action"] = action
            self._save()

    # ---------- 简历发送记录 ----------

    def resume_sent(self, chat_name: str) -> bool:
        with self._lock:
            return bool(self._chat(chat_name).get("resume_sent"))

    def mark_resume_sent(self, chat_name: str):
        with self._lock:
            chat = self._chat(chat_name)
            chat["resume_sent"] = True
            # 落时间戳=这条是"点确认前数一遍、点完卡片多出一条"验过的。
            # 盘上旧标记没有这个键，那是假判据写的，只配按存档证据重判。
            chat["resume_sent_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save()

    def resume_sent_at(self, chat_name: str) -> str:
        """这一单"已发"标记落下的时间；旧标记没有，返回空串。"""
        with self._lock:
            return str(self._chat(chat_name).get("resume_sent_at") or "")

    def clear_resume_sent(self, chat_name: str):
        """清掉这一单的"已发"标记。

        2026-10-08 数过：盘上标了已发的会话 81 个，按姓名回存档里查卡片，
        有 4 个（每号 2 个）压根没有卡片——那是旧假判据（消息里找"简历"两字）
        写下的，照着它去重，欠的简历就永远补不回来。带时间戳的新标记不走这里。
        """
        with self._lock:
            self._chat(chat_name)["resume_sent"] = False
            self._save()

    # ---------- 转人工暂停 ----------

    def is_paused(self) -> bool:
        with self._lock:
            return bool(self._data.get("paused", {}).get("paused"))

    def pause(self, reason: str = "", chat_name: str = ""):
        with self._lock:
            self._data["paused"] = {
                "paused": True,
                "reason": reason,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "chat": chat_name,
            }
            self._save()

    def resume(self) -> bool:
        """恢复自动回复，返回是否有暂停被恢复"""
        with self._lock:
            paused = self._data.get("paused", {})
            was = bool(paused.get("paused"))
            self._data["paused"] = {"paused": False, "reason": "", "at": "", "chat": ""}
            self._save()
            return was

    def pause_info(self) -> dict:
        with self._lock:
            return dict(self._data.get("paused", {}))
