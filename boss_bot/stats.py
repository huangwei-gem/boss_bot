"""
BOSS 自动回复机器人 - 统计数据持久化

按天记录回复量、来源分布、动作分布、重要事件等，用于复盘求职效果。
"""

import json
import threading
from datetime import datetime, date
from pathlib import Path


def _empty_day() -> dict:
    return {
        "replies": 0,
        "by_source": {"rule": 0, "intent": 0, "ai": 0, "default": 0},
        "by_action": {"text": 0, "resume": 0, "skip": 0},
        "important_events": 0,
        "skipped_duplicates": 0,
    }


class Stats:
    """统计数据存储（JSON 文件持久化，线程安全）"""

    def __init__(self, path=None):
        from boss_bot.config import STATS_FILE
        self.path = Path(path) if path else Path(STATS_FILE)
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"days": {}, "total": _empty_day()}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"保存统计失败: {e}")

    def _day(self) -> dict:
        today = date.today().isoformat()
        if today not in self._data["days"]:
            self._data["days"][today] = _empty_day()
            # 只保留最近 30 天
            keys = sorted(self._data["days"].keys())
            for k in keys[:-30]:
                del self._data["days"][k]
        return self._data["days"][today]

    # ---------- 记录 ----------

    def record_reply(self, source: str = "rule", action: str = "text"):
        """记录一次回复。source: rule/intent/ai/default；action: text/resume"""
        with self._lock:
            for bucket in (self._day(), self._data["total"]):
                bucket["replies"] += 1
                bucket["by_source"][source] = bucket["by_source"].get(source, 0) + 1
                bucket["by_action"][action] = bucket["by_action"].get(action, 0) + 1
            self._save()

    def record_skip(self):
        """记录一次重复消息跳过"""
        with self._lock:
            for bucket in (self._day(), self._data["total"]):
                bucket["skipped_duplicates"] += 1
            self._save()

    def record_important(self):
        """记录一次重要事件"""
        with self._lock:
            for bucket in (self._day(), self._data["total"]):
                bucket["important_events"] += 1
            self._save()

    # ---------- 查询 ----------

    def today(self) -> dict:
        with self._lock:
            today = date.today().isoformat()
            return self._data["days"].get(today, _empty_day())

    def total(self) -> dict:
        with self._lock:
            return dict(self._data["total"])

    def summary(self) -> dict:
        return {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "today": self.today(),
            "total": self.total(),
        }
