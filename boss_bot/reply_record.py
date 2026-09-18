"""
BOSS 自动回复机器人 - 回复记录系统

详细记录每次回复决策和打招呼/AI分析的全过程信息，
方便用户评测和AI自进化。

两类记录：
1. ReplyRecord — 回复记录：收到HR消息并做出回复（或决定不回复）时记录
2. GreetRecord — 打招呼/AI分析记录：AI分析岗位匹配时记录

持久化：
- data/reply_records.json
- data/greet_records.json
- 线程锁保证并发安全
- 支持按日期、账号、聊天对象筛选
- 自动截断过长内容

导出：
- JSON 格式
- Excel 格式（需安装 openpyxl）
"""

import json
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from boss_bot.unified_config import BASE_DIR

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 路径常量
# ─────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

REPLY_RECORDS_FILE = DATA_DIR / "reply_records.json"
GREET_RECORDS_FILE = DATA_DIR / "greet_records.json"

# 内容截断阈值
MAX_JOB_DESCRIPTION_LEN = 500
MAX_JOB_REQUIREMENTS_LEN = 500
MAX_RECEIVED_MESSAGE_LEN = 500
MAX_REPLY_CONTENT_LEN = 500
MAX_SYSTEM_PROMPT_LEN = 2000
MAX_USER_PROMPT_LEN = 2000
MAX_AI_RAW_RESPONSE_LEN = 1000
MAX_REPLY_REASON_LEN = 500
MAX_AI_REASON_LEN = 500
MAX_AI_SUGGESTED_GREETING_LEN = 200
MAX_ACTUAL_GREETING_LEN = 200
MAX_SKIP_REASON_LEN = 200
MAX_STRENGTHS_LEN = 300
MAX_WEAKNESSES_LEN = 300

# 保留最近记录数（防止文件无限增长）
MAX_REPLY_RECORDS = 5000
MAX_GREET_RECORDS = 5000


def _truncate(text: Optional[str], max_len: int) -> Optional[str]:
    """截断过长内容，末尾添加截断标记。"""
    if text is None:
        return None
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...[截断]"


# ─────────────────────────────────────────────
# ReplyRecord — 回复记录
# ─────────────────────────────────────────────

class ReplyRecord:
    """单条回复记录。

    记录每次收到HR消息并做出回复（或决定不回复）时的完整信息。

    Attributes:
        timestamp: 时间戳
        chat_name: 聊天对象名称（HR名称）
        job_name: 岗位名称
        received_message: 收到的消息原文
        reply_content: AI/规则回复的内容
        reply_source: 回复来源 (rule/intent/ai/default/skip)
        reply_intent: 识别到的意图
        reply_reason: 为什么这样回复（判断依据的详细说明）
        system_prompt: AI回复时使用的系统提示词
        user_prompt: AI回复时使用的用户提示词（含对话历史）
        ai_model: 使用的AI模型名称
        ai_raw_response: AI原始返回内容
        is_skipped: 是否跳过了回复
        skip_reason: 跳过原因
        account_name: 账号名称
        account_index: 账号索引
    """

    def __init__(
        self,
        chat_name: str = "",
        job_name: str = "",
        received_message: str = "",
        reply_content: Optional[str] = None,
        reply_source: str = "",
        reply_intent: str = "",
        reply_reason: str = "",
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        ai_model: str = "",
        ai_raw_response: Optional[str] = None,
        is_skipped: bool = False,
        skip_reason: str = "",
        account_name: str = "",
        account_index: int = 0,
        timestamp: Optional[str] = None,
    ):
        self.timestamp = timestamp or datetime.now().isoformat()
        self.chat_name = chat_name
        self.job_name = job_name
        self.received_message = _truncate(received_message, MAX_RECEIVED_MESSAGE_LEN)
        self.reply_content = _truncate(reply_content, MAX_REPLY_CONTENT_LEN)
        self.reply_source = reply_source
        self.reply_intent = reply_intent
        self.reply_reason = _truncate(reply_reason, MAX_REPLY_REASON_LEN)
        self.system_prompt = _truncate(system_prompt, MAX_SYSTEM_PROMPT_LEN)
        self.user_prompt = _truncate(user_prompt, MAX_USER_PROMPT_LEN)
        self.ai_model = ai_model
        self.ai_raw_response = _truncate(ai_raw_response, MAX_AI_RAW_RESPONSE_LEN)
        self.is_skipped = is_skipped
        self.skip_reason = _truncate(skip_reason, MAX_SKIP_REASON_LEN)
        self.account_name = account_name
        self.account_index = account_index

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "chat_name": self.chat_name,
            "job_name": self.job_name,
            "received_message": self.received_message,
            "reply_content": self.reply_content,
            "reply_source": self.reply_source,
            "reply_intent": self.reply_intent,
            "reply_reason": self.reply_reason,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "ai_model": self.ai_model,
            "ai_raw_response": self.ai_raw_response,
            "is_skipped": self.is_skipped,
            "skip_reason": self.skip_reason,
            "account_name": self.account_name,
            "account_index": self.account_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplyRecord":
        return cls(
            timestamp=data.get("timestamp"),
            chat_name=data.get("chat_name", ""),
            job_name=data.get("job_name", ""),
            received_message=data.get("received_message", ""),
            reply_content=data.get("reply_content"),
            reply_source=data.get("reply_source", ""),
            reply_intent=data.get("reply_intent", ""),
            reply_reason=data.get("reply_reason", ""),
            system_prompt=data.get("system_prompt"),
            user_prompt=data.get("user_prompt"),
            ai_model=data.get("ai_model", ""),
            ai_raw_response=data.get("ai_raw_response"),
            is_skipped=data.get("is_skipped", False),
            skip_reason=data.get("skip_reason", ""),
            account_name=data.get("account_name", ""),
            account_index=data.get("account_index", 0),
        )


# ─────────────────────────────────────────────
# GreetRecord — 打招呼/AI分析记录
# ─────────────────────────────────────────────

class GreetRecord:
    """单条打招呼/AI分析记录。

    记录每次AI分析岗位匹配时的完整信息。

    Attributes:
        timestamp: 时间戳
        job_name: 岗位名称
        job_url: 岗位链接
        company: 公司名称
        salary: 薪资
        job_description: 岗位描述
        job_requirements: 任职要求
        ai_score: AI匹配评分 (0-100)
        ai_is_match: 是否匹配
        ai_reason: AI匹配理由
        ai_strengths: 优势列表
        ai_weaknesses: 劣势列表
        ai_suggested_greeting: AI建议的打招呼语
        system_prompt: AI分析使用的系统提示词
        user_prompt: AI分析使用的用户提示词
        ai_model: 使用的AI模型
        ai_raw_response: AI原始返回内容
        actual_greeting_sent: 实际发送的打招呼语
        is_greeted: 是否成功打招呼
        is_skipped: 是否跳过
        skip_reason: 跳过原因
        account_name: 账号名称
    """

    def __init__(
        self,
        job_name: str = "",
        job_url: str = "",
        company: str = "",
        salary: str = "",
        job_description: str = "",
        job_requirements: str = "",
        ai_score: int = 0,
        ai_is_match: bool = False,
        ai_reason: str = "",
        ai_strengths: Optional[List[str]] = None,
        ai_weaknesses: Optional[List[str]] = None,
        ai_suggested_greeting: str = "",
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        ai_model: str = "",
        ai_raw_response: Optional[str] = None,
        actual_greeting_sent: str = "",
        is_greeted: bool = False,
        is_skipped: bool = False,
        skip_reason: str = "",
        account_name: str = "",
        timestamp: Optional[str] = None,
    ):
        self.timestamp = timestamp or datetime.now().isoformat()
        self.job_name = job_name
        self.job_url = job_url
        self.company = company
        self.salary = salary
        self.job_description = _truncate(job_description, MAX_JOB_DESCRIPTION_LEN)
        self.job_requirements = _truncate(job_requirements, MAX_JOB_REQUIREMENTS_LEN)
        self.ai_score = ai_score
        self.ai_is_match = ai_is_match
        self.ai_reason = _truncate(ai_reason, MAX_AI_REASON_LEN)
        self.ai_strengths = [
            _truncate(s, MAX_STRENGTHS_LEN // 3 + 50) for s in (ai_strengths or [])
        ]
        self.ai_weaknesses = [
            _truncate(w, MAX_WEAKNESSES_LEN // 3 + 50) for w in (ai_weaknesses or [])
        ]
        self.ai_suggested_greeting = _truncate(
            ai_suggested_greeting, MAX_AI_SUGGESTED_GREETING_LEN
        )
        self.system_prompt = _truncate(system_prompt, MAX_SYSTEM_PROMPT_LEN)
        self.user_prompt = _truncate(user_prompt, MAX_USER_PROMPT_LEN)
        self.ai_model = ai_model
        self.ai_raw_response = _truncate(ai_raw_response, MAX_AI_RAW_RESPONSE_LEN)
        self.actual_greeting_sent = _truncate(
            actual_greeting_sent, MAX_ACTUAL_GREETING_LEN
        )
        self.is_greeted = is_greeted
        self.is_skipped = is_skipped
        self.skip_reason = _truncate(skip_reason, MAX_SKIP_REASON_LEN)
        self.account_name = account_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "job_name": self.job_name,
            "job_url": self.job_url,
            "company": self.company,
            "salary": self.salary,
            "job_description": self.job_description,
            "job_requirements": self.job_requirements,
            "ai_score": self.ai_score,
            "ai_is_match": self.ai_is_match,
            "ai_reason": self.ai_reason,
            "ai_strengths": self.ai_strengths,
            "ai_weaknesses": self.ai_weaknesses,
            "ai_suggested_greeting": self.ai_suggested_greeting,
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "ai_model": self.ai_model,
            "ai_raw_response": self.ai_raw_response,
            "actual_greeting_sent": self.actual_greeting_sent,
            "is_greeted": self.is_greeted,
            "is_skipped": self.is_skipped,
            "skip_reason": self.skip_reason,
            "account_name": self.account_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GreetRecord":
        return cls(
            timestamp=data.get("timestamp"),
            job_name=data.get("job_name", ""),
            job_url=data.get("job_url", ""),
            company=data.get("company", ""),
            salary=data.get("salary", ""),
            job_description=data.get("job_description", ""),
            job_requirements=data.get("job_requirements", ""),
            ai_score=data.get("ai_score", 0),
            ai_is_match=data.get("ai_is_match", False),
            ai_reason=data.get("ai_reason", ""),
            ai_strengths=data.get("ai_strengths", []),
            ai_weaknesses=data.get("ai_weaknesses", []),
            ai_suggested_greeting=data.get("ai_suggested_greeting", ""),
            system_prompt=data.get("system_prompt"),
            user_prompt=data.get("user_prompt"),
            ai_model=data.get("ai_model", ""),
            ai_raw_response=data.get("ai_raw_response"),
            actual_greeting_sent=data.get("actual_greeting_sent", ""),
            is_greeted=data.get("is_greeted", False),
            is_skipped=data.get("is_skipped", False),
            skip_reason=data.get("skip_reason", ""),
            account_name=data.get("account_name", ""),
        )


# ─────────────────────────────────────────────
# ReplyRecordStore — 回复记录存储
# ─────────────────────────────────────────────

class ReplyRecordStore:
    """回复记录存储（JSON 文件持久化，线程安全）。

    支持按日期、账号、聊天对象筛选。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else REPLY_RECORDS_FILE
        self._lock = threading.Lock()
        self._records: List[ReplyRecord] = []
        self._load()

    def _load(self):
        """从文件加载记录。"""
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._records = [
                    ReplyRecord.from_dict(item) for item in data.get("records", [])
                ]
                logger.debug(
                    f"加载回复记录: {len(self._records)} 条"
                )
        except Exception as e:
            logger.error(f"加载回复记录失败: {e}")
            self._records = []

    def _save(self):
        """保存记录到文件（不加锁，由调用方负责加锁）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "records": [r.to_dict() for r in self._records[-MAX_REPLY_RECORDS:]],
                "total": len(self._records),
                "last_saved": datetime.now().isoformat(),
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存回复记录失败: {e}")

    def add(self, record: ReplyRecord):
        """添加一条回复记录。"""
        with self._lock:
            self._records.append(record)
            # 超过上限时截断旧记录
            if len(self._records) > MAX_REPLY_RECORDS * 2:
                self._records = self._records[-MAX_REPLY_RECORDS:]
            self._save()

    def get_all(self) -> List[ReplyRecord]:
        """获取所有回复记录。"""
        with self._lock:
            return list(self._records)

    def filter(
        self,
        date: Optional[str] = None,
        account_name: Optional[str] = None,
        chat_name: Optional[str] = None,
    ) -> List[ReplyRecord]:
        """按条件筛选回复记录。

        Args:
            date: 日期字符串，如 "2026-09-18"，匹配 timestamp 的日期部分
            account_name: 账号名称
            chat_name: 聊天对象名称

        Returns:
            符合条件的记录列表
        """
        with self._lock:
            results = []
            for r in self._records:
                if date and not r.timestamp.startswith(date):
                    continue
                if account_name and r.account_name != account_name:
                    continue
                if chat_name and r.chat_name != chat_name:
                    continue
                results.append(r)
            return results

    def count(self) -> int:
        """获取记录总数。"""
        with self._lock:
            return len(self._records)

    def clear(self):
        """清空所有记录。"""
        with self._lock:
            self._records = []
            self._save()


# ─────────────────────────────────────────────
# GreetRecordStore — 打招呼记录存储
# ─────────────────────────────────────────────

class GreetRecordStore:
    """打招呼记录存储（JSON 文件持久化，线程安全）。

    支持按日期、账号筛选。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else GREET_RECORDS_FILE
        self._lock = threading.Lock()
        self._records: List[GreetRecord] = []
        self._load()

    def _load(self):
        """从文件加载记录。"""
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._records = [
                    GreetRecord.from_dict(item) for item in data.get("records", [])
                ]
                logger.debug(
                    f"加载打招呼记录: {len(self._records)} 条"
                )
        except Exception as e:
            logger.error(f"加载打招呼记录失败: {e}")
            self._records = []

    def _save(self):
        """保存记录到文件（不加锁，由调用方负责加锁）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "records": [r.to_dict() for r in self._records[-MAX_GREET_RECORDS:]],
                "total": len(self._records),
                "last_saved": datetime.now().isoformat(),
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存打招呼记录失败: {e}")

    def add(self, record: GreetRecord):
        """添加一条打招呼记录。"""
        with self._lock:
            self._records.append(record)
            if len(self._records) > MAX_GREET_RECORDS * 2:
                self._records = self._records[-MAX_GREET_RECORDS:]
            self._save()

    def get_all(self) -> List[GreetRecord]:
        """获取所有打招呼记录。"""
        with self._lock:
            return list(self._records)

    def filter(
        self,
        date: Optional[str] = None,
        account_name: Optional[str] = None,
    ) -> List[GreetRecord]:
        """按条件筛选打招呼记录。

        Args:
            date: 日期字符串，如 "2026-09-18"
            account_name: 账号名称

        Returns:
            符合条件的记录列表
        """
        with self._lock:
            results = []
            for r in self._records:
                if date and not r.timestamp.startswith(date):
                    continue
                if account_name and r.account_name != account_name:
                    continue
                results.append(r)
            return results

    def count(self) -> int:
        """获取记录总数。"""
        with self._lock:
            return len(self._records)

    def clear(self):
        """清空所有记录。"""
        with self._lock:
            self._records = []
            self._save()


# ─────────────────────────────────────────────
# 导出功能
# ─────────────────────────────────────────────

def export_reply_records(
    format: str = "json",
    output_path: Optional[str] = None,
    date: Optional[str] = None,
    account_name: Optional[str] = None,
    chat_name: Optional[str] = None,
    store: Optional[ReplyRecordStore] = None,
) -> str:
    """导出回复记录。

    Args:
        format: 导出格式，"json" 或 "excel"
        output_path: 输出文件路径，默认自动生成
        date: 按日期筛选
        account_name: 按账号筛选
        chat_name: 按聊天对象筛选
        store: 自定义存储实例，默认使用全局实例

    Returns:
        导出文件路径
    """
    s = store or _get_reply_store()
    records = s.filter(date=date, account_name=account_name, chat_name=chat_name)

    if format == "json":
        if output_path is None:
            output_path = str(DATA_DIR / "reply_records_export.json")
        data = {
            "exported_at": datetime.now().isoformat(),
            "filters": {"date": date, "account_name": account_name, "chat_name": chat_name},
            "total": len(records),
            "records": [r.to_dict() for r in records],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return output_path

    elif format == "excel":
        return _export_reply_records_excel(records, output_path)

    else:
        raise ValueError(f"不支持的导出格式: {format}，请使用 'json' 或 'excel'")


def export_greet_records(
    format: str = "json",
    output_path: Optional[str] = None,
    date: Optional[str] = None,
    account_name: Optional[str] = None,
    store: Optional[GreetRecordStore] = None,
) -> str:
    """导出打招呼记录。

    Args:
        format: 导出格式，"json" 或 "excel"
        output_path: 输出文件路径，默认自动生成
        date: 按日期筛选
        account_name: 按账号筛选
        store: 自定义存储实例，默认使用全局实例

    Returns:
        导出文件路径
    """
    s = store or _get_greet_store()
    records = s.filter(date=date, account_name=account_name)

    if format == "json":
        if output_path is None:
            output_path = str(DATA_DIR / "greet_records_export.json")
        data = {
            "exported_at": datetime.now().isoformat(),
            "filters": {"date": date, "account_name": account_name},
            "total": len(records),
            "records": [r.to_dict() for r in records],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return output_path

    elif format == "excel":
        return _export_greet_records_excel(records, output_path)

    else:
        raise ValueError(f"不支持的导出格式: {format}，请使用 'json' 或 'excel'")


def _export_reply_records_excel(
    records: List[ReplyRecord], output_path: Optional[str] = None
) -> str:
    """将回复记录导出为 Excel 文件。"""
    try:
        from openpyxl import Workbook
    except ImportError:
        raise ImportError(
            "导出 Excel 需要安装 openpyxl 库，请运行: pip install openpyxl"
        )

    if output_path is None:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(DATA_DIR / f"reply_records_{timestamp_str}.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "回复记录"

    # 表头
    headers = [
        "时间", "聊天对象", "岗位名称", "收到消息", "回复内容",
        "回复来源", "意图", "回复理由", "AI模型", "AI原始返回",
        "是否跳过", "跳过原因", "账号名称", "账号索引",
    ]
    ws.append(headers)

    # 数据行
    for r in records:
        ws.append([
            r.timestamp, r.chat_name, r.job_name,
            r.received_message or "", r.reply_content or "",
            r.reply_source, r.reply_intent, r.reply_reason or "",
            r.ai_model, r.ai_raw_response or "",
            "是" if r.is_skipped else "否",
            r.skip_reason or "",
            r.account_name, r.account_index,
        ])

    wb.save(output_path)
    return output_path


def _export_greet_records_excel(
    records: List[GreetRecord], output_path: Optional[str] = None
) -> str:
    """将打招呼记录导出为 Excel 文件。"""
    try:
        from openpyxl import Workbook
    except ImportError:
        raise ImportError(
            "导出 Excel 需要安装 openpyxl 库，请运行: pip install openpyxl"
        )

    if output_path is None:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(DATA_DIR / f"greet_records_{timestamp_str}.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "打招呼记录"

    # 表头
    headers = [
        "时间", "岗位名称", "岗位链接", "公司", "薪资",
        "岗位描述", "任职要求", "AI评分", "是否匹配",
        "AI理由", "优势", "劣势", "AI建议打招呼",
        "AI模型", "AI原始返回", "实际打招呼", "是否已打招呼",
        "是否跳过", "跳过原因", "账号名称",
    ]
    ws.append(headers)

    # 数据行
    for r in records:
        ws.append([
            r.timestamp, r.job_name, r.job_url, r.company, r.salary,
            r.job_description or "", r.job_requirements or "",
            r.ai_score, "是" if r.ai_is_match else "否",
            r.ai_reason or "",
            "; ".join(r.ai_strengths) if r.ai_strengths else "",
            "; ".join(r.ai_weaknesses) if r.ai_weaknesses else "",
            r.ai_suggested_greeting or "",
            r.ai_model, r.ai_raw_response or "",
            r.actual_greeting_sent or "",
            "是" if r.is_greeted else "否",
            "是" if r.is_skipped else "否",
            r.skip_reason or "",
            r.account_name,
        ])

    wb.save(output_path)
    return output_path


# ─────────────────────────────────────────────
# 全局单例（延迟初始化）
# ─────────────────────────────────────────────

_reply_store: Optional[ReplyRecordStore] = None
_greet_store: Optional[GreetRecordStore] = None
_store_lock = threading.Lock()


def _get_reply_store() -> ReplyRecordStore:
    """获取全局回复记录存储单例。"""
    global _reply_store
    if _reply_store is None:
        with _store_lock:
            if _reply_store is None:
                _reply_store = ReplyRecordStore()
    return _reply_store


def _get_greet_store() -> GreetRecordStore:
    """获取全局打招呼记录存储单例。"""
    global _greet_store
    if _greet_store is None:
        with _store_lock:
            if _greet_store is None:
                _greet_store = GreetRecordStore()
    return _greet_store


def record_reply(
    chat_name: str = "",
    job_name: str = "",
    received_message: str = "",
    reply_content: Optional[str] = None,
    reply_source: str = "",
    reply_intent: str = "",
    reply_reason: str = "",
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    ai_model: str = "",
    ai_raw_response: Optional[str] = None,
    is_skipped: bool = False,
    skip_reason: str = "",
    account_name: str = "",
    account_index: int = 0,
) -> ReplyRecord:
    """便捷函数：创建并保存一条回复记录。

    Returns:
        创建的 ReplyRecord 对象
    """
    record = ReplyRecord(
        chat_name=chat_name,
        job_name=job_name,
        received_message=received_message,
        reply_content=reply_content,
        reply_source=reply_source,
        reply_intent=reply_intent,
        reply_reason=reply_reason,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        ai_model=ai_model,
        ai_raw_response=ai_raw_response,
        is_skipped=is_skipped,
        skip_reason=skip_reason,
        account_name=account_name,
        account_index=account_index,
    )
    _get_reply_store().add(record)
    return record


def record_greet(
    job_name: str = "",
    job_url: str = "",
    company: str = "",
    salary: str = "",
    job_description: str = "",
    job_requirements: str = "",
    ai_score: int = 0,
    ai_is_match: bool = False,
    ai_reason: str = "",
    ai_strengths: Optional[List[str]] = None,
    ai_weaknesses: Optional[List[str]] = None,
    ai_suggested_greeting: str = "",
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    ai_model: str = "",
    ai_raw_response: Optional[str] = None,
    actual_greeting_sent: str = "",
    is_greeted: bool = False,
    is_skipped: bool = False,
    skip_reason: str = "",
    account_name: str = "",
) -> GreetRecord:
    """便捷函数：创建并保存一条打招呼记录。

    Returns:
        创建的 GreetRecord 对象
    """
    record = GreetRecord(
        job_name=job_name,
        job_url=job_url,
        company=company,
        salary=salary,
        job_description=job_description,
        job_requirements=job_requirements,
        ai_score=ai_score,
        ai_is_match=ai_is_match,
        ai_reason=ai_reason,
        ai_strengths=ai_strengths,
        ai_weaknesses=ai_weaknesses,
        ai_suggested_greeting=ai_suggested_greeting,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        ai_model=ai_model,
        ai_raw_response=ai_raw_response,
        actual_greeting_sent=actual_greeting_sent,
        is_greeted=is_greeted,
        is_skipped=is_skipped,
        skip_reason=skip_reason,
        account_name=account_name,
    )
    _get_greet_store().add(record)
    return record