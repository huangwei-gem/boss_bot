"""
BOSS 自动回复机器人 - 消息存储

将所有聊天消息保存为 JSON 文件，供前端展示和回复质量分析。
每个会话一个文件：messages/{chat_name}.json
内置 TTL 内存缓存，减少重复磁盘 IO。

完整对话消息存储：
- 每条消息包含：sender(HR/bot)、content、timestamp、job_name、is_mine
- 同时保存 HR 发来的消息和机器人回复的消息
- 提供 append_hr_message / append_bot_message 便捷方法
- 提供 get_full_dialog 获取完整对话历史（用于 AI 上下文）
"""

import json
import threading
import time
import re
from datetime import datetime
from pathlib import Path

from boss_bot.unified_config import write_json_atomic


def _safe_filename(name: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fff]', '_', name)[:50]


class MessageStore:
    """消息存储（JSON 文件持久化 + 内存缓存，线程安全）"""

    CACHE_TTL = 30  # 缓存有效期（秒）

    def __init__(self, base_dir=None, account_index: int = 0):
        from boss_bot.config import BASE_DIR, TEST_MODE
        # 测试模式下隔离目录，避免 mock 测试数据污染前端真实消息列表
        dirname = "messages_test" if TEST_MODE else "messages"
        self.base_dir = Path(base_dir) if base_dir else Path(BASE_DIR) / dirname
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # 多账号：BOSS 只显示"杨女士"这类称呼，两个账号聊到同名 HR 时
        # 必须落到不同文件，否则互相覆盖对话历史
        self.account_index = account_index
        self._locks = {}
        self._global_lock = threading.Lock()
        # 内存缓存：chat_name -> (data dict, 时间戳)
        self._cache = {}
        # 跨账号读取时的 chat_name -> path 映射缓存
        self._alias_cache = {}

    def _get_lock(self, chat_name: str) -> threading.Lock:
        with self._global_lock:
            if chat_name not in self._locks:
                self._locks[chat_name] = threading.Lock()
            return self._locks[chat_name]

    @property
    def _prefix(self) -> str:
        """账号0 沿用原文件名，历史数据不用迁移；其余账号加 aN_ 前缀"""
        return "" if self.account_index <= 0 else f"a{self.account_index}_"

    def _path(self, chat_name: str) -> Path:
        return self.base_dir / f"{self._prefix}{_safe_filename(chat_name)}.json"

    def _read_path(self, chat_name: str) -> Path:
        """读取用路径：先读本账号的，没有再按 chat_name 找别人的会话文件。

        Web 端默认用账号0 的实例去浏览全部聊天，需要这个回落。
        """
        own = self._path(chat_name)
        if own.exists():
            return own
        hit = self._alias_cache.get(chat_name)
        if hit and hit.exists():
            return hit
        for path in sorted(self.base_dir.glob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            if data.get("chat_name") == chat_name:
                self._alias_cache[chat_name] = path
                return path
        return own

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
                "account_index": self.account_index,
                "job_name": job_name,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "messages": messages,
            }
            try:
                write_json_atomic(path, data)
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
                        "account_index": self.account_index,
                        "job_name": job_name,
                        "updated_at": "",
                        "messages": [],
                    }
                data["messages"].append(message)
                data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if job_name:
                    data["job_name"] = job_name
                write_json_atomic(path, data)
                self._cache_put(chat_name, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"追加消息失败: {e}")

    @staticmethod
    def _msg_key(msg: dict) -> str:
        """构造消息去重键。

        优先用 BOSS 给每条消息的 data-mid（线上唯一且稳定）；没有 mid 的
        旧数据退回 content+time。HR 重复发同一句话时 content+time 会把两条
        合并成一条，导致界面和线上对不上。
        """
        mid = str(msg.get("mid") or "").strip()
        if mid:
            return f"mid|{mid}"
        content = (msg.get("content") or msg.get("text") or "").strip()
        t = (msg.get("time") or msg.get("timestamp") or "").strip()
        return f"{content}|{t}"

    @staticmethod
    def _parse_time_for_sort(msg: dict) -> float:
        """从消息中解析时间用于排序，失败返回 0.0。

        支持多种格式：
        - HH:MM（当天时间）
        - YYYY-MM-DD HH:MM:SS
        - ISO 格式（带 T）
        - YYYY-MM-DD HH:MM
        """
        t = msg.get("time") or msg.get("timestamp") or ""
        if not t:
            return 0.0
        t = t.strip()
        # 尝试常见格式
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%H:%M",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(t, fmt)
                # 仅时间格式（HH:MM）视为当天
                if fmt == "%H:%M":
                    dt = dt.replace(
                        year=datetime.now().year,
                        month=datetime.now().month,
                        day=datetime.now().day,
                    )
                return dt.timestamp()
            except ValueError:
                continue
        # 尝试 ISO 解析
        try:
            return datetime.fromisoformat(t).timestamp()
        except Exception:
            return 0.0

    def merge_messages(self, chat_name: str, new_messages: list,
                       job_name: str = "") -> int:
        """将页面读取的新消息与已存储消息合并去重后保存。

        去重逻辑：按 content+time 去重（相同内容和时间的消息视为同一条）。
        合并后按时间排序（若能解析时间），保存合并后的完整消息列表。

        Args:
            chat_name: 聊天对象名称
            new_messages: 页面读取的新消息列表
            job_name: 岗位名称

        Returns:
            合并后的消息总数
        """
        if not new_messages:
            return len(self.get_messages(chat_name))

        lock = self._get_lock(chat_name)
        with lock:
            path = self._path(chat_name)
            try:
                # 读取已存储的消息
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    data = {
                        "chat_name": chat_name,
                        "account_index": self.account_index,
                        "job_name": job_name,
                        "updated_at": "",
                        "messages": [],
                    }

                existing_msgs = data.get("messages", [])

                # 按 content+time 去重合并
                seen_keys = set()
                merged = []

                # 先放入已存储消息
                for msg in existing_msgs:
                    key = self._msg_key(msg)
                    # 已存储消息可能也有重复，去重
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    merged.append(msg)

                # 再合并新消息
                new_added = 0
                for msg in new_messages:
                    # 规范化字段：确保同时有 content/text 和 is_mine/isFriend
                    content = (msg.get("content") or msg.get("text") or "").strip()
                    if not content:
                        continue
                    norm_msg = dict(msg)
                    norm_msg["content"] = content
                    norm_msg["text"] = content
                    # is_mine / isFriend 互补
                    if "is_mine" in msg and "isFriend" not in msg:
                        norm_msg["isFriend"] = not msg["is_mine"]
                    elif "isFriend" in msg and "is_mine" not in msg:
                        norm_msg["is_mine"] = not msg["isFriend"]
                    # sender 字段（用于前端区分气泡）
                    if "sender" not in norm_msg:
                        norm_msg["sender"] = "hr" if not norm_msg.get("is_mine", False) else "bot"

                    key = self._msg_key(norm_msg)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    merged.append(norm_msg)
                    new_added += 1

                # 按时间排序，但岗位卡/简历卡这类消息 BOSS 不给时间戳：
                # 直接丢到末尾会让它们和线上顺序错位（线上是夹在对话中间的），
                # 所以沿用 DOM 里前一条有时间消息的时间，保持原有相对位置
                last_ts = 0.0
                effective = []
                for idx, m in enumerate(merged):
                    ts = self._parse_time_for_sort(m)
                    if ts > 0:
                        last_ts = ts
                        effective.append((ts, idx))
                    else:
                        effective.append((last_ts if last_ts > 0 else 0.0, idx))
                # 开头就没有时间的，向前借用第一条有时间消息的时间，避免整段被排到最后
                first_ts = next((t for t, _ in effective if t > 0), 0.0)
                effective = [(t if t > 0 else first_ts, i) for t, i in effective]
                order = sorted(range(len(merged)), key=lambda i: effective[i])
                merged = [merged[i] for i in order]

                # 保存合并后的完整消息列表
                data["messages"] = merged
                data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if job_name:
                    data["job_name"] = job_name
                write_json_atomic(path, data)
                self._cache_put(chat_name, data)

                import logging
                logging.getLogger(__name__).info(
                    f"[merge_messages] chat={chat_name} "
                    f"existing={len(existing_msgs)} new={len(new_messages)} "
                    f"new_added={new_added} merged_total={len(merged)}"
                )
                return len(merged)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"合并消息失败: {e}")
                return len(self.get_messages(chat_name))

    def append_hr_message(self, chat_name: str, content: str,
                          job_name: str = "", timestamp: str = "",
                          extra: dict = None) -> dict:
        """追加一条 HR 发来的消息（左侧气泡）。

        Args:
            chat_name: 聊天对象名称
            content: 消息文本内容
            job_name: 岗位名称
            timestamp: 时间戳，为空则取当前时间
            extra: 额外字段（如 raw_time）

        Returns:
            构造的消息 dict
        """
        # 统一时间戳格式为 YYYY-MM-DD HH:MM:SS
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_short = ts[11:16] if len(ts) >= 16 else datetime.now().strftime("%H:%M")
        msg = {
            "sender": "hr",
            "content": content,
            "text": content,
            "is_mine": False,
            "timestamp": ts,
            "time": time_short,
            "job_name": job_name,
        }
        if extra:
            msg.update(extra)
        self.append_message(chat_name, msg, job_name)
        return msg

    def append_bot_message(self, chat_name: str, content: str,
                           job_name: str = "", timestamp: str = "",
                           reply_source: str = "", action: str = "text",
                           extra: dict = None) -> dict:
        """追加一条机器人回复消息（右侧气泡）。

        Args:
            chat_name: 聊天对象名称
            content: 回复内容
            job_name: 岗位名称
            timestamp: 时间戳，为空则取当前时间
            reply_source: 回复来源（rule/intent/ai/default/skip）
            action: 动作类型（text/resume/skip）
            extra: 额外字段

        Returns:
            构造的消息 dict
        """
        # 统一时间戳格式为 YYYY-MM-DD HH:MM:SS
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = {
            "sender": "bot",
            "content": content,
            "text": content,
            "is_mine": True,
            "source": "bot",
            "reply_source": reply_source,
            "action": action,
            "timestamp": ts,
            "time": ts[11:16] if len(ts) >= 16 else datetime.now().strftime("%H:%M"),
            "job_name": job_name,
        }
        if extra:
            msg.update(extra)
        self.append_message(chat_name, msg, job_name)
        return msg

    def append_skip_record(self, chat_name: str, skip_reason: str,
                           job_name: str = "", received_message: str = "",
                           timestamp: str = "") -> dict:
        """追加一条跳过记录到会话（用于完整对话上下文）。

        Args:
            chat_name: 聊天对象名称
            skip_reason: 跳过原因
            job_name: 岗位名称
            received_message: 收到的消息（可能为空）
            timestamp: 时间戳

        Returns:
            构造的消息 dict
        """
        # 统一时间戳格式为 YYYY-MM-DD HH:MM:SS
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = {
            "sender": "system",
            "content": f"[跳过] {skip_reason}",
            "text": f"[跳过] {skip_reason}",
            "is_mine": False,
            "is_skipped": True,
            "skip_reason": skip_reason,
            "received_message": received_message,
            "timestamp": ts,
            "time": ts[11:16] if len(ts) >= 16 else datetime.now().strftime("%H:%M"),
            "job_name": job_name,
        }
        self.append_message(chat_name, msg, job_name)
        return msg

    def get_messages(self, chat_name: str) -> list:
        """读取一个会话的所有消息（优先走缓存）"""
        if self._cache_valid(chat_name):
            return self._cache_get(chat_name).get("messages", [])
        path = self._read_path(chat_name)
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache_put(chat_name, data)
                return data.get("messages", [])
        except Exception:
            pass
        return []

    def get_full_dialog(self, chat_name: str, limit: int = 50) -> list:
        """获取完整对话历史（用于 AI 上下文）。

        Args:
            chat_name: 聊天对象名称
            limit: 最多返回消息数

        Returns:
            消息列表，每条含 sender/content/timestamp/is_mine 等字段
        """
        msgs = self.get_messages(chat_name)
        if limit > 0 and len(msgs) > limit:
            return msgs[-limit:]
        return msgs

    def get_chat_list(self) -> list:
        """获取所有有消息记录的会话列表（含各账号的会话）"""
        result = []
        for path in self.base_dir.glob("*.json"):
            if path.name.endswith(".meta.json"):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages", [])
                m = re.match(r"^a(\d+)_", path.name)
                result.append({
                    "chat_name": data.get("chat_name", path.stem),
                    "job_name": data.get("job_name", ""),
                    "account_index": data.get(
                        "account_index", int(m.group(1)) if m else 0),
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
        path = self._read_path(chat_name)
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache_put(chat_name, data)
                return data
        except Exception:
            pass
        return {"chat_name": chat_name, "job_name": "", "updated_at": "", "messages": []}

    def get_all_chats_detail(self) -> list:
        """获取所有会话的完整详情列表（用于前端聊天界面展示）。

        每个会话包含：
        - chat_name: 聊天对象名称
        - job_name: 岗位名称
        - updated_at: 最后更新时间
        - message_count: 消息总数
        - unread_count: 未读数（HR 发送且未读的消息数）
        - last_message: 最新消息内容
        - last_time: 最新消息时间
        - messages: 完整消息列表

        Returns:
            按最后更新时间倒序排列的会话详情列表
        """
        result = []
        for path in self.base_dir.glob("*.json"):
            if path.name.endswith(".meta.json"):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages", [])
                chat_name = data.get("chat_name", path.stem)
                m = re.match(r"^a(\d+)_", path.name)
                account_index = data.get(
                    "account_index", int(m.group(1)) if m else 0)
                # 计算未读数：sender=hr 且未读标记
                unread_count = sum(1 for m in msgs
                                    if m.get("sender") == "hr"
                                    and not m.get("is_read", False)
                                    and not m.get("is_mine", False))
                # 最新消息
                last_msg = msgs[-1] if msgs else None
                last_time = ""
                last_content = ""
                if last_msg:
                    last_time = last_msg.get("timestamp", "") or last_msg.get("time", "")
                    last_content = last_msg.get("content", "") or last_msg.get("text", "")
                result.append({
                    "chat_name": chat_name,
                    "account_index": account_index,
                    "job_name": data.get("job_name", ""),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(msgs),
                    "unread_count": unread_count,
                    "last_message": last_content,
                    "last_time": last_time,
                    "messages": msgs,
                })
            except Exception:
                pass
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result

    def mark_chat_read(self, chat_name: str):
        """标记一个会话的所有 HR 消息为已读。"""
        lock = self._get_lock(chat_name)
        with lock:
            path = self._read_path(chat_name)
            try:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for m in data.get("messages", []):
                        if m.get("sender") == "hr" or not m.get("is_mine", False):
                            m["is_read"] = True
                    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    write_json_atomic(path, data)
                    self._cache_put(chat_name, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"标记已读失败: {e}")
