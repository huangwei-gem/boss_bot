"""
BOSS 自动回复机器人 - 消息存储

将所有聊天消息保存为 JSON 文件，供前端展示和回复质量分析。
每个会话一个文件：messages/{chat_name}.json
内置 TTL 内存缓存，减少重复磁盘 IO。
"""

import json
import threading
import time
import re
from datetime import datetime
from pathlib import Path


def _safe_filename(name: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fff]', '_', name)[:50]


class MessageStore:
    """消息存储（JSON 文件持久化 + 内存缓存，线程安全）"""

    CACHE_TTL = 30  # 缓存有效期（秒）

    def __init__(self, base_dir=None):
        from boss_bot.config import BASE_DIR, TEST_MODE
        # 测试模式下隔离目录，避免 mock 测试数据污染前端真实消息列表
        dirname = "messages_test" if TEST_MODE else "messages"
        self.base_dir = Path(base_dir) if base_dir else Path(BASE_DIR) / dirname
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._locks = {}
        self._global_lock = threading.Lock()
        # 内存缓存：chat_name -> (data dict, 时间戳)
        self._cache = {}

    def _get_lock(self, chat_name: str) -> threading.Lock:
        with self._global_lock:
            if chat_name not in self._locks:
                self._locks[chat_name] = threading.Lock()
            return self._locks[chat_name]

    def _path(self, chat_name: str) -> Path:
        return self.base_dir / f"{_safe_filename(chat_name)}.json"

    def _cache_valid(self, chat_name: str) -> bool:
        entry = self._cache.get(chat_name)
        if not entry:
            return False
        return (time.time() - entry[1]) < self.CACHE_TTL

    def _cache_get(self, chat_name: str):
        return self._cache[chat_name][0]

    def _cache_put(self, chat_name: str, data: dict):
        self._cache[chat_name] = (data, time.time())

    def save_messages(self, chat_name: str, messages: list, job_name: str = ""):
        """保存一个会话的完整消息列表（覆盖式）"""
        lock = self._get_lock(chat_name)
        with lock:
            path = self._path(chat_name)
            data = {
                "chat_name": chat_name,
                "job_name": job_name,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "messages": messages,
            }
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._cache_put(chat_name, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"保存消息失败: {e}")

    def append_message(self, chat_name: str, message: dict, job_name: str = ""):
        """追加一条消息到会话文件"""
        lock = self._get_lock(chat_name)
        with lock:
            path = self._path(chat_name)
            try:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    data = {
                        "chat_name": chat_name,
                        "job_name": job_name,
                        "updated_at": "",
                        "messages": [],
                    }
                data["messages"].append(message)
                data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if job_name:
                    data["job_name"] = job_name
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._cache_put(chat_name, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"追加消息失败: {e}")

    def get_messages(self, chat_name: str) -> list:
        """读取一个会话的所有消息（优先走缓存）"""
        if self._cache_valid(chat_name):
            return self._cache_get(chat_name).get("messages", [])
        path = self._path(chat_name)
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache_put(chat_name, data)
                return data.get("messages", [])
        except Exception:
            pass
        return []

    def get_chat_list(self) -> list:
        """获取所有有消息记录的会话列表"""
        result = []
        for path in self.base_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages", [])
                result.append({
                    "chat_name": data.get("chat_name", path.stem),
                    "job_name": data.get("job_name", ""),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(msgs),
                    "last_message": msgs[-1] if msgs else None,
                })
            except Exception:
                pass
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result

    def get_chat_detail(self, chat_name: str) -> dict:
        """获取一个会话的完整详情（含消息列表，优先走缓存）"""
        if self._cache_valid(chat_name):
            return self._cache_get(chat_name)
        path = self._path(chat_name)
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache_put(chat_name, data)
                return data
        except Exception:
            pass
        return {"chat_name": chat_name, "job_name": "", "updated_at": "", "messages": []}
