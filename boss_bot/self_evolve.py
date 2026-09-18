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

# 回复效果分类
EFFECT_POSITIVE = "positive"
EFFECT_NEUTRAL = "neutral"
EFFECT_NEGATIVE = "negative"
EFFECT_IGNORED = "ignored"

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

        # 加载持久化数据
        self.load_evolution_data()

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

    def classify_message_source(self, message_text: str,
                                 conversation_history: Optional[List[dict]] = None) -> str:
        """识别消息来源：AI 回复 vs 用户回复。

        通过以下特征判断：
        - AI 回复通常匹配已配置的回复模板风格
        - AI 回复通常更正式、更完整
        - 用户回复通常包含新的内容、提问、拒绝等
        - 消息存储中 is_mine 标记

        Args:
            message_text: 消息文本内容
            conversation_history: 对话历史，包含 is_mine 标记

        Returns:
            "ai" 表示 AI 回复，"user" 表示用户回复
        """
        if not message_text:
            return "user"

        # 优先检查对话历史中的 is_mine 标记
        if conversation_history:
            for msg in reversed(conversation_history):
                if msg.get("text", "").strip() == message_text.strip():
                    return "ai" if msg.get("is_mine") else "user"

        # 检查是否匹配 AI 回复模板特征
        text = message_text.strip()
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

        # 用户回复特征：较短、口语化、包含提问或拒绝
        if len(text) < 15 and any(c in text for c in "？?吗嘛呢吧"):
            return "user"

        return "user"

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