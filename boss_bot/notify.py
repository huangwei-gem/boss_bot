"""
BOSS 自动回复机器人 - 通知模块

检测面试邀约/offer 等重要事件，发送通知：
- 本地记录 notifications.json（Flask 界面可查看）
- 可选 Webhook 推送（企业微信/飞书/钉钉机器人等，POST JSON）
"""

import json
import logging
import re
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class Notifier:
    """通知发送器"""

    MAX_RECORDS = 100

    def __init__(self, path=None):
        from boss_bot.config import NOTIFY_FILE, NOTIFY_WEBHOOK_URL, NOTIFY_ENABLED
        self.path = Path(path) if path else Path(NOTIFY_FILE)
        self.webhook_url = NOTIFY_WEBHOOK_URL
        self.enabled = NOTIFY_ENABLED
        self._lock = threading.Lock()

    # ---------- 本地记录 ----------

    def _records(self) -> list:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _append(self, record: dict):
        with self._lock:
            records = self._records()
            records.append(record)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(records[-self.MAX_RECORDS:], f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"写入通知记录失败: {e}")

    def records(self, limit: int = 50) -> list:
        return self._records()[-limit:]

    # ---------- 发送 ----------

    def send_notification(self, title: str, content: str, level: str = "info") -> bool:
        """发送通知：写本地记录 + 可选 webhook"""
        record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "title": title,
            "content": content,
        }
        self._append(record)

        if not self.enabled:
            logger.info(f"[通知已禁用] {title}: {content[:50]}")
            return False

        logger.info(f"[通知] {title}: {content[:50]}")

        if self.webhook_url:
            try:
                payload = json.dumps({
                    "title": title,
                    "content": content,
                    "level": level,
                }).encode("utf-8")
                req = urllib.request.Request(
                    self.webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
                logger.info("Webhook 通知已发送")
            except Exception as e:
                logger.warning(f"Webhook 通知发送失败: {e}")
        return True

    # ---------- 重要事件检测 ----------

    def is_important(self, message: str, intent: str = "") -> bool:
        """判断消息是否为重要事件（面试邀约/offer 等）"""
        from boss_bot.config import IMPORTANCE_KEYWORDS
        if intent == "invite_interview":
            return True
        text = (message or "").lower()
        return any(kw.lower() in text for kw in IMPORTANCE_KEYWORDS)

    def notify_if_important(self, message: str, chat_name: str = "", job_name: str = "",
                            intent: str = "") -> bool:
        """检测并通知重要事件，返回是否命中重要事件"""
        if not self.is_important(message, intent):
            return False
        job_part = f"（{job_name}）" if job_name else ""
        self.send_notification(
            title="收到重要消息，建议人工跟进",
            content=f"{chat_name or '招聘方'}{job_part}: {message[:200]}",
            level="important",
        )
        return True


# 通用电话号码提取（可用于日志/通知脱敏等场景）
PHONE_RE = re.compile(r"1[3-9]\d{9}")
