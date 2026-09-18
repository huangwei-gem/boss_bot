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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
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
            self._chat(chat_name)["resume_sent"] = True
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
