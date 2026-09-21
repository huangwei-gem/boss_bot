"""
BOSS 自动回复机器人 - AI 自进化引擎

消息回复准确度自检与自动优化功能：
1. 消息来源识别：区分 AI 回复 vs 用户（HR）回复
2. 回复效果评估：分析 HR 对 AI 回复的反应（积极/中性/消极/忽略）
3. 策略自动优化：根据反馈调整回复模板和规则匹配优先级
4. 进化记录：持久化进化历史，跟踪优化效果

设计原则：
- 渐进式优化：每次只做小幅调整，不一次性大幅修改策略
- 数据安全：进化数据持久化到 JSON 文件，重启后不丢失
- 可关闭：通过配置开关启用/禁用自进化功能
- 不破坏现有功能：自进化是增量功能，不影响现有回复逻辑
"""

import json
import re
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

logger = logging.getLogger(__name__)

# 进化数据文件路径
_EVOLUTION_DATA_FILE = Path(__file__).parent.parent / "data" / "evolution_data.json"

# 自进化质量数据文件路径（评分、规则调整、模板优化记录）
_QUALITY_DATA_FILE = Path(__file__).parent.parent / "data" / "self_evolve_data.json"

# 回复效果分类
EFFECT_POSITIVE = "positive"
EFFECT_NEUTRAL = "neutral"
EFFECT_NEGATIVE = "negative"
EFFECT_IGNORED = "ignored"

# ─────────────────────────────────────────────
# 评分维度配置（总分 100）
# ─────────────────────────────────────────────
SCORE_RELEVANCE_MAX = 30       # 相关性：回复内容是否与收到的消息相关
SCORE_COMPLETENESS_MAX = 30    # 完整性：回复是否完整回答了对方的问题
SCORE_NATURALNESS_MAX = 20     # 自然度：回复是否像真人说话，不像机器
SCORE_EFFECTIVENESS_MAX = 20   # 有效性：回复是否推动了对话进展
SCORE_TOTAL_MAX = 100
LOW_SCORE_THRESHOLD = 60       # 低于此分数视为低分回复

# 评分批量大小（每次调用 AI 评分的样本数，避免 prompt 过长）
AI_SCORING_BATCH_SIZE = 10

# 模板使用次数阈值：低于此值不参与优化建议
TEMPLATE_MIN_USAGE_FOR_OPT = 3

# 积极反应关键词（HR 继续对话、询问详情、约面试等）
_POSITIVE_PATTERNS = [
    re.compile(r"(面试|聊聊|沟通|电话|视频|线下|来公司)", re.IGNORECASE),
    re.compile(r"(可以|好的|没问题|方便|同意|接受)", re.IGNORECASE),
    re.compile(r"(请问|想了解|能否|可否|具体|详细)", re.IGNORECASE),
    re.compile(r"(什么时候|哪天|几点|安排)", re.IGNORECASE),
    re.compile(r"(期待|欢迎|不错|感兴趣|合适)", re.IGNORECASE),
    re.compile(r"(offer|入职|录用|录取|报到)", re.IGNORECASE),
]

# 消极反应关键词（HR 拒绝、表示不合适等）
_NEGATIVE_PATTERNS = [
    re.compile(r"(不合适|不符合|不匹配|不考虑|抱歉|遗憾)", re.IGNORECASE),
    re.compile(r"(不需要|不用了|算了|放弃|拒绝)", re.IGNORECASE),
    re.compile(r"(已招满|已关闭|已结束|暂停招聘)", re.IGNORECASE),
    re.compile(r"(太远|薪资太低|经验不足|学历不够)", re.IGNORECASE),
    re.compile(r"(再看看|再考虑|有消息通知|有合适再联系)", re.IGNORECASE),
]

# AI 回复特征关键词（匹配已配置的回复模板风格）
_AI_REPLY_MARKERS = [
    "期望薪资",
    "可以面谈",
    "更看重发展",
    "方便安排面试",
    "哪个时间段方便",
    "我了解这个岗位",
    "相信能快速上手",
    "我对这个岗位很感兴趣",
    "方便了解一下",
    "感谢您的消息",
    "稍后完成",
    "马上处理",
    "简历刚刚已经发",
    "稍后我补发",
]


class SelfEvolveEngine:
    """AI 自进化引擎 — 消息回复准确度自检与自动优化

    功能：
    1. 消息来源识别：区分 AI 回复 vs 用户回复
    2. 回复效果评估：分析 HR 对 AI 回复的反应
    3. 策略自动优化：根据反馈调整回复模板和规则
    4. 进化记录：持久化进化历史，跟踪优化效果

    Attributes:
        enabled: 是否启用自进化功能
        config: 配置字典
        log_cb: 日志回调函数
    """

    # 自动优化的触发阈值（每 N 轮回复后触发）
    AUTO_OPTIMIZE_THRESHOLD = 10

    # 无响应判定时间（小时）
    IGNORED_THRESHOLD_HOURS = 24

    def __init__(self, config: Optional[dict] = None, log_callback: Optional[Callable] = None,
                 data_file: Optional[str] = None):
        """初始化自进化引擎。

        Args:
            config: 配置字典，包含 enabled 等字段
            log_callback: 日志回调函数，用于将日志传递到上层
            data_file: 进化数据文件路径，默认使用 data/evolution_data.json
        """
        self.enabled = (config or {}).get("enabled", True)
        self.log_cb = log_callback
        self._data_file = Path(data_file) if data_file else _EVOLUTION_DATA_FILE
        # 质量评分数据文件（评分历史、规则调整、模板优化记录）
        self._quality_data_file = _QUALITY_DATA_FILE
        self._lock = threading.Lock()

        # 进化数据结构
        self._reply_records: List[Dict[str, Any]] = []  # AI 回复记录
        self._reply_stats: Dict[str, int] = {
            "total_replies": 0,
            "positive": 0,
            "neutral": 0,
            "negative": 0,
            "ignored": 0,
        }
        self._template_effectiveness: Dict[str, Dict[str, int]] = {}
        self._strategy_adjustments: List[Dict[str, Any]] = []
        self._reply_count_since_optimize = 0

        # ── 自进化质量评分数据结构 ──
        # 回复评分历史：[{id, received_message, reply_content, reply_source,
        #               score, relevance, completeness, naturalness, effectiveness,
        #               timestamp, scoring_method}]
        self._reply_evaluations: List[Dict[str, Any]] = []
        # 规则调整记录：[{rule_key, old_value, new_value, reason, auto_applied, timestamp}]
        self._rule_adjustments: List[Dict[str, Any]] = []
        # 模板优化记录：[{template_key, current_template, suggested_template,
        #                 usage_count, avg_score, reason, timestamp, applied}]
        self._template_optimizations: List[Dict[str, Any]] = []
        # 模板使用统计：{template_key: {usage_count, total_score, avg_score, last_used}}
        self._template_usage_stats: Dict[str, Dict[str, Any]] = {}

        # 加载持久化数据
        self.load_evolution_data()
        self._load_quality_data()

    def _log(self, level: str, msg: str):
        """统一日志输出。"""
        if self.log_cb:
            try:
                self.log_cb(f"[{level}] [自进化] {msg}")
            except Exception:
                pass
        getattr(logger, level.lower(), logger.info)(f"[自进化] {msg}")

    # ─────────────────────────────────────────────
    # 消息来源识别
    # ─────────────────────────────────────────────

    # reply_records.json 缓存（避免每次 classify 都重新读文件）
    # 结构: {"records": [...], "loaded_at": float(timestamp)}
    _reply_records_cache: Dict[str, Any] = {"records": [], "loaded_at": 0.0}
    # 缓存有效期（秒）：5 分钟内不重复读文件
    _REPLY_RECORDS_CACHE_TTL = 300

    def _load_reply_records_for_classify(self) -> List[Dict[str, Any]]:
        """加载 reply_records.json 用于消息来源匹配。

        优先使用 ReplyRecordStore（线程安全、已加载），失败时直接读 JSON 文件。
        结果缓存 5 分钟，避免每次 classify 都重新读文件。

        Returns:
            回复记录列表，每条至少包含 reply_content 和 timestamp 字段
        """
        now = time.time()
        cache = SelfEvolveEngine._reply_records_cache
        if cache["records"] and (now - cache["loaded_at"]) < self._REPLY_RECORDS_CACHE_TTL:
            return cache["records"]

        records: List[Dict[str, Any]] = []

        # 优先用 ReplyRecordStore（与 reply_record.py 同源，含已发送记录）
        try:
            from boss_bot.reply_record import _get_reply_store
            store = _get_reply_store()
            raw = store.get_all()
            for r in raw:
                records.append({
                    "reply_content": r.reply_content or "",
                    "timestamp": r.timestamp or "",
                    "chat_name": r.chat_name or "",
                })
        except Exception as e:
            self._log("DEBUG", f"ReplyRecordStore 加载失败，回退到直接读 JSON: {e}")
            records = []
            # 兜底：直接读 data/reply_records.json
            try:
                reply_file = Path(__file__).parent.parent / "data" / "reply_records.json"
                if reply_file.exists():
                    with open(reply_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for item in data.get("records", []):
                        records.append({
                            "reply_content": item.get("reply_content", "") or "",
                            "timestamp": item.get("timestamp", "") or "",
                            "chat_name": item.get("chat_name", "") or "",
                        })
            except Exception as e2:
                self._log("DEBUG", f"直接读 reply_records.json 失败: {e2}")

        # 更新缓存
        SelfEvolveEngine._reply_records_cache = {
            "records": records,
            "loaded_at": now,
        }
        return records

    @staticmethod
    def _parse_msg_time(time_str: str) -> Optional[datetime]:
        """将消息时间字符串解析为 datetime。

        支持格式：
        - "19:37" → 当天 19:37
        - "昨天 21:35" → 昨天 21:35
        - "2026-09-18 19:37" → 该日期 19:37
        - ISO 格式 "2026-09-18T20:38:42.690993" → 直接解析

        解析失败返回 None。
        """
        if not time_str:
            return None
        s = time_str.strip()
        try:
            # ISO 格式
            if "T" in s:
                return datetime.fromisoformat(s)
            # "昨天 HH:MM"
            if s.startswith("昨天"):
                m = re.match(r"昨天\s*(\d{1,2}):(\d{2})", s)
                if m:
                    return datetime.now().replace(
                        hour=int(m.group(1)), minute=int(m.group(2)),
                        second=0, microsecond=0
                    ) - timedelta(days=1)
                return None
            # "YYYY-MM-DD HH:MM"
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})", s)
            if m:
                return datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    int(m.group(4)), int(m.group(5))
                )
            # "HH:MM" → 当天
            m = re.match(r"(\d{1,2}):(\d{2})", s)
            if m:
                return datetime.now().replace(
                    hour=int(m.group(1)), minute=int(m.group(2)),
                    second=0, microsecond=0
                )
        except (ValueError, TypeError):
            return None
        return None

    def classify_message_source(self, message_text: str,
                                 conversation_history: Optional[List[dict]] = None,
                                 timestamp: Optional[str] = None) -> str:
        """识别消息来源：AI 回复 vs 人工手动回复 vs HR 回复。

        优先级：
        1. reply_records.json 精确匹配 content + timestamp（±5 分钟误差）
        2. reply_records.json 模糊匹配 content（reply_content 字段）
        3. 对话历史 is_mine 标记（己方消息 → ai 或 human；对方消息 → hr）
        4. 文本模式特征匹配（fallback：AI 模板特征词）

        BOSS 直聘前端不区分 AI 自动发送 vs 人工手动发送，
        需用 reply_records.json 按 content + timestamp 匹配判断：
        - 若消息内容出现在 reply_records.json 的 reply_content 字段中，
          则该消息是 AI 发送的（reply_source 字段记录了具体来源）。
        - 若 content 匹配多条记录，用 timestamp 进一步筛选（±5 分钟误差）。

        Args:
            message_text: 消息文本内容
            conversation_history: 对话历史，包含 is_mine 标记
                [{"text": str, "is_mine": bool, "time": str, ...}]
            timestamp: 消息时间戳（可选，格式如 "19:37" / "昨天 21:35" /
                "2026-09-18T20:38:42"，用于与 reply_records.json 精确匹配）

        Returns:
            "ai" — AI 自动回复（reply_records.json 命中）
            "human" — 人工手动回复（己方消息但 reply_records.json 未命中）
            "hr" — HR/对方发送的消息
        """
        if not message_text:
            return "hr"

        text = message_text.strip()

        # ── 优先级 1 & 2：reply_records.json 匹配 ──
        # 若消息内容出现在 reply_records.json 的 reply_content 字段中，
        # 则该消息是 AI 发送的。
        try:
            records = self._load_reply_records_for_classify()
        except Exception as e:
            self._log("DEBUG", f"加载 reply_records 失败，跳过精确匹配: {e}")
            records = []

        if records:
            # 收集所有 content 匹配的记录
            matched_records = [
                r for r in records
                if r.get("reply_content") and text == r["reply_content"].strip()
            ]

            if matched_records and timestamp:
                # 优先级 1：content + timestamp 精确匹配（±5 分钟误差）
                msg_time = self._parse_msg_time(timestamp)
                if msg_time is not None:
                    tolerance = timedelta(minutes=5)
                    for r in matched_records:
                        rec_time = self._parse_msg_time(r.get("timestamp", ""))
                        if rec_time is not None and abs(msg_time - rec_time) <= tolerance:
                            return "ai"
                    # timestamp 未匹配上任何记录，但仍是 content 精确命中
                    # → 仍判定为 AI（content 唯一性已足够强）

            if matched_records:
                # 优先级 2：content 精确匹配（无 timestamp 或 timestamp 解析失败）
                return "ai"

            # 模糊匹配：消息文本包含某条 reply_content（或反之）
            # 仅当文本较长时启用，避免短文本误匹配
            if len(text) >= 10:
                for r in records:
                    rc = r.get("reply_content", "").strip()
                    if not rc or len(rc) < 10:
                        continue
                    # 完全包含关系（双向）
                    if text in rc or rc in text:
                        return "ai"

        # ── 优先级 3：对话历史 is_mine 标记 ──
        # 己方消息 → 需进一步区分 ai/human（已在上面 reply_records 匹配过）
        # 对方消息 → hr
        if conversation_history:
            for msg in reversed(conversation_history):
                if msg.get("text", "").strip() == text:
                    if msg.get("is_mine"):
                        # 己方消息但 reply_records 未命中 → 人工手动回复
                        return "human"
                    else:
                        return "hr"

        # ── 优先级 4：文本模式特征匹配（fallback） ──
        # 检查是否匹配 AI 回复模板特征
        ai_marker_count = sum(1 for marker in _AI_REPLY_MARKERS if marker in text)

        # AI 回复通常包含多个模板特征词，且较长、较正式
        if ai_marker_count >= 2:
            return "ai"

        # 检查是否匹配已知回复模板（从配置中加载的模板内容）
        try:
            from boss_bot.config import (
                SALARY_REPLY, INTERVIEW_TIME_REPLY, JOB_CONTENT_REPLY,
                GREETING_REPLY, DEFAULT_REPLY, RESUME_DUPLICATE_REPLY,
                RESUME_UNAVAILABLE_REPLY,
            )
            known_templates = [
                SALARY_REPLY, INTERVIEW_TIME_REPLY, JOB_CONTENT_REPLY,
                GREETING_REPLY, DEFAULT_REPLY, RESUME_DUPLICATE_REPLY,
                RESUME_UNAVAILABLE_REPLY,
            ]
            for template in known_templates:
                if template and text in template:
                    return "ai"
        except Exception:
            pass

        # 无法确定时默认返回 "human"（保守判断：己方消息但无证据是 AI）
        # 调用方可结合 conversation_history 的 is_mine 进一步判断
        return "human"

    # ─────────────────────────────────────────────
    # 对话结果检测
    # ─────────────────────────────────────────────

    # HR 明确拒绝关键词（detect_conversation_outcome 用）
    _REJECT_PATTERNS = [
        re.compile(r"(不合适|不符合|不匹配|不考虑|抱歉|遗憾)", re.IGNORECASE),
        re.compile(r"(不需要|不用了|算了|放弃|拒绝)", re.IGNORECASE),
        re.compile(r"(已招满|已关闭|已结束|暂停招聘|停止招聘)", re.IGNORECASE),
        re.compile(r"(太远|薪资太低|经验不足|学历不够|年龄不符)", re.IGNORECASE),
        re.compile(r"(再看看|再考虑|有合适再联系|有消息通知)", re.IGNORECASE),
        re.compile(r"(祝您|祝你).*(找到|求职|发展).*(顺利|成功|如意)", re.IGNORECASE),
    ]

    # 约面试关键词
    _INTERVIEW_PATTERNS = [
        re.compile(r"(面试|面谈|聊聊|沟通一下|详谈)", re.IGNORECASE),
        re.compile(r"(来公司|到公司|线下|当面|碰面)", re.IGNORECASE),
        re.compile(r"(电话|视频|腾讯会议|钉钉|微信聊)", re.IGNORECASE),
        re.compile(r"(什么时候|哪天|几点|方便.*时间|安排.*时间)", re.IGNORECASE),
        re.compile(r"(offer|入职|录用|录取|报到|入职培训)", re.IGNORECASE),
        re.compile(r"(发个.*offer|发.*offer|发录用|发入职)", re.IGNORECASE),
    ]

    # 对话中断时间阈值（小时）：双方最后一条消息超过此时间未回复视为 abandoned
    _ABANDONED_THRESHOLD_HOURS = 72

    def detect_conversation_outcome(self, messages: List[dict]) -> str:
        """分析对话的最终结果。

        基于 messages 列表（含 sender/status 字段）判断对话结局：
        - "rejected": HR 明确拒绝（含拒绝关键词）
        - "interview": 约面试（含面试/见面/来公司等关键词）
        - "read_no_reply": HR 已读不回（我方最后消息 status=read，且无后续 HR 消息）
        - "ongoing": 继续沟通（最后消息是 HR 发的，且无拒绝意图）
        - "abandoned": 对话中断（双方都长时间未回复，超过 72 小时）

        判定优先级：
        1. 空消息列表 → "abandoned"
        2. 提取最后一条非系统消息，判断发送者
        3. 若最后消息是 HR 发的：
           - 含拒绝关键词 → "rejected"
           - 含面试关键词 → "interview"
           - 否则 → "ongoing"
        4. 若最后消息是我方发的：
           - 检查我方最后消息的 status
           - status="read" 且之后无 HR 消息 → "read_no_reply"
           - 距最后消息超过 72 小时 → "abandoned"
           - 否则 → "ongoing"（仍在等待 HR 回复）
        5. 检查整段对话是否含拒绝/面试关键词（兜底）

        Args:
            messages: 消息列表，每条至少包含：
                - sender: "hr" / "me" / "system"
                - text: 消息文本
                - status: "delivered" / "read" / None（仅我方消息）
                - time: 时间戳（可选，用于判断 abandoned）

        Returns:
            "rejected" / "interview" / "read_no_reply" / "ongoing" / "abandoned"
        """
        if not messages:
            return "abandoned"

        # 过滤掉系统消息，只保留 HR 和我方的对话消息
        conversation_msgs = [
            m for m in messages
            if m.get("sender") in ("hr", "me")
        ]
        if not conversation_msgs:
            return "abandoned"

        # 取最后一条对话消息
        last_msg = conversation_msgs[-1]
        last_sender = last_msg.get("sender", "")
        last_text = (last_msg.get("text") or "").strip()

        # ── 若最后消息是 HR 发的 ──
        if last_sender == "hr":
            # 检查拒绝关键词
            for pattern in self._REJECT_PATTERNS:
                if pattern.search(last_text):
                    return "rejected"
            # 检查面试关键词
            for pattern in self._INTERVIEW_PATTERNS:
                if pattern.search(last_text):
                    return "interview"
            # HR 最后发言且无拒绝意图 → 继续沟通
            return "ongoing"

        # ── 若最后消息是我方发的 ──
        if last_sender == "me":
            # 检查我方最后消息的已读状态
            last_status = last_msg.get("status")

            # 找到最后的 HR 消息（如果有）
            last_hr_msg = None
            for m in reversed(conversation_msgs):
                if m.get("sender") == "hr":
                    last_hr_msg = m
                    break

            # 我方最后消息已读，且之后无 HR 回复 → 已读不回
            if last_status == "read" and last_hr_msg is None:
                return "read_no_reply"
            # 我方最后消息已读，且 HR 在我方最后消息之前发言
            # （即 HR 已读但未回复）→ 已读不回
            if last_status == "read" and last_hr_msg is not None:
                # 判断 last_hr_msg 是否在 last_msg 之前
                last_hr_idx = conversation_msgs.index(last_hr_msg)
                last_msg_idx = len(conversation_msgs) - 1
                if last_hr_idx < last_msg_idx:
                    return "read_no_reply"

            # 检查是否对话中断（距最后消息超过 72 小时）
            last_time_str = last_msg.get("time") or last_msg.get("timestamp")
            if last_time_str:
                last_time = self._parse_msg_time(last_time_str)
                if last_time is not None:
                    hours_elapsed = (datetime.now() - last_time).total_seconds() / 3600
                    if hours_elapsed >= self._ABANDONED_THRESHOLD_HOURS:
                        return "abandoned"

            # 仍在等待 HR 回复 → 继续沟通
            return "ongoing"

        # ── 兜底：检查整段对话是否含拒绝/面试关键词 ──
        all_text = " ".join(
            (m.get("text") or "").strip()
            for m in conversation_msgs
            if m.get("sender") == "hr"
        )
        for pattern in self._REJECT_PATTERNS:
            if pattern.search(all_text):
                return "rejected"
        for pattern in self._INTERVIEW_PATTERNS:
            if pattern.search(all_text):
                return "interview"

        return "ongoing"

    # ─────────────────────────────────────────────
    # 回复效果评估
    # ─────────────────────────────────────────────

    def evaluate_reply_effectiveness(self, ai_reply: str,
                                      hr_response: Optional[str] = None,
                                      response_time_hours: Optional[float] = None) -> str:
        """评估 AI 回复的效果。

        评估维度：
        - positive: HR 回复积极（继续对话、询问详情、约面试）
        - neutral: HR 回复中性（简单确认、未展开）
        - negative: HR 回复消极（拒绝、已读不回、结束对话）
        - ignored: HR 未回复（超过24小时无响应）

        Args:
            ai_reply: AI 发送的回复内容
            hr_response: HR 的后续回复内容（None 表示未回复）
            response_time_hours: HR 回复的时间间隔（小时）

        Returns:
            效果分类：positive/neutral/negative/ignored
        """
        # HR 未回复
        if hr_response is None or not hr_response.strip():
            if response_time_hours is not None and response_time_hours >= self.IGNORED_THRESHOLD_HOURS:
                return EFFECT_IGNORED
            # 还在等待回复窗口内，暂不判定
            return EFFECT_NEUTRAL

        text = hr_response.strip()

        # 检查消极反应
        for pattern in _NEGATIVE_PATTERNS:
            if pattern.search(text):
                return EFFECT_NEGATIVE

        # 检查积极反应
        for pattern in _POSITIVE_PATTERNS:
            if pattern.search(text):
                return EFFECT_POSITIVE

        # 默认中性
        return EFFECT_NEUTRAL

    # ─────────────────────────────────────────────
    # AI 回复记录与效果追踪
    # ─────────────────────────────────────────────

    def record_ai_reply(self, reply_text: str, context: dict):
        """记录一次 AI 回复，用于后续效果评估。

        Args:
            reply_text: AI 发送的回复内容
            context: 回复上下文，包含 chat_name、job_name、source、intent 等
        """
        if not self.enabled:
            return

        with self._lock:
            record = {
                "id": len(self._reply_records) + 1,
                "reply_text": reply_text[:200],  # 截断防止过长
                "chat_name": context.get("chat_name", ""),
                "job_name": context.get("job_name", ""),
                "source": context.get("source", ""),  # rule/intent/ai/default
                "intent": context.get("intent", ""),
                "timestamp": datetime.now().isoformat(),
                "effect": None,  # 待评估
                "hr_response": None,
                "evaluated": False,
            }
            self._reply_records.append(record)
            self._reply_stats["total_replies"] += 1

            # 按模板/来源统计效果
            source_key = context.get("source", "unknown")
            if source_key not in self._template_effectiveness:
                self._template_effectiveness[source_key] = {
                    "used": 0, "positive": 0, "neutral": 0, "negative": 0, "ignored": 0,
                }
            self._template_effectiveness[source_key]["used"] += 1

            self._reply_count_since_optimize += 1
            self._save_internal()

        self._log("DEBUG", f"记录 AI 回复 #{record['id']} (source={source_key})")

    def evaluate_previous_replies(self, new_messages: List[dict], chat_name: str = ""):
        """收到新消息时，评估之前 AI 回复的效果。

        Args:
            new_messages: 新收到的消息列表 [{"text", "is_mine", "time"}]
            chat_name: 当前聊天会话名称
        """
        if not self.enabled:
            return

        with self._lock:
            # 找到该会话中尚未评估的 AI 回复
            pending_records = [
                r for r in self._reply_records
                if not r["evaluated"] and r["chat_name"] == chat_name
            ]

            if not pending_records:
                return

            # 提取 HR 的新消息（非己方消息）
            hr_messages = [
                m for m in new_messages
                if not m.get("is_mine") and m.get("text", "").strip()
            ]

            if not hr_messages:
                return

            # 用最新的 HR 消息评估最近的 AI 回复
            latest_hr_msg = hr_messages[-1].get("text", "")
            for record in pending_records:
                # 计算回复时间间隔
                reply_time = datetime.fromisoformat(record["timestamp"])
                now = datetime.now()
                hours_elapsed = (now - reply_time).total_seconds() / 3600

                effect = self.evaluate_reply_effectiveness(
                    record["reply_text"], latest_hr_msg, hours_elapsed
                )

                record["effect"] = effect
                record["hr_response"] = latest_hr_msg[:200]
                record["evaluated"] = True

                # 更新统计
                if effect in self._reply_stats:
                    self._reply_stats[effect] += 1

                # 更新模板效果统计
                source_key = record["source"] or "unknown"
                if source_key in self._template_effectiveness:
                    if effect in self._template_effectiveness[source_key]:
                        self._template_effectiveness[source_key][effect] += 1

                self._log("INFO",
                          f"回复 #{record['id']} 效果评估: {effect} "
                          f"(source={source_key}, HR回复={latest_hr_msg[:30]})")

            self._save_internal()

        # 检查是否需要自动优化
        if self._reply_count_since_optimize >= self.AUTO_OPTIMIZE_THRESHOLD:
            self._log("INFO", f"已达到自动优化阈值({self.AUTO_OPTIMIZE_THRESHOLD}轮)，触发优化")
            self.auto_optimize_strategy()
            self._reply_count_since_optimize = 0

    # ─────────────────────────────────────────────
    # 优化机会检测
    # ─────────────────────────────────────────────

    def detect_optimization_opportunity(self) -> List[Dict[str, Any]]:
        """检测优化机会。

        分析对话历史，识别：
        - 哪些回复模板效果不好（被拒绝率高）
        - 哪些关键词触发了更好的回复
        - 哪些回复时机更好

        Returns:
            优化机会列表，每项包含 type、detail、suggestion
        """
        opportunities = []

        with self._lock:
            # 分析各来源/模板的效果
            for source_key, stats in self._template_effectiveness.items():
                used = stats.get("used", 0)
                if used < 3:
                    continue  # 数据量不足，跳过

                positive_rate = stats.get("positive", 0) / used
                negative_rate = stats.get("negative", 0) / used
                ignored_rate = stats.get("ignored", 0) / used

                # 效果差的模板：消极率 > 30%
                if negative_rate > 0.3:
                    opportunities.append({
                        "type": "low_effectiveness_template",
                        "source": source_key,
                        "detail": f"消极率 {negative_rate:.0%}（{used}次使用）",
                        "suggestion": "降低使用频率或优化回复内容",
                        "severity": "high",
                    })

                # 被忽略率高
                if ignored_rate > 0.3:
                    opportunities.append({
                        "type": "high_ignore_rate",
                        "source": source_key,
                        "detail": f"忽略率 {ignored_rate:.0%}（{used}次使用）",
                        "suggestion": "优化回复时机或调整回复内容吸引力",
                        "severity": "medium",
                    })

                # 效果好的模板
                if positive_rate > 0.5:
                    opportunities.append({
                        "type": "high_effectiveness_template",
                        "source": source_key,
                        "detail": f"积极率 {positive_rate:.0%}（{used}次使用）",
                        "suggestion": "提高使用优先级",
                        "severity": "info",
                    })

        return opportunities

    # ─────────────────────────────────────────────
    # 策略自动优化
    # ─────────────────────────────────────────────

    def auto_optimize_strategy(self) -> Dict[str, Any]:
        """自动优化回复策略。

        根据统计数据自动调整：
        - 优化回复模板（替换效果差的模板）
        - 调整规则匹配优先级
        - 优化 AI 提示词
        - 调整回复时机和频率

        Returns:
            优化结果摘要
        """
        if not self.enabled:
            return {"status": "disabled", "message": "自进化功能未启用"}

        opportunities = self.detect_optimization_opportunity()
        adjustments = []

        for opp in opportunities:
            if opp["severity"] == "high":
                # 高严重度：降低效果差的模板使用频率
                adjustment = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "action": f"降低 {opp['source']} 使用频率",
                    "reason": opp["detail"],
                    "type": "reduce_frequency",
                    "source": opp["source"],
                }
                adjustments.append(adjustment)
                self._log("INFO", f"策略调整: {adjustment['action']} — {adjustment['reason']}")

            elif opp["severity"] == "info":
                # 信息级：提高效果好的模板优先级
                adjustment = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "action": f"提高 {opp['source']} 使用优先级",
                    "reason": opp["detail"],
                    "type": "increase_priority",
                    "source": opp["source"],
                }
                adjustments.append(adjustment)
                self._log("INFO", f"策略调整: {adjustment['action']} — {adjustment['reason']}")

        with self._lock:
            self._strategy_adjustments.extend(adjustments)
            self._save_internal()

        result = {
            "status": "ok",
            "opportunities_found": len(opportunities),
            "adjustments_made": len(adjustments),
            "adjustments": adjustments,
        }

        self._log("INFO",
                  f"自动优化完成: 发现 {len(opportunities)} 个机会，"
                  f"执行 {len(adjustments)} 项调整")

        return result

    # ─────────────────────────────────────────────
    # 进化报告
    # ─────────────────────────────────────────────

    def get_evolution_report(self) -> Dict[str, Any]:
        """获取进化报告。

        Returns:
            包含以下字段的报告字典：
            - reply_stats: 总回复数、各效果分类占比
            - template_effectiveness: 各模板/来源的效果统计
            - best_templates: 最有效的回复模板
            - worst_templates: 最需要优化的回复模板
            - strategy_adjustments: 策略调整历史
            - recent_records: 最近的回复记录
        """
        with self._lock:
            total = self._reply_stats["total_replies"]
            stats = dict(self._reply_stats)

            # 计算占比
            if total > 0:
                stats["positive_rate"] = round(stats["positive"] / total, 3)
                stats["neutral_rate"] = round(stats["neutral"] / total, 3)
                stats["negative_rate"] = round(stats["negative"] / total, 3)
                stats["ignored_rate"] = round(stats["ignored"] / total, 3)
            else:
                stats["positive_rate"] = 0
                stats["neutral_rate"] = 0
                stats["negative_rate"] = 0
                stats["ignored_rate"] = 0

            # 最有效和最需优化的模板
            template_stats = []
            for source_key, s in self._template_effectiveness.items():
                used = s.get("used", 0)
                if used == 0:
                    continue
                positive_rate = s.get("positive", 0) / used
                negative_rate = s.get("negative", 0) / used
                template_stats.append({
                    "source": source_key,
                    "used": used,
                    "positive": s.get("positive", 0),
                    "neutral": s.get("neutral", 0),
                    "negative": s.get("negative", 0),
                    "ignored": s.get("ignored", 0),
                    "positive_rate": round(positive_rate, 3),
                    "negative_rate": round(negative_rate, 3),
                })

            # 按积极率排序
            template_stats.sort(key=lambda x: x["positive_rate"], reverse=True)
            best_templates = template_stats[:3] if template_stats else []
            worst_templates = sorted(template_stats, key=lambda x: x["negative_rate"], reverse=True)[:3]

            # 最近的回复记录（最多20条）
            recent_records = []
            for r in reversed(self._reply_records[-20:]):
                recent_records.append({
                    "id": r["id"],
                    "reply_text": r["reply_text"][:80],
                    "source": r["source"],
                    "effect": r["effect"],
                    "evaluated": r["evaluated"],
                    "timestamp": r["timestamp"],
                    "hr_response": (r["hr_response"] or "")[:80],
                })

            return {
                "reply_stats": stats,
                "template_effectiveness": template_stats,
                "best_templates": best_templates,
                "worst_templates": worst_templates,
                "strategy_adjustments": list(self._strategy_adjustments[-20:]),
                "recent_records": recent_records,
                "enabled": self.enabled,
            }

    # ─────────────────────────────────────────────
    # 持久化
    # ─────────────────────────────────────────────

    def _save_internal(self):
        """内部保存方法（不加锁，由调用方负责加锁）。"""
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "reply_stats": dict(self._reply_stats),
                "template_effectiveness": {
                    k: dict(v) for k, v in self._template_effectiveness.items()
                },
                "strategy_adjustments": list(self._strategy_adjustments),
                "reply_records": self._reply_records[-200:],  # 保留最近200条
                "last_saved": datetime.now().isoformat(),
            }
            with open(self._data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log("ERROR", f"保存进化数据失败: {e}")

    def save_evolution_data(self):
        """持久化进化数据到文件。"""
        with self._lock:
            self._save_internal()
        self._log("DEBUG", "进化数据已保存")

    def load_evolution_data(self):
        """从文件加载进化数据。"""
        try:
            if self._data_file.exists():
                with open(self._data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self._reply_stats = data.get("reply_stats", self._reply_stats)
                self._template_effectiveness = data.get("template_effectiveness", {})
                self._strategy_adjustments = data.get("strategy_adjustments", [])
                self._reply_records = data.get("reply_records", [])

                self._log("INFO",
                          f"进化数据已加载: {self._reply_stats['total_replies']} 条回复记录, "
                          f"{len(self._strategy_adjustments)} 项策略调整")
            else:
                self._log("DEBUG", "无历史进化数据文件，使用空数据初始化")
        except Exception as e:
            self._log("ERROR", f"加载进化数据失败: {e}")

    # ─────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────

    def get_pending_evaluations(self) -> int:
        """获取待评估的 AI 回复数量。"""
        with self._lock:
            return sum(1 for r in self._reply_records if not r["evaluated"])

    def reset_stats(self):
        """重置统计数据（不清除策略调整历史）。"""
        with self._lock:
            self._reply_records = []
            self._reply_stats = {
                "total_replies": 0,
                "positive": 0,
                "neutral": 0,
                "negative": 0,
                "ignored": 0,
            }
            self._template_effectiveness = {}
            self._reply_count_since_optimize = 0
            self._save_internal()
        self._log("INFO", "统计数据已重置")

    def set_enabled(self, enabled: bool):
        """启用或禁用自进化功能。"""
        self.enabled = enabled
        self._log("INFO", f"自进化功能已{'启用' if enabled else '禁用'}")
    # ─────────────────────────────────────────────
    # 回复质量评分
    # ─────────────────────────────────────────────

    def _get_reply_records_from_store(self, limit: int = 50) -> List[Dict[str, Any]]:
        """从 ReplyRecordStore 获取最近的回复记录。

        Args:
            limit: 最多获取的记录数

        Returns:
            回复记录字典列表，每条包含 received_message、reply_content、reply_source 等
        """
        try:
            from boss_bot.reply_record import _get_reply_store
            store = _get_reply_store()
            records = store.get_all()
            # 取最近 limit 条已回复的记录（排除跳过的）
            valid_records = [
                r for r in records
                if not r.is_skipped and r.reply_content and r.received_message
            ]
            recent = valid_records[-limit:] if limit > 0 else valid_records
            return [
                {
                    "chat_name": r.chat_name,
                    "job_name": r.job_name,
                    "received_message": r.received_message or "",
                    "reply_content": r.reply_content or "",
                    "reply_source": r.reply_source or "",
                    "reply_intent": r.reply_intent or "",
                    "timestamp": r.timestamp,
                    "ai_model": r.ai_model or "",
                }
                for r in recent
            ]
        except Exception as e:
            self._log("ERROR", f"获取回复记录失败: {e}")
            return []

    def _get_ai_providers(self) -> List[dict]:
        """获取已配置的 AI 提供商列表。

        优先使用 Agnes 或商汤(SenseNova)。

        Returns:
            provider 字典列表，每个包含 key/model/url
        """
        try:
            from boss_bot.config import AI_PROVIDERS
            if not AI_PROVIDERS:
                return []
            # 优先排序：agnes 和 sensenova 排前面
            def _priority(p):
                model = (p.get("model", "") + p.get("url", "")).lower()
                if "agnes" in model:
                    return 0
                if "sensenova" in model or "sensetime" in model:
                    return 1
                return 2
            return sorted(AI_PROVIDERS, key=_priority)
        except Exception:
            return []

    def _call_ai_for_scoring(self, prompt: str, system_prompt: str = "") -> Optional[str]:
        """调用 AI 进行评分/优化（OpenAI 兼容格式）。

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词

        Returns:
            AI 返回的文本，失败返回 None
        """
        providers = self._get_ai_providers()
        if not providers:
            return None

        try:
            from openai import OpenAI
        except ImportError:
            self._log("WARNING", "未安装 openai 库，无法使用 AI 评分")
            return None

        # 只尝试前 2 个可用 provider，避免长时间等待
        max_attempts = 2
        attempted = 0

        for provider in providers:
            if attempted >= max_attempts:
                break

            api_key = provider.get("key", "")
            model = provider.get("model", "")
            base_url = provider.get("url", "")
            if not api_key or not model or not base_url:
                continue

            attempted += 1

            try:
                # 设置 10 秒超时，避免长时间阻塞
                client = OpenAI(api_key=api_key, base_url=base_url, timeout=10.0)
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})

                response = client.chat.completions.create(
                    model=model,
                    max_tokens=1000,
                    temperature=0.3,  # 评分需要确定性，降低温度
                    messages=messages,
                )
                content = response.choices[0].message.content
                return content.strip() if content else None
            except Exception as e:
                self._log("DEBUG", f"AI 调用失败 [{model}]: {str(e)[:100]}")
                continue

        return None

    def _heuristic_score(self, received_message: str, reply_content: str,
                         reply_source: str = "") -> Dict[str, int]:
        """规则启发式评分（AI 不可用时的降级方案）。

        评分维度：
        - 相关性（30分）：回复内容是否与收到的消息相关
        - 完整性（30分）：回复是否完整回答了对方的问题
        - 自然度（20分）：回复是否像真人说话
        - 有效性（20分）：回复是否推动了对话进展

        Args:
            received_message: 收到的消息
            reply_content: 回复内容
            reply_source: 回复来源（rule/intent/ai/default）

        Returns:
            {"relevance": int, "completeness": int, "naturalness": int, "effectiveness": int}
        """
        received = (received_message or "").strip()
        reply = (reply_content or "").strip()

        # ── 相关性评分（30分）──
        relevance = 0
        if reply_source == "rule":
            # 规则匹配通常相关性较高
            relevance = 25
        elif reply_source == "intent":
            relevance = 22
        elif reply_source == "ai":
            relevance = 20
        elif reply_source == "default":
            # 兜底回复相关性较低
            relevance = 10

        # 检查回复中是否包含消息中的关键词
        if received and reply:
            # 提取消息中的关键词（简单分词）
            msg_keywords = set(re.findall(r"[\u4e00-\u9fa5]{2,}", received))
            reply_keywords = set(re.findall(r"[\u4e00-\u9fa5]{2,}", reply))
            if msg_keywords:
                overlap = msg_keywords & reply_keywords
                overlap_ratio = len(overlap) / len(msg_keywords)
                relevance += int(5 * overlap_ratio)

        relevance = min(relevance, SCORE_RELEVANCE_MAX)

        # ── 完整性评分（30分）──
        completeness = 0
        if not reply:
            completeness = 0
        else:
            # 检查是否包含问号（回答了问题）
            has_question = "？" in received or "?" in received
            if has_question:
                # 如果对方问了问题，回复应该包含具体信息
                if len(reply) >= 10:
                    completeness = 20
                if len(reply) >= 30:
                    completeness = 25
                # 检查是否包含具体回答（数字、时间等）
                if re.search(r"\d+|可以|能够|方便|没问题", reply):
                    completeness = min(completeness + 5, SCORE_COMPLETENESS_MAX)
            else:
                # 非问题消息，回复确认即可
                if len(reply) >= 5:
                    completeness = 25
                if len(reply) >= 15:
                    completeness = 30

        # ── 自然度评分（20分）──
        naturalness = 0
        if not reply:
            naturalness = 0
        else:
            # 检查是否像真人说话
            # 过长或过短都不自然
            length = len(reply)
            if 5 <= length <= 100:
                naturalness = 15
            elif length > 100:
                naturalness = 10  # 过长，像机器
            elif length > 0:
                naturalness = 8  # 过短

            # 检查是否包含口语化表达
            if any(w in reply for w in ["~", "哈", "呢", "呀", "哦", "嗯"]):
                naturalness = min(naturalness + 3, SCORE_NATURALNESS_MAX)

            # 检查是否过于模板化（连续重复词）
            if re.search(r"(.{3,})\1{2,}", reply):
                naturalness = max(naturalness - 5, 0)

            # AI 回复通常更自然
            if reply_source == "ai":
                naturalness = min(naturalness + 2, SCORE_NATURALNESS_MAX)

        # ── 有效性评分（20分）──
        effectiveness = 0
        if not reply:
            effectiveness = 0
        else:
            # 检查是否推动对话进展
            # 包含提问、邀约、确认等
            if re.search(r"[？?]|方便|可以吗|怎么样|何时|什么时候", reply):
                effectiveness = 15  # 包含提问，推动对话
            elif re.search(r"面试|面谈|沟通|联系|安排", reply):
                effectiveness = 18  # 包含邀约，高度有效
            elif re.search(r"好的|可以|没问题|感谢|谢谢", reply):
                effectiveness = 12  # 确认回复
            else:
                effectiveness = 8  # 普通回复

            # 兜底回复有效性低
            if reply_source == "default":
                effectiveness = max(effectiveness - 5, 0)

        return {
            "relevance": relevance,
            "completeness": completeness,
            "naturalness": naturalness,
            "effectiveness": effectiveness,
        }

    def _parse_ai_scoring_response(self, ai_response: str) -> Optional[Dict[str, int]]:
        """解析 AI 评分响应。

        期望 AI 返回 JSON 格式：
        {"relevance": 25, "completeness": 20, "naturalness": 15, "effectiveness": 12}

        Args:
            ai_response: AI 返回的文本

        Returns:
            评分字典，解析失败返回 None
        """
        if not ai_response:
            return None

        try:
            # 尝试从文本中提取 JSON
            # AI 可能返回 ```json ... ``` 或纯 JSON
            text = ai_response.strip()

            # 去除 markdown 代码块标记
            if text.startswith("```"):
                lines = text.split("\n")
                # 去除首行 ```json 和末行 ```
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines).strip()

            # 尝试找到 JSON 部分
            json_match = re.search(r"\{[^}]+\}", text, re.DOTALL)
            if json_match:
                text = json_match.group(0)

            data = json.loads(text)

            # 验证并限制范围
            def _clamp(val, max_val):
                try:
                    v = int(val)
                    return max(0, min(v, max_val))
                except (ValueError, TypeError):
                    return 0

            return {
                "relevance": _clamp(data.get("relevance", 0), SCORE_RELEVANCE_MAX),
                "completeness": _clamp(data.get("completeness", 0), SCORE_COMPLETENESS_MAX),
                "naturalness": _clamp(data.get("naturalness", 0), SCORE_NATURALNESS_MAX),
                "effectiveness": _clamp(data.get("effectiveness", 0), SCORE_EFFECTIVENESS_MAX),
            }
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            self._log("DEBUG", f"解析 AI 评分响应失败: {e}")
            return None

    def evaluate_reply_quality(self, limit: int = 50) -> dict:
        """评估最近N条回复的质量。

        读取 reply_record.py 中的回复记录，对每条回复评分（0-100分）。
        评分维度：
        - 相关性（30分）：回复内容是否与收到的消息相关
        - 完整性（30分）：回复是否完整回答了对方的问题
        - 自然度（20分）：回复是否像真人说话，不像机器
        - 有效性（20分）：回复是否推动了对话进展

        评分方法：优先使用 AI 批量评分，AI 不可用时降级到规则启发式评分。
        评分结果存入 self_evolve_data.json。

        Args:
            limit: 评估最近 N 条回复，默认 50

        Returns:
            {
                "total_evaluated": int,
                "avg_score": float,
                "avg_relevance": float,
                "avg_completeness": float,
                "avg_naturalness": float,
                "avg_effectiveness": float,
                "low_score_replies": list,  # 低于60分的回复
                "suggestions": list  # 改进建议
            }
        """
        if not self.enabled:
            return {"status": "disabled", "message": "自进化功能未启用"}

        # 获取回复记录
        records = self._get_reply_records_from_store(limit=limit)
        if not records:
            self._log("INFO", "无回复记录可评估")
            return {
                "total_evaluated": 0,
                "avg_score": 0,
                "avg_relevance": 0,
                "avg_completeness": 0,
                "avg_naturalness": 0,
                "avg_effectiveness": 0,
                "low_score_replies": [],
                "suggestions": ["暂无回复记录，无法评估"],
            }

        self._log("INFO", f"开始评估 {len(records)} 条回复质量")

        # 尝试使用 AI 批量评分
        ai_available = bool(self._get_ai_providers())
        scoring_method = "ai" if ai_available else "heuristic"

        # AI 可用性预检查：用第一条记录测试 AI 是否真的可用
        if ai_available:
            test_record = records[0]
            test_score = self._score_single_reply_with_ai(
                test_record.get("received_message", ""),
                test_record.get("reply_content", ""),
                test_record.get("reply_source", ""),
            )
            if test_score is None:
                ai_available = False
                scoring_method = "heuristic"
                self._log("INFO", "AI 评分预检查失败，降级到启发式评分")

        evaluations = []
        for record in records:
            received = record.get("received_message", "")
            reply = record.get("reply_content", "")
            source = record.get("reply_source", "")

            score_data = None

            # 优先尝试 AI 评分
            if ai_available:
                score_data = self._score_single_reply_with_ai(received, reply, source)

            # AI 评分失败，降级到启发式评分
            if score_data is None:
                score_data = self._heuristic_score(received, reply, source)
                if scoring_method == "ai":
                    scoring_method = "heuristic"

            total_score = sum(score_data.values())

            evaluation = {
                "received_message": received[:100],
                "reply_content": reply[:100],
                "reply_source": source,
                "reply_intent": record.get("reply_intent", ""),
                "chat_name": record.get("chat_name", ""),
                "score": total_score,
                "relevance": score_data["relevance"],
                "completeness": score_data["completeness"],
                "naturalness": score_data["naturalness"],
                "effectiveness": score_data["effectiveness"],
                "scoring_method": scoring_method,
                "timestamp": record.get("timestamp", datetime.now().isoformat()),
            }
            evaluations.append(evaluation)

            # 更新模板使用统计
            self._update_template_usage_stats(source, total_score)

        # 计算统计值
        total_evaluated = len(evaluations)
        avg_relevance = sum(e["relevance"] for e in evaluations) / total_evaluated
        avg_completeness = sum(e["completeness"] for e in evaluations) / total_evaluated
        avg_naturalness = sum(e["naturalness"] for e in evaluations) / total_evaluated
        avg_effectiveness = sum(e["effectiveness"] for e in evaluations) / total_evaluated
        avg_score = sum(e["score"] for e in evaluations) / total_evaluated

        # 找出低分回复
        low_score_replies = [
            {
                "received_message": e["received_message"],
                "reply_content": e["reply_content"],
                "reply_source": e["reply_source"],
                "score": e["score"],
                "timestamp": e["timestamp"],
            }
            for e in evaluations
            if e["score"] < LOW_SCORE_THRESHOLD
        ]

        # 生成改进建议
        suggestions = self._generate_improvement_suggestions(
            avg_relevance, avg_completeness, avg_naturalness, avg_effectiveness,
            low_score_replies
        )

        # 保存评分结果
        with self._lock:
            self._reply_evaluations.extend(evaluations)
            # 保留最近 1000 条评分记录
            if len(self._reply_evaluations) > 1000:
                self._reply_evaluations = self._reply_evaluations[-1000:]
            self._save_quality_data()

        result = {
            "total_evaluated": total_evaluated,
            "avg_score": round(avg_score, 2),
            "avg_relevance": round(avg_relevance, 2),
            "avg_completeness": round(avg_completeness, 2),
            "avg_naturalness": round(avg_naturalness, 2),
            "avg_effectiveness": round(avg_effectiveness, 2),
            "low_score_replies": low_score_replies,
            "suggestions": suggestions,
            "scoring_method": scoring_method,
        }

        self._log("INFO",
                  f"回复质量评估完成: {total_evaluated} 条, 平均分 {avg_score:.1f}, "
                  f"低分 {len(low_score_replies)} 条, 评分方式={scoring_method}")

        return result

    def _score_single_reply_with_ai(self, received_message: str, reply_content: str,
                                     reply_source: str) -> Optional[Dict[str, int]]:
        """使用 AI 评分单条回复。

        Args:
            received_message: 收到的消息
            reply_content: 回复内容
            reply_source: 回复来源

        Returns:
            评分字典，失败返回 None
        """
        if not received_message or not reply_content:
            return None

        system_prompt = (
            "你是一个回复质量评分专家。请对求职者在 BOSS 直聘上的回复进行评分。\n"
            "评分维度：\n"
            f"- relevance: 相关性（0-{SCORE_RELEVANCE_MAX}分），回复内容是否与收到的消息相关\n"
            f"- completeness: 完整性（0-{SCORE_COMPLETENESS_MAX}分），回复是否完整回答了对方的问题\n"
            f"- naturalness: 自然度（0-{SCORE_NATURALNESS_MAX}分），回复是否像真人说话，不像机器\n"
            f"- effectiveness: 有效性（0-{SCORE_EFFECTIVENESS_MAX}分），回复是否推动了对话进展\n"
            "请只返回 JSON 格式，不要其他内容。格式：\n"
            '{"relevance": 25, "completeness": 20, "naturalness": 15, "effectiveness": 12}'
        )

        prompt = (
            f"收到的消息：{received_message[:200]}\n"
            f"回复内容：{reply_content[:200]}\n"
            f"回复来源：{reply_source}\n\n"
            "请对这条回复进行评分，只返回 JSON。"
        )

        try:
            ai_response = self._call_ai_for_scoring(prompt, system_prompt)
            if ai_response:
                return self._parse_ai_scoring_response(ai_response)
        except Exception as e:
            self._log("DEBUG", f"AI 评分异常: {e}")

        return None

    def _update_template_usage_stats(self, template_key: str, score: int):
        """更新模板使用统计。

        Args:
            template_key: 模板/来源标识
            score: 本次评分
        """
        if not template_key:
            template_key = "unknown"

        if template_key not in self._template_usage_stats:
            self._template_usage_stats[template_key] = {
                "usage_count": 0,
                "total_score": 0,
                "avg_score": 0,
                "last_used": datetime.now().isoformat(),
            }

        stats = self._template_usage_stats[template_key]
        stats["usage_count"] += 1
        stats["total_score"] += score
        stats["avg_score"] = round(stats["total_score"] / stats["usage_count"], 2)
        stats["last_used"] = datetime.now().isoformat()

    def _generate_improvement_suggestions(self, avg_relevance: float,
                                           avg_completeness: float,
                                           avg_naturalness: float,
                                           avg_effectiveness: float,
                                           low_score_replies: list) -> list:
        """根据评分结果生成改进建议。

        Args:
            avg_relevance: 平均相关性得分
            avg_completeness: 平均完整性得分
            avg_naturalness: 平均自然度得分
            avg_effectiveness: 平均有效性得分
            low_score_replies: 低分回复列表

        Returns:
            改进建议列表
        """
        suggestions = []

        # 相关性建议
        if avg_relevance < SCORE_RELEVANCE_MAX * 0.6:
            suggestions.append(
                f"相关性得分偏低（{avg_relevance:.1f}/{SCORE_RELEVANCE_MAX}），"
                "建议优化规则匹配精度，确保回复内容与收到消息紧密相关"
            )

        # 完整性建议
        if avg_completeness < SCORE_COMPLETENESS_MAX * 0.6:
            suggestions.append(
                f"完整性得分偏低（{avg_completeness:.1f}/{SCORE_COMPLETENESS_MAX}），"
                "建议完善回复模板，确保完整回答对方问题，增加具体信息"
            )

        # 自然度建议
        if avg_naturalness < SCORE_NATURALNESS_MAX * 0.6:
            suggestions.append(
                f"自然度得分偏低（{avg_naturalness:.1f}/{SCORE_NATURALNESS_MAX}），"
                "建议优化回复语气，增加口语化表达，避免过于模板化"
            )

        # 有效性建议
        if avg_effectiveness < SCORE_EFFECTIVENESS_MAX * 0.6:
            suggestions.append(
                f"有效性得分偏低（{avg_effectiveness:.1f}/{SCORE_EFFECTIVENESS_MAX}），"
                "建议在回复中增加提问或邀约，推动对话进展"
            )

        # 低分回复统计
        if low_score_replies:
            # 按来源统计低分回复
            source_counts = {}
            for r in low_score_replies:
                src = r.get("reply_source", "unknown")
                source_counts[src] = source_counts.get(src, 0) + 1

            for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
                suggestions.append(
                    f"来源 '{src}' 有 {count} 条低分回复，建议重点优化该来源的回复策略"
                )

        if not suggestions:
            suggestions.append("回复质量整体良好，继续保持当前策略")

        return suggestions

    # ─────────────────────────────────────────────
    # 规则自动调整
    # ─────────────────────────────────────────────

    def auto_adjust_rules(self, evaluation: dict) -> list:
        """根据评估结果自动调整回复规则。

        分析低分回复对应的规则，检测规则匹配是否过于宽泛或过于狭窄，
        自动调整规则关键词的匹配范围，生成调整建议。

        Args:
            evaluation: evaluate_reply_quality() 的返回结果

        Returns:
            调整建议列表，每条包含：
            {
                "rule_key": str,
                "old_value": str,
                "new_value": str,
                "reason": str,
                "auto_applied": bool
            }
        """
        if not self.enabled:
            return []

        adjustments = []

        # 获取当前规则配置
        try:
            from boss_bot.config import REPLY_RULES
            current_rules = dict(REPLY_RULES or {})
        except Exception:
            current_rules = {}

        # 分析低分回复
        low_score_replies = evaluation.get("low_score_replies", [])
        if not low_score_replies:
            self._log("INFO", "无低分回复，无需调整规则")
            return []

        # 按来源统计低分回复
        source_low_count = {}
        for r in low_score_replies:
            src = r.get("reply_source", "unknown")
            source_low_count[src] = source_low_count.get(src, 0) + 1

        # 获取模板使用统计
        template_stats = self._template_usage_stats

        # 分析每个低分来源的规则
        for source, low_count in source_low_count.items():
            if source not in template_stats:
                continue

            stats = template_stats[source]
            usage_count = stats.get("usage_count", 0)
            avg_score = stats.get("avg_score", 0)

            if usage_count < TEMPLATE_MIN_USAGE_FOR_OPT:
                continue  # 数据量不足

            # 计算低分率
            low_rate = low_count / usage_count if usage_count > 0 else 0

            # 规则过于宽泛：使用次数多但平均分低
            if low_rate > 0.4 and avg_score < LOW_SCORE_THRESHOLD:
                # 查找该来源对应的规则
                rule_keys = self._find_rules_by_source(source, current_rules)

                for rule_key in rule_keys:
                    old_value = current_rules.get(rule_key, "")
                    if not old_value:
                        continue

                    # 分析规则是否过于宽泛
                    new_value, reason = self._suggest_rule_narrowing(
                        rule_key, old_value, source, low_rate
                    )

                    if new_value is not None:
                        adjustment = {
                            "rule_key": rule_key,
                            "old_value": old_value if isinstance(old_value, str) else str(old_value),
                            "new_value": new_value if isinstance(new_value, str) else str(new_value),
                            "reason": reason,
                            "auto_applied": False,  # 默认不自动应用，需人工确认
                            "timestamp": datetime.now().isoformat(),
                        }
                        adjustments.append(adjustment)

        # 分析评分维度短板，给出规则调整建议
        avg_relevance = evaluation.get("avg_relevance", 0)

        if avg_relevance < SCORE_RELEVANCE_MAX * 0.6:
            # 相关性低，建议增加更精确的关键词
            for rule_key, rule_value in current_rules.items():
                if not isinstance(rule_value, str):
                    continue
                # 检查规则关键词是否过少
                keywords = re.findall(r"[\u4e00-\u9fa5]+", rule_value)
                if len(keywords) <= 1:
                    adjustment = {
                        "rule_key": rule_key,
                        "old_value": rule_value,
                        "new_value": rule_value,  # 需要人工补充
                        "reason": f"规则关键词过少（{len(keywords)}个），"
                                  f"建议增加更精确的匹配关键词以提高相关性",
                        "auto_applied": False,
                        "timestamp": datetime.now().isoformat(),
                    }
                    adjustments.append(adjustment)

        # 保存调整记录
        with self._lock:
            self._rule_adjustments.extend(adjustments)
            # 保留最近 200 条调整记录
            if len(self._rule_adjustments) > 200:
                self._rule_adjustments = self._rule_adjustments[-200:]
            self._save_quality_data()

        self._log("INFO", f"规则自动调整完成: 生成 {len(adjustments)} 条建议")

        return adjustments

    def _find_rules_by_source(self, source: str, rules: dict) -> list:
        """根据回复来源查找对应的规则键。

        Args:
            source: 回复来源（rule/intent/ai/default）
            rules: 规则字典

        Returns:
            规则键列表
        """
        if source == "rule":
            # 规则来源：返回所有规则键
            return list(rules.keys())[:5]  # 限制数量
        elif source == "intent":
            # 意图来源：返回意图相关的规则键
            return [k for k in rules.keys() if "intent" in k.lower()][:3]
        elif source == "default":
            # 兜底来源
            return [k for k in rules.keys() if "default" in k.lower()][:2]
        return []

    def _suggest_rule_narrowing(self, rule_key: str, old_value: str,
                                 source: str, low_rate: float) -> tuple:
        """建议规则收窄（使匹配更精确）。

        Args:
            rule_key: 规则键
            old_value: 原规则值
            source: 回复来源
            low_rate: 低分率

        Returns:
            (新规则值, 调整原因)
        """
        reason = f"来源 '{source}' 低分率 {low_rate:.0%}，规则匹配可能过于宽泛，建议收窄匹配范围"

        # 如果规则是列表形式（关键词列表），建议增加更精确的关键词
        if isinstance(old_value, list):
            new_value = old_value  # 不自动修改列表，仅给出建议
            return new_value, reason

        # 如果规则是字符串形式，分析是否需要收窄
        if isinstance(old_value, str):
            # 不自动修改规则内容，仅给出建议
            return old_value, reason

        return old_value, reason

    # ─────────────────────────────────────────────
    # 模板优化
    # ─────────────────────────────────────────────

    def optimize_templates(self) -> list:
        """分析并优化回复模板。

        统计每个模板的使用次数和平均评分，找出使用频率高但评分低的模板，
        使用 AI 生成优化后的模板内容，返回优化建议。

        Returns:
            优化建议列表，每条包含：
            {
                "template_key": str,
                "current_template": str,
                "suggested_template": str,
                "usage_count": int,
                "avg_score": float,
                "reason": str
            }
        """
        if not self.enabled:
            return []

        # 获取当前模板配置
        try:
            from boss_bot.config import (
                SALARY_REPLY, INTERVIEW_TIME_REPLY, JOB_CONTENT_REPLY,
                GREETING_REPLY, DEFAULT_REPLY, RESUME_DUPLICATE_REPLY,
                RESUME_UNAVAILABLE_REPLY,
            )
            templates = {
                "salary_reply": SALARY_REPLY,
                "interview_time_reply": INTERVIEW_TIME_REPLY,
                "job_content_reply": JOB_CONTENT_REPLY,
                "greeting_reply": GREETING_REPLY,
                "default_reply": DEFAULT_REPLY,
                "resume_duplicate_reply": RESUME_DUPLICATE_REPLY,
                "resume_unavailable_reply": RESUME_UNAVAILABLE_REPLY,
            }
        except Exception as e:
            self._log("ERROR", f"获取模板配置失败: {e}")
            return []

        # 模板来源到模板键的映射
        source_to_template = {
            "rule": ["salary_reply", "interview_time_reply", "job_content_reply"],
            "intent": ["greeting_reply", "job_content_reply"],
            "default": ["default_reply", "resume_duplicate_reply", "resume_unavailable_reply"],
            "ai": [],  # AI 回复不使用固定模板
        }

        optimizations = []

        for template_key, template_content in templates.items():
            if not template_content:
                continue

            # 查找该模板的使用统计
            usage_count = 0
            avg_score = 0

            # 通过来源映射查找统计
            for source, template_keys in source_to_template.items():
                if template_key in template_keys:
                    stats = self._template_usage_stats.get(source, {})
                    if stats.get("usage_count", 0) > usage_count:
                        usage_count = stats.get("usage_count", 0)
                        avg_score = stats.get("avg_score", 0)

            # 也可以直接用 template_key 查找
            if usage_count == 0:
                stats = self._template_usage_stats.get(template_key, {})
                usage_count = stats.get("usage_count", 0)
                avg_score = stats.get("avg_score", 0)

            # 数据量不足，跳过
            if usage_count < TEMPLATE_MIN_USAGE_FOR_OPT:
                continue

            # 判断是否需要优化
            needs_optimization = avg_score < LOW_SCORE_THRESHOLD

            if not needs_optimization:
                continue  # 模板效果良好，不需要优化

            # 生成优化建议
            suggested_template, reason = self._optimize_template_content(
                template_key, template_content, usage_count, avg_score
            )

            optimization = {
                "template_key": template_key,
                "current_template": template_content,
                "suggested_template": suggested_template,
                "usage_count": usage_count,
                "avg_score": avg_score,
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
                "applied": False,
            }
            optimizations.append(optimization)

        # 保存优化记录
        with self._lock:
            self._template_optimizations.extend(optimizations)
            # 保留最近 100 条优化记录
            if len(self._template_optimizations) > 100:
                self._template_optimizations = self._template_optimizations[-100:]
            self._save_quality_data()

        self._log("INFO", f"模板优化完成: 生成 {len(optimizations)} 条建议")

        return optimizations

    def _optimize_template_content(self, template_key: str, current_template: str,
                                    usage_count: int, avg_score: float) -> tuple:
        """优化单个模板内容。

        优先使用 AI 生成优化建议，AI 不可用时使用规则启发式优化。

        Args:
            template_key: 模板键
            current_template: 当前模板内容
            usage_count: 使用次数
            avg_score: 平均评分

        Returns:
            (优化后的模板内容, 优化原因)
        """
        reason = f"使用 {usage_count} 次，平均分 {avg_score:.1f}，低于阈值 {LOW_SCORE_THRESHOLD}"

        # 尝试使用 AI 优化
        providers = self._get_ai_providers()
        if providers:
            system_prompt = (
                "你是求职回复话术优化专家。请优化以下在 BOSS 直聘上使用的回复模板，"
                "使其更自然、更有效、更能推动对话进展。\n"
                "优化要求：\n"
                "1. 保持专业礼貌的语气\n"
                "2. 增加口语化表达，更像真人说话\n"
                "3. 适当增加提问或邀约，推动对话进展\n"
                "4. 控制在 1-2 句话，简洁明了\n"
                "5. 保留原模板中的占位符（如 {salary}、{interview_time} 等）\n"
                "只返回优化后的模板内容，不要解释。"
            )

            prompt = (
                f"模板类型：{template_key}\n"
                f"当前模板：{current_template}\n"
                f"使用次数：{usage_count}\n"
                f"平均评分：{avg_score:.1f}/100\n\n"
                "请优化这个模板。"
            )

            try:
                ai_response = self._call_ai_for_scoring(prompt, system_prompt)
                if ai_response and len(ai_response) > 5:
                    # 清理 AI 响应
                    suggested = ai_response.strip()
                    # 去除引号
                    suggested = suggested.strip('"\'""''')
                    if suggested and suggested != current_template:
                        return suggested, reason + "（AI 优化）"
            except Exception as e:
                self._log("DEBUG", f"AI 模板优化失败: {e}")

        # AI 不可用，使用启发式优化
        suggested = self._heuristic_optimize_template(template_key, current_template)
        return suggested, reason + "（规则启发式优化）"

    def _heuristic_optimize_template(self, template_key: str, template_content: str) -> str:
        """规则启发式优化模板内容。

        Args:
            template_key: 模板键
            template_content: 当前模板内容

        Returns:
            优化后的模板内容
        """
        if not template_content:
            return template_content

        optimized = template_content

        # 根据模板类型进行针对性优化
        if template_key == "default_reply":
            # 兜底回复：增加提问推动对话
            if "？" not in optimized and "?" not in optimized:
                optimized = optimized.rstrip("。.") + "，有什么我可以进一步了解的吗？"

        elif template_key == "greeting_reply":
            # 打招呼：增加具体性
            if "方便" not in optimized:
                optimized = optimized.rstrip("。.") + "，方便详细聊聊吗？"

        elif template_key == "salary_reply":
            # 薪资回复：确保包含面谈邀约
            if "面谈" not in optimized:
                optimized = optimized.rstrip("。.") + "，具体可以面谈。"

        elif template_key == "interview_time_reply":
            # 面试时间：确保有提问
            if "？" not in optimized and "?" not in optimized:
                optimized = optimized.rstrip("。.") + "，您看哪个时间方便？"

        return optimized

    # ─────────────────────────────────────────────
    # 进化报告生成
    # ─────────────────────────────────────────────

    def generate_report(self, evaluation: Optional[dict] = None,
                        rule_adjustments: Optional[list] = None,
                        template_optimizations: Optional[list] = None) -> str:
        """生成人类可读的进化报告。

        包含：
        - 统计数据（总回复数、平均评分、改进幅度）
        - 问题分析（哪些类型消息回复效果差）
        - 改进措施（已调整的规则和模板）
        - 下一步建议

        Args:
            evaluation: 评估结果，None 时使用最近一次评估
            rule_adjustments: 规则调整列表，None 时使用最近记录
            template_optimizations: 模板优化列表，None 时使用最近记录

        Returns:
            人类可读的进化报告字符串
        """
        # 使用传入的数据或最近的数据
        if evaluation is None:
            # 使用最近的评分数据计算
            if self._reply_evaluations:
                recent_evals = self._reply_evaluations[-50:]
                total = len(recent_evals)
                avg_score = sum(e.get("score", 0) for e in recent_evals) / total
                avg_relevance = sum(e.get("relevance", 0) for e in recent_evals) / total
                avg_completeness = sum(e.get("completeness", 0) for e in recent_evals) / total
                avg_naturalness = sum(e.get("naturalness", 0) for e in recent_evals) / total
                avg_effectiveness = sum(e.get("effectiveness", 0) for e in recent_evals) / total
                evaluation = {
                    "total_evaluated": total,
                    "avg_score": round(avg_score, 2),
                    "avg_relevance": round(avg_relevance, 2),
                    "avg_completeness": round(avg_completeness, 2),
                    "avg_naturalness": round(avg_naturalness, 2),
                    "avg_effectiveness": round(avg_effectiveness, 2),
                    "low_score_replies": [
                        {"reply_source": e.get("reply_source", "")}
                        for e in recent_evals
                        if e.get("score", 0) < LOW_SCORE_THRESHOLD
                    ],
                    "suggestions": [],
                }
            else:
                evaluation = {
                    "total_evaluated": 0,
                    "avg_score": 0,
                    "avg_relevance": 0,
                    "avg_completeness": 0,
                    "avg_naturalness": 0,
                    "avg_effectiveness": 0,
                    "low_score_replies": [],
                    "suggestions": [],
                }

        if rule_adjustments is None:
            rule_adjustments = self._rule_adjustments[-10:] if self._rule_adjustments else []

        if template_optimizations is None:
            template_optimizations = self._template_optimizations[-10:] if self._template_optimizations else []

        # 生成报告
        lines = []
        lines.append("=" * 60)
        lines.append("              AI 自进化报告")
        lines.append(f"              生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 60)

        # ── 一、统计数据 ──
        lines.append("")
        lines.append("一、统计数据")
        lines.append("-" * 40)
        lines.append(f"  评估回复数：{evaluation.get('total_evaluated', 0)} 条")
        lines.append(f"  平均总评分：{evaluation.get('avg_score', 0):.1f} / {SCORE_TOTAL_MAX}")
        lines.append(f"  相关性得分：{evaluation.get('avg_relevance', 0):.1f} / {SCORE_RELEVANCE_MAX}")
        lines.append(f"  完整性得分：{evaluation.get('avg_completeness', 0):.1f} / {SCORE_COMPLETENESS_MAX}")
        lines.append(f"  自然度得分：{evaluation.get('avg_naturalness', 0):.1f} / {SCORE_NATURALNESS_MAX}")
        lines.append(f"  有效性得分：{evaluation.get('avg_effectiveness', 0):.1f} / {SCORE_EFFECTIVENESS_MAX}")

        # 评分等级
        avg_score = evaluation.get("avg_score", 0)
        if avg_score >= 80:
            grade = "优秀"
        elif avg_score >= 70:
            grade = "良好"
        elif avg_score >= 60:
            grade = "合格"
        else:
            grade = "需改进"
        lines.append(f"  质量等级：{grade}")

        # ── 二、问题分析 ──
        lines.append("")
        lines.append("二、问题分析")
        lines.append("-" * 40)

        low_score_replies = evaluation.get("low_score_replies", [])
        if low_score_replies:
            lines.append(f"  低分回复数：{len(low_score_replies)} 条（低于 {LOW_SCORE_THRESHOLD} 分）")

            # 按来源统计
            source_counts = {}
            for r in low_score_replies:
                src = r.get("reply_source", "unknown")
                source_counts[src] = source_counts.get(src, 0) + 1

            lines.append("  低分回复来源分布：")
            for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
                lines.append(f"    - {src}: {count} 条")
        else:
            lines.append("  无低分回复，质量良好")

        # 维度短板分析
        avg_relevance = evaluation.get("avg_relevance", 0)
        avg_completeness = evaluation.get("avg_completeness", 0)
        avg_naturalness = evaluation.get("avg_naturalness", 0)
        avg_effectiveness = evaluation.get("avg_effectiveness", 0)

        weaknesses = []
        if avg_relevance < SCORE_RELEVANCE_MAX * 0.6:
            weaknesses.append("相关性")
        if avg_completeness < SCORE_COMPLETENESS_MAX * 0.6:
            weaknesses.append("完整性")
        if avg_naturalness < SCORE_NATURALNESS_MAX * 0.6:
            weaknesses.append("自然度")
        if avg_effectiveness < SCORE_EFFECTIVENESS_MAX * 0.6:
            weaknesses.append("有效性")

        if weaknesses:
            lines.append(f"  主要短板：{', '.join(weaknesses)}")
        else:
            lines.append("  各维度得分均衡，无明显短板")

        # ── 三、改进措施 ──
        lines.append("")
        lines.append("三、改进措施")
        lines.append("-" * 40)

        if rule_adjustments:
            lines.append(f"  规则调整建议：{len(rule_adjustments)} 条")
            for adj in rule_adjustments[-5:]:
                lines.append(f"    - [{adj.get('rule_key', '')}] {adj.get('reason', '')}")
        else:
            lines.append("  规则调整建议：无")

        if template_optimizations:
            lines.append(f"  模板优化建议：{len(template_optimizations)} 条")
            for opt in template_optimizations[-5:]:
                lines.append(f"    - [{opt.get('template_key', '')}] "
                             f"使用 {opt.get('usage_count', 0)} 次, "
                             f"平均分 {opt.get('avg_score', 0):.1f}")
        else:
            lines.append("  模板优化建议：无")

        # ── 四、下一步建议 ──
        lines.append("")
        lines.append("四、下一步建议")
        lines.append("-" * 40)

        suggestions = evaluation.get("suggestions", [])
        if suggestions:
            for i, s in enumerate(suggestions, 1):
                lines.append(f"  {i}. {s}")
        else:
            lines.append("  1. 继续收集回复数据，积累更多评估样本")
            lines.append("  2. 定期运行自进化周期，持续优化回复质量")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)

    # ─────────────────────────────────────────────
    # 自进化主循环
    # ─────────────────────────────────────────────

    def run_evolution_cycle(self) -> dict:
        """执行一次完整的自进化周期。

        流程：
        1. 评估回复质量
        2. 自动调整规则
        3. 优化模板
        4. 生成进化报告
        5. 持久化结果

        Returns:
            {
                "evaluation": dict,
                "rule_adjustments": list,
                "template_optimizations": list,
                "report": str,  # 人类可读的进化报告
                "timestamp": str
            }
        """
        if not self.enabled:
            return {"status": "disabled", "message": "自进化功能未启用"}

        timestamp = datetime.now().isoformat()
        self._log("INFO", f"开始执行自进化周期 @ {timestamp}")

        try:
            # 1. 评估回复质量
            self._log("INFO", "[自进化] 步骤 1/4: 评估回复质量")
            evaluation = self.evaluate_reply_quality()

            # 2. 自动调整规则
            self._log("INFO", "[自进化] 步骤 2/4: 自动调整规则")
            rule_adjustments = self.auto_adjust_rules(evaluation)

            # 3. 优化模板
            self._log("INFO", "[自进化] 步骤 3/4: 优化模板")
            template_optimizations = self.optimize_templates()

            # 4. 生成进化报告
            self._log("INFO", "[自进化] 步骤 4/4: 生成进化报告")
            report = self.generate_report(
                evaluation=evaluation,
                rule_adjustments=rule_adjustments,
                template_optimizations=template_optimizations,
            )

            # 5. 持久化结果
            with self._lock:
                self._save_quality_data()

            result = {
                "evaluation": evaluation,
                "rule_adjustments": rule_adjustments,
                "template_optimizations": template_optimizations,
                "report": report,
                "timestamp": timestamp,
            }

            self._log("INFO",
                      f"自进化周期完成: 评估 {evaluation.get('total_evaluated', 0)} 条回复, "
                      f"规则调整 {len(rule_adjustments)} 条, "
                      f"模板优化 {len(template_optimizations)} 条")

            return result

        except Exception as e:
            self._log("ERROR", f"自进化周期执行失败: {e}")
            return {
                "status": "error",
                "message": str(e),
                "timestamp": timestamp,
            }

    # ─────────────────────────────────────────────
    # 质量数据持久化
    # ─────────────────────────────────────────────

    def _save_quality_data(self):
        """保存质量评分数据到 self_evolve_data.json（不加锁，由调用方负责加锁）。"""
        try:
            self._quality_data_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "reply_evaluations": self._reply_evaluations[-1000:],  # 保留最近1000条
                "rule_adjustments": self._rule_adjustments[-200:],    # 保留最近200条
                "template_optimizations": self._template_optimizations[-100:],  # 保留最近100条
                "template_usage_stats": {
                    k: dict(v) for k, v in self._template_usage_stats.items()
                },
                "last_saved": datetime.now().isoformat(),
            }
            with open(self._quality_data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log("ERROR", f"保存质量数据失败: {e}")

    def _load_quality_data(self):
        """从 self_evolve_data.json 加载质量评分数据。"""
        try:
            if self._quality_data_file.exists():
                with open(self._quality_data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self._reply_evaluations = data.get("reply_evaluations", [])
                self._rule_adjustments = data.get("rule_adjustments", [])
                self._template_optimizations = data.get("template_optimizations", [])
                self._template_usage_stats = data.get("template_usage_stats", {})

                self._log("INFO",
                          f"质量数据已加载: {len(self._reply_evaluations)} 条评分记录, "
                          f"{len(self._rule_adjustments)} 条规则调整, "
                          f"{len(self._template_optimizations)} 条模板优化")
            else:
                self._log("DEBUG", "无历史质量数据文件，使用空数据初始化")
        except Exception as e:
            self._log("ERROR", f"加载质量数据失败: {e}")
    # ─────────────────────────────────────────────
    # 基于聊天记录的自进化（任务85新增）
    # ─────────────────────────────────────────────

    # 自进化结果文件路径
    _EVOLVE_RESULT_FILE = Path(__file__).parent.parent / "data" / "evolve_result.json"

    # 不合理对话模式分类
    PATTERN_HR_REJECTED_BUT_GREET = "A_hr_rejected_but_greet"  # HR已拒绝但bot还发打招呼语
    PATTERN_DUPLICATE_SELF_INTRO = "B_duplicate_self_intro"  # 同一聊天重复发自我介绍
    PATTERN_REJECTED_BUT_SEND_RESUME = "C_rejected_but_send_resume"  # HR拒绝后bot还要求发简历
    PATTERN_DUPLICATE_MESSAGES = "D_duplicate_messages"  # 连发多条相同消息
    PATTERN_IRRELEVANT_REPLY = "E_irrelevant_reply"  # 回复不切题

    # 自我介绍特征关键词（与 reply_engine.py 保持一致）
    _SELF_INTRO_MARKERS = [
        "您好，我是", "您好！我是", "我是双一流", "我是本科", "我是硕士",
        "我对这个岗位很感兴趣", "我对这个职位很感兴趣",
        "希望可以进一步沟通", "希望能获得面试机会", "希望能有机会",
        "应聘数据分析", "看到您的招聘信息",
    ]

    # HR 拒绝关键词
    _REJECTION_MARKERS = [
        "不好意思", "不太合适", "不匹配", "不完全吻合", "不完全匹配",
        "不太合适哦", "暂时不太合适", "感谢您的关注", "祝您找到",
        "不太适合", "不合适", "另有安排", "暂不考虑",
    ]

    # 简历相关回复特征
    _RESUME_REPLY_MARKERS = [
        "简历文件暂时不在我这边", "简历稍后发", "稍后我补发简历",
        "我发一份简历", "发送简历", "简历已发送",
    ]

    def _is_rejection_msg(self, text: str) -> bool:
        """检测消息是否是 HR 拒绝。"""
        if not text:
            return False
        for marker in self._REJECTION_MARKERS:
            if marker in text:
                return True
        return False

    def _is_self_intro_msg(self, text: str) -> bool:
        """检测消息是否是自我介绍。"""
        if not text:
            return False
        for marker in self._SELF_INTRO_MARKERS:
            if marker in text:
                return True
        return False

    def _is_resume_reply_msg(self, text: str) -> bool:
        """检测消息是否是简历相关回复。"""
        if not text:
            return False
        for marker in self._RESUME_REPLY_MARKERS:
            if marker in text:
                return True
        return False

    def _load_all_chat_records(self) -> List[Dict[str, Any]]:
        """加载 messages/ 目录下所有聊天记录。

        Returns:
            聊天记录列表，每项包含 chat_name, job_name, messages
        """
        records = []
        try:
            messages_dir = Path(__file__).parent.parent / "messages"
            if not messages_dir.exists():
                self._log("WARN", f"聊天记录目录不存在: {messages_dir}")
                return records
            for path in messages_dir.glob("*.json"):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    records.append({
                        "chat_name": data.get("chat_name", path.stem),
                        "job_name": data.get("job_name", ""),
                        "messages": data.get("messages", []),
                    })
                except Exception as e:
                    self._log("DEBUG", f"加载聊天记录失败 {path.name}: {e}")
        except Exception as e:
            self._log("ERROR", f"加载聊天记录目录失败: {e}")
        return records

    def analyze_chat_patterns(self) -> Dict[str, Any]:
        """分析所有聊天记录，识别不合理对话模式。

        归纳为以下几类问题：
        A. HR已拒绝但bot还发打招呼语
        B. 同一聊天重复发自我介绍
        C. HR拒绝后bot还要求发简历
        D. 连发多条相同消息
        E. 回复不切题

        Returns:
            分析结果字典，包含各类问题的实例列表和统计
        """
        self._log("INFO", "开始分析聊天记录中的不合理对话模式...")

        records = self._load_all_chat_records()
        total_chats = len(records)
        self._log("INFO", f"共加载 {total_chats} 个聊天记录")

        # 各类问题实例
        issues = {
            self.PATTERN_HR_REJECTED_BUT_GREET: [],
            self.PATTERN_DUPLICATE_SELF_INTRO: [],
            self.PATTERN_REJECTED_BUT_SEND_RESUME: [],
            self.PATTERN_DUPLICATE_MESSAGES: [],
            self.PATTERN_IRRELEVANT_REPLY: [],
        }

        for record in records:
            chat_name = record.get("chat_name", "")
            job_name = record.get("job_name", "")
            messages = record.get("messages", [])

            if not messages:
                continue

            # 提取 HR 消息和 bot 消息（保持顺序）
            hr_messages = []  # [(index, text)]
            bot_messages = []  # [(index, text)]
            for i, msg in enumerate(messages):
                text = (msg.get("text") or msg.get("content") or "").strip()
                if not text:
                    continue
                if msg.get("is_mine"):
                    bot_messages.append((i, text))
                else:
                    hr_messages.append((i, text))

            # ── A. HR已拒绝但bot还发打招呼语 ──
            # 检测：HR 发了拒绝消息后，bot 又发了打招呼语/自我介绍
            rejection_indices = []
            for idx, text in hr_messages:
                if self._is_rejection_msg(text):
                    rejection_indices.append(idx)

            if rejection_indices:
                for rej_idx in rejection_indices:
                    # 找到拒绝后 bot 发的打招呼语
                    for bot_idx, bot_text in bot_messages:
                        if bot_idx > rej_idx and self._is_self_intro_msg(bot_text):
                            issues[self.PATTERN_HR_REJECTED_BUT_GREET].append({
                                "chat_name": chat_name,
                                "job_name": job_name,
                                "hr_rejection": next(
                                    (t for i, t in hr_messages if i == rej_idx), ""),
                                "bot_greeting": bot_text[:80],
                                "detail": f"HR拒绝后bot还发打招呼语",
                            })
                            break

            # ── B. 同一聊天重复发自我介绍 ──
            self_intro_count = sum(
                1 for _, text in bot_messages if self._is_self_intro_msg(text)
            )
            if self_intro_count >= 2:
                issues[self.PATTERN_DUPLICATE_SELF_INTRO].append({
                    "chat_name": chat_name,
                    "job_name": job_name,
                    "self_intro_count": self_intro_count,
                    "detail": f"重复发送自我介绍 {self_intro_count} 次",
                })

            # ── C. HR拒绝后bot还要求发简历 ──
            if rejection_indices:
                for rej_idx in rejection_indices:
                    for bot_idx, bot_text in bot_messages:
                        if bot_idx > rej_idx and self._is_resume_reply_msg(bot_text):
                            issues[self.PATTERN_REJECTED_BUT_SEND_RESUME].append({
                                "chat_name": chat_name,
                                "job_name": job_name,
                                "hr_rejection": next(
                                    (t for i, t in hr_messages if i == rej_idx), ""),
                                "bot_resume_reply": bot_text[:80],
                                "detail": f"HR拒绝后bot还回复简历相关内容",
                            })
                            break

            # ── D. 连发多条相同消息 ──
            if len(bot_messages) >= 2:
                for i in range(1, len(bot_messages)):
                    prev_text = bot_messages[i - 1][1]
                    curr_text = bot_messages[i][1]
                    if prev_text == curr_text and len(curr_text) > 5:
                        issues[self.PATTERN_DUPLICATE_MESSAGES].append({
                            "chat_name": chat_name,
                            "job_name": job_name,
                            "duplicate_text": curr_text[:80],
                            "detail": f"连续发送相同消息",
                        })

            # ── E. 回复不切题（简单检测：HR问岗位内容bot回薪资等）──
            # 这里只做简单检测，详细分析交给 AI
            for hr_idx, hr_text in hr_messages:
                # HR 问岗位内容
                if any(kw in hr_text for kw in ["工作内容", "岗位职责", "做什么的", "具体做什么"]):
                    # 找到 HR 消息后 bot 的回复
                    for bot_idx, bot_text in bot_messages:
                        if bot_idx > hr_idx:
                            # bot 回复薪资相关（不切题）
                            if any(kw in bot_text for kw in ["期望薪资", "薪资", "面议"]):
                                issues[self.PATTERN_IRRELEVANT_REPLY].append({
                                    "chat_name": chat_name,
                                    "job_name": job_name,
                                    "hr_question": hr_text[:80],
                                    "bot_reply": bot_text[:80],
                                    "detail": f"HR问岗位内容但bot回薪资",
                                })
                            break

        # 统计摘要
        summary = {
            "total_chats_analyzed": total_chats,
            "issues_by_type": {
                k: len(v) for k, v in issues.items()
            },
            "total_issues": sum(len(v) for v in issues.values()),
        }

        self._log("INFO",
                  f"聊天记录分析完成: {total_chats} 个聊天, "
                  f"发现 {summary['total_issues']} 个问题: "
                  f"A(HR拒绝但发打招呼)={summary['issues_by_type'][self.PATTERN_HR_REJECTED_BUT_GREET]}, "
                  f"B(重复自我介绍)={summary['issues_by_type'][self.PATTERN_DUPLICATE_SELF_INTRO]}, "
                  f"C(拒绝后发简历)={summary['issues_by_type'][self.PATTERN_REJECTED_BUT_SEND_RESUME]}, "
                  f"D(重复消息)={summary['issues_by_type'][self.PATTERN_DUPLICATE_MESSAGES]}, "
                  f"E(不切题)={summary['issues_by_type'][self.PATTERN_IRRELEVANT_REPLY]}")

        return {
            "summary": summary,
            "issues": issues,
            "timestamp": datetime.now().isoformat(),
        }

    def generate_rule_adjustments_from_patterns(self, analysis: dict) -> List[Dict[str, Any]]:
        """根据聊天记录分析结果生成规则调整建议。

        Args:
            analysis: analyze_chat_patterns() 的返回结果

        Returns:
            规则调整建议列表
        """
        adjustments = []
        issues = analysis.get("issues", {})
        timestamp = datetime.now().isoformat()

        # A. HR已拒绝但bot还发打招呼语 → 添加拒绝检测规则
        if issues.get(self.PATTERN_HR_REJECTED_BUT_GREET):
            adjustments.append({
                "rule_key": "rejection_detection",
                "old_value": "无拒绝检测",
                "new_value": "检测到HR拒绝时礼貌接受，不再发打招呼语",
                "reason": f"发现 {len(issues[self.PATTERN_HR_REJECTED_BUT_GREET])} 例"
                          f"HR拒绝后bot仍发打招呼语，已添加拒绝检测逻辑",
                "auto_applied": True,
                "timestamp": timestamp,
            })

        # B. 重复发自我介绍 → 添加自我介绍去重规则
        if issues.get(self.PATTERN_DUPLICATE_SELF_INTRO):
            adjustments.append({
                "rule_key": "self_intro_dedup",
                "old_value": "无自我介绍去重",
                "new_value": "检测到已发过自我介绍时不再重复发送",
                "reason": f"发现 {len(issues[self.PATTERN_DUPLICATE_SELF_INTRO])} 例"
                          f"重复发送自我介绍，已添加去重逻辑",
                "auto_applied": True,
                "timestamp": timestamp,
            })

        # C. HR拒绝后还发简历 → 添加拒绝后不发简历规则
        if issues.get(self.PATTERN_REJECTED_BUT_SEND_RESUME):
            adjustments.append({
                "rule_key": "no_resume_after_rejection",
                "old_value": "无拒绝后简历检测",
                "new_value": "HR拒绝后不再发送简历相关内容",
                "reason": f"发现 {len(issues[self.PATTERN_REJECTED_BUT_SEND_RESUME])} 例"
                          f"HR拒绝后bot仍回复简历相关内容，已添加拒绝后不发简历逻辑",
                "auto_applied": True,
                "timestamp": timestamp,
            })

        # D. 连发多条相同消息 → 添加重复消息检测规则
        if issues.get(self.PATTERN_DUPLICATE_MESSAGES):
            adjustments.append({
                "rule_key": "duplicate_message_detection",
                "old_value": "无重复消息检测",
                "new_value": "检测到即将发送的消息与历史重复时跳过",
                "reason": f"发现 {len(issues[self.PATTERN_DUPLICATE_MESSAGES])} 例"
                          f"连续发送相同消息，已添加重复消息检测逻辑",
                "auto_applied": True,
                "timestamp": timestamp,
            })

        # E. 回复不切题 → 添加切题检测规则
        if issues.get(self.PATTERN_IRRELEVANT_REPLY):
            adjustments.append({
                "rule_key": "relevance_check",
                "old_value": "无切题检测",
                "new_value": "回复前检查是否切题，避免答非所问",
                "reason": f"发现 {len(issues[self.PATTERN_IRRELEVANT_REPLY])} 例"
                          f"回复不切题，已在SYSTEM_PROMPT中添加切题要求",
                "auto_applied": True,
                "timestamp": timestamp,
            })

        return adjustments

    def generate_template_optimizations_from_patterns(self, analysis: dict) -> List[Dict[str, Any]]:
        """根据聊天记录分析结果生成模板优化建议。

        Args:
            analysis: analyze_chat_patterns() 的返回结果

        Returns:
            模板优化建议列表
        """
        optimizations = []
        issues = analysis.get("issues", {})
        timestamp = datetime.now().isoformat()

        # 如果存在 HR 拒绝但 bot 还发打招呼的问题，优化打招呼模板
        if issues.get(self.PATTERN_HR_REJECTED_BUT_GREET):
            optimizations.append({
                "template_key": "greeting_reply",
                "current_template": "您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。",
                "suggested_template": "您好，看到您的招聘信息，对这个岗位很感兴趣，希望可以进一步沟通~",
                "reason": "优化打招呼语气，增加口语化表达，更像真人聊天",
                "timestamp": timestamp,
                "applied": False,
            })

        # 如果存在重复自我介绍问题，建议精简自我介绍
        if issues.get(self.PATTERN_DUPLICATE_SELF_INTRO):
            optimizations.append({
                "template_key": "self_intro",
                "current_template": "您好，我是双一流的本科，应聘数据分析岗位...",
                "suggested_template": "您好，我对这个岗位很感兴趣，希望可以进一步沟通~",
                "reason": "精简自我介绍，避免长篇大论，增加自然度",
                "timestamp": timestamp,
                "applied": False,
            })

        # 如果存在拒绝后发简历问题，建议添加拒绝后的礼貌回复模板
        if issues.get(self.PATTERN_REJECTED_BUT_SEND_RESUME):
            optimizations.append({
                "template_key": "rejection_reply",
                "current_template": "简历文件暂时不在我这边",
                "suggested_template": "好的，感谢您的时间，祝您招聘顺利~",
                "reason": "HR拒绝后应礼貌接受，而非继续推简历",
                "timestamp": timestamp,
                "applied": True,
            })

        return optimizations

    def run_chat_based_evolution(self) -> Dict[str, Any]:
        """基于聊天记录的自进化主入口。

        流程：
        1. 分析所有聊天记录，识别不合理对话模式
        2. 根据分析结果生成规则调整建议
        3. 根据分析结果生成模板优化建议
        4. 将分析结果保存到 data/evolve_result.json
        5. 返回完整的自进化结果

        Returns:
            自进化结果字典
        """
        if not self.enabled:
            return {"status": "disabled", "message": "自进化功能未启用"}

        self._log("INFO", "=" * 50)
        self._log("INFO", "开始基于聊天记录的自进化")
        self._log("INFO", "=" * 50)

        timestamp = datetime.now().isoformat()

        try:
            # 1. 分析聊天记录
            self._log("INFO", "[自进化] 步骤 1/4: 分析聊天记录中的不合理对话模式")
            analysis = self.analyze_chat_patterns()

            # 2. 生成规则调整建议
            self._log("INFO", "[自进化] 步骤 2/4: 生成规则调整建议")
            rule_adjustments = self.generate_rule_adjustments_from_patterns(analysis)

            # 3. 生成模板优化建议
            self._log("INFO", "[自进化] 步骤 3/4: 生成模板优化建议")
            template_optimizations = self.generate_template_optimizations_from_patterns(analysis)

            # 4. 保存结果到 data/evolve_result.json
            self._log("INFO", "[自进化] 步骤 4/4: 保存分析结果到 data/evolve_result.json")
            result = {
                "analysis": analysis,
                "rule_adjustments": rule_adjustments,
                "template_optimizations": template_optimizations,
                "timestamp": timestamp,
                "summary": {
                    "total_chats": analysis.get("summary", {}).get("total_chats_analyzed", 0),
                    "total_issues": analysis.get("summary", {}).get("total_issues", 0),
                    "rule_adjustments_count": len(rule_adjustments),
                    "template_optimizations_count": len(template_optimizations),
                },
            }

            try:
                self._EVOLVE_RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(self._EVOLVE_RESULT_FILE, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                self._log("INFO", f"自进化结果已保存到: {self._EVOLVE_RESULT_FILE}")
            except Exception as e:
                self._log("ERROR", f"保存自进化结果失败: {e}")

            # 同时更新内部的规则调整和模板优化记录
            with self._lock:
                self._rule_adjustments.extend(rule_adjustments)
                if len(self._rule_adjustments) > 200:
                    self._rule_adjustments = self._rule_adjustments[-200:]
                self._template_optimizations.extend(template_optimizations)
                if len(self._template_optimizations) > 100:
                    self._template_optimizations = self._template_optimizations[-100:]
                self._save_quality_data()

            self._log("INFO", "=" * 50)
            self._log("INFO", "基于聊天记录的自进化完成")
            self._log("INFO",
                      f"分析 {result['summary']['total_chats']} 个聊天, "
                      f"发现 {result['summary']['total_issues']} 个问题, "
                      f"生成 {len(rule_adjustments)} 条规则调整, "
                      f"{len(template_optimizations)} 条模板优化")
            self._log("INFO", "=" * 50)

            return result

        except Exception as e:
            self._log("ERROR", f"基于聊天记录的自进化失败: {e}")
            import traceback
            self._log("ERROR", traceback.format_exc())
            return {
                "status": "error",
                "message": str(e),
                "timestamp": timestamp,
            }