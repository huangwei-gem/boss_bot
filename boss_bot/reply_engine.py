"""
BOSS 自动回复机器人 - 回复引擎

四级决策：
1. 关键词规则直通（REPLY_RULES）
2. 意图识别回复（intent.py，比关键词更精准）
3. AI 生成回复（带多轮对话历史，OpenAI 兼容 API，主备切换）
4. 兜底（AI 失败时可配置 skip / default）

返回 (动作类型, 回复内容, 元信息)：
- 动作类型: 'text' | 'resume' | 'none'
- 元信息: {"source": rule/intent/ai/default, "intent": ..., "important": bool}

集成自进化引擎（SelfEvolveEngine）：
- 每次发送 AI 回复后，记录回复内容和上下文
- 每次收到 HR 新消息时，评估之前 AI 回复的效果
- 定期自动优化回复策略
"""

import random
import time
import logging
from typing import Optional, Tuple

import boss_bot.config as config
from boss_bot.config import (
    MIN_DELAY, MAX_DELAY,
    SALARY_REPLY, INTERVIEW_TIME_REPLY, JOB_CONTENT_REPLY,
    GREETING_REPLY,
    USER_PROFILE, render_template,
)
from boss_bot.rules import RuleEngine
from boss_bot.intent import classify
from boss_bot.prompts import SYSTEM_PROMPT, build_user_prompt
from boss_bot.reply_record import ReplyRecord, ReplyRecordStore

logger = logging.getLogger(__name__)

# 意图 -> 动作/话术模板（从个人画像渲染占位符）
INTENT_REPLIES = {
    "ask_salary": ("text", SALARY_REPLY),
    "ask_resume": ("resume", None),
    "ask_interview": ("text", INTERVIEW_TIME_REPLY),
    "invite_interview": ("text", INTERVIEW_TIME_REPLY),
    "ask_job_content": ("text", JOB_CONTENT_REPLY),
    "greeting": ("text", GREETING_REPLY),
    "contact_request": ("text", "方便的话您可以直接在平台上和我沟通，看到消息我会尽快回复您~"),
    "tell_salary": ("text", "感谢您的报价，我这边综合考虑一下，有进一步消息会及时回复您。"),
}


class ReplyCache:
    """AI 回复缓存 — 相同消息内容不重复调用 API"""

    def __init__(self, max_size: int = 200, ttl: int = 3600):
        self._cache = {}
        self._max_size = max_size
        self._ttl = ttl

    def _key(self, message: str, boss_name: str, job_name: str) -> str:
        return f"{message[:100]}|{boss_name}|{job_name}"

    def get(self, message: str, boss_name: str, job_name: str) -> Optional[str]:
        key = self._key(message, boss_name, job_name)
        entry = self._cache.get(key)
        if entry and time.time() - entry[1] < self._ttl:
            logger.debug(f"[缓存命中] key={key[:30]}")
            return entry[0]
        return None

    def set(self, message: str, boss_name: str, job_name: str, reply: str):
        if len(self._cache) >= self._max_size:
            oldest = min(self._cache.items(), key=lambda x: x[1][1])
            del self._cache[oldest[0]]
        key = self._key(message, boss_name, job_name)
        self._cache[key] = (reply, time.time())


class ReplyEngine:
    """回复引擎：规则 + 意图 + AI 混合模式

    可选集成 SelfEvolveEngine 实现自进化功能：
    - 记录每次 AI 回复的内容和上下文
    - 收到新消息时评估之前回复的效果
    - 定期自动优化回复策略
    """

    def __init__(self, self_evolve: Optional["SelfEvolveEngine"] = None,
                 account_name: str = "", account_index: int = 0):
        self.rule_engine = RuleEngine()
        self._reply_count = 0
        self._hour_start = time.time()
        self._cache = ReplyCache()
        self._self_evolve = self_evolve
        self._record_store = ReplyRecordStore()
        self._account_name = account_name
        self._account_index = account_index
        # AI 调用元信息（每次 _ask_ai 前重置，_call_chat 中写入）
        self._last_ai_system_prompt: Optional[str] = None
        self._last_ai_user_prompt: Optional[str] = None
        self._last_ai_model: str = ""
        self._last_ai_raw_response: Optional[str] = None

    # ---------- 决策入口 ----------

    def get_reply(self, messages, boss_name: str = "", job_name: str = "",
                  chat_name: str = "") -> Tuple[str, Optional[str], dict]:
        """
        根据消息决定回复。
        Args:
            messages: 消息列表 [{"text","is_mine"/"isFriend","time"}]，
                      也兼容直接传最新消息字符串
        Returns:
            (动作类型, 回复内容, 元信息)
        """
        latest, history = self._split_messages(messages)
        decision_start = time.time()

        meta = {"source": "", "intent": "", "important": False}

        if latest:
            meta["intent"] = classify(latest)

        # 收到新消息时，评估之前 AI 回复的效果（自进化）
        if self._self_evolve and self._self_evolve.enabled and history:
            try:
                self._self_evolve.evaluate_previous_replies(history, chat_name=chat_name)
            except Exception as e:
                logger.debug(f"[自进化] 评估历史回复异常: {e}")

        # 1. 关键词规则直通（最高优先级）
        if latest:
            result = self.rule_engine.match(latest)
            if result:
                action, content = result
                logger.info(f"[规则匹配] 命中规则 -> 动作={action}")
                meta["source"] = "rule"
                self._log_decision(chat_name, latest, meta, action, decision_start)
                self._record_to_evolve(action, content, meta, chat_name, boss_name, job_name)
                self._add_record(
                    chat_name=chat_name, job_name=job_name, received_message=latest,
                    reply_content=content, reply_source="rule", reply_intent=meta["intent"],
                    reply_reason=f"关键词规则匹配命中，动作={action}",
                )
                return action, content, meta

        # 2. 意图识别回复
        if meta["intent"] in INTENT_REPLIES:
            action, template = INTENT_REPLIES[meta["intent"]]
            content = render_template(template, USER_PROFILE) if template else None
            logger.info(f"[意图匹配] intent={meta['intent']} -> 动作={action}")
            meta["source"] = "intent"
            self._log_decision(chat_name, latest, meta, action, decision_start)
            self._record_to_evolve(action, content, meta, chat_name, boss_name, job_name)
            self._add_record(
                chat_name=chat_name, job_name=job_name, received_message=latest,
                reply_content=content, reply_source="intent", reply_intent=meta["intent"],
                reply_reason=f"意图识别匹配: {meta['intent']}，动作={action}",
            )
            return action, content, meta

        # 3. AI 生成回复（带多轮历史）
        if config.ENABLE_AI and any(config.AI_API_KEYS):
            logger.info("[AI回复] 规则/意图未命中，调用 AI 生成回复...")
            ai_reply = self._ask_ai(latest, boss_name, job_name, history)
            if ai_reply:
                meta["source"] = "ai"
                self._log_decision(chat_name, latest, meta, "text", decision_start)
                self._record_to_evolve("text", ai_reply, meta, chat_name, boss_name, job_name)
                self._add_record(
                    chat_name=chat_name, job_name=job_name, received_message=latest,
                    reply_content=ai_reply, reply_source="ai", reply_intent=meta["intent"],
                    reply_reason="规则和意图均未命中，使用AI生成回复",
                    system_prompt=self._last_ai_system_prompt,
                    user_prompt=self._last_ai_user_prompt,
                    ai_model=self._last_ai_model,
                    ai_raw_response=self._last_ai_raw_response,
                )
                return ("text", ai_reply, meta)
            logger.warning("[AI回复] 主备 API 均失败")

        # 4. 兜底
        if config.AI_FAIL_ACTION == "default":
            logger.info("[默认回复] 使用兜底话术")
            meta["source"] = "default"
            self._log_decision(chat_name, latest, meta, "text", decision_start)
            self._record_to_evolve("text", config.DEFAULT_REPLY, meta, chat_name, boss_name, job_name)
            self._add_record(
                chat_name=chat_name, job_name=job_name, received_message=latest,
                reply_content=config.DEFAULT_REPLY, reply_source="default",
                reply_intent=meta["intent"],
                reply_reason="AI调用失败(AI_FAIL_ACTION=default)，使用兜底话术",
            )
            return ("text", config.DEFAULT_REPLY, meta)

        # skip：宁可不回复，不发驴唇不对马嘴的话
        logger.info("[跳过回复] 无规则/意图命中且 AI 未响应，跳过")
        meta["source"] = "default"
        self._log_decision(chat_name, latest, meta, "none", decision_start)
        self._add_record(
            chat_name=chat_name, job_name=job_name, received_message=latest,
            reply_content=None, reply_source="skip", reply_intent=meta["intent"],
            reply_reason="无规则/意图命中且AI未响应或未启用，跳过回复",
            is_skipped=True, skip_reason="无规则/意图命中且AI未响应或未启用",
        )
        return ("none", None, meta)

    def _record_to_evolve(self, action: str, content: Optional[str], meta: dict,
                          chat_name: str, boss_name: str, job_name: str):
        """将回复记录到自进化引擎（如果已集成且启用）。"""
        if not self._self_evolve or not self._self_evolve.enabled:
            return
        if action == "none" or not content:
            return
        try:
            self._self_evolve.record_ai_reply(content, {
                "chat_name": chat_name,
                "job_name": job_name,
                "boss_name": boss_name,
                "source": meta.get("source", ""),
                "intent": meta.get("intent", ""),
                "action": action,
            })
        except Exception as e:
            logger.debug(f"[自进化] 记录回复异常: {e}")

    def _add_record(self, **kwargs):
        """创建并保存一条 ReplyRecord（异常不影响主流程）。"""
        try:
            # 自动填充账号信息（如果调用方未指定）
            kwargs.setdefault("account_name", self._account_name)
            kwargs.setdefault("account_index", self._account_index)
            # 关键字段缺失警告（帮助定位调用方未传字段的问题）
            if not kwargs.get("chat_name"):
                logger.debug(f"[回复记录] chat_name 为空，received_message={kwargs.get('received_message', '')[:50]}")
            if not kwargs.get("received_message") and not kwargs.get("is_skipped"):
                logger.debug(f"[回复记录] received_message 为空且未跳过，chat_name={kwargs.get('chat_name', '')}")
            record = ReplyRecord(**kwargs)
            self._record_store.add(record)
        except Exception as e:
            logger.debug(f"[回复记录] 保存记录异常: {e}")

    @staticmethod
    def _log_decision(chat_name: str, message: str, meta: dict,
                      action: str, start_time: float):
        """记录回复决策事件（结构化日志，方便事后分析）"""
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"[reply] chat={chat_name} message={message[:120]} "
            f"intent={meta.get('intent', '')} source={meta.get('source', '')} "
            f"action={action} duration_ms={duration_ms}"
        )

    @staticmethod
    def _split_messages(messages):
        """拆出最新对方消息和历史列表，兼容直接传字符串"""
        if isinstance(messages, str):
            return messages, []
        if not messages:
            return "", []
        latest = ""
        for msg in reversed(messages):
            if not msg.get("is_mine"):
                latest = (msg.get("text") or "").strip()
                break
        return latest, messages

    # ---------- AI ----------

    @staticmethod
    def _is_rate_limit_error(e: Exception) -> bool:
        """判断是否为限流错误（429 / rate limit / quota）"""
        text = str(e).lower().replace("_", " ")
        return "429" in text or "rate limit" in text or "insufficient quota" in text or "tpm/rpm" in text

    def _call_with_rate_limit_retry(self, client, model, message,
                                    boss_name, job_name, history, api_name: str):
        """调用 AI，429 限流时等待后重试一次；重试仍失败返回 None（由调用方切换备用 API）"""
        for attempt in range(2):
            try:
                start = time.time()
                reply = self._call_chat(client, model, message, boss_name, job_name, history)
                latency_ms = int((time.time() - start) * 1000)
                logger.info(f"[ai_call] api={api_name} ok=True latency_ms={latency_ms}")
                return reply
            except Exception as e:
                if attempt == 0 and self._is_rate_limit_error(e):
                    wait = config.AI_RATE_LIMIT_WAIT
                    logger.warning(f"[{api_name}] API 限流(429)，等待 {wait} 秒后重试...")
                    logger.info(f"[ai_call] api={api_name} ok=False error=rate_limit wait={wait}")
                    time.sleep(wait)
                    continue
                # 最终失败：记录日志，返回 None 交给调用方切换备用 API
                logger.error(f"[{api_name}] API 调用最终失败: {e}")
                logger.info(f"[ai_call] api={api_name} ok=False error={str(e)[:200]}")
                return None
        return None

    def _ask_ai(self, message: str, boss_name: str, job_name: str,
                history: list = None) -> Optional[str]:
        """调用 AI API 生成回复（OpenAI 兼容格式），多模型自动切换

        策略：
        1. 缓存命中时直接返回
        2. 从模型池中随机打乱顺序，逐个尝试
        3. 429 限流时等待后重试一次
        4. 全部失败时返回 None
        """
        # 重置 AI 元信息（供 get_reply 中记录使用）
        self._last_ai_system_prompt = None
        self._last_ai_user_prompt = None
        self._last_ai_model = ""
        self._last_ai_raw_response = None

        if message == "" and not history:
            return None

        # 缓存检查
        cached = self._cache.get(message, boss_name, job_name)
        if cached:
            logger.info(f"[缓存命中] 跳过 API 调用，直接返回缓存回复")
            return cached

        # 检查是否有配置模型
        providers = config.AI_PROVIDERS
        if not providers:
            logger.info("未配置任何 AI 模型")
            return None

        # 随机打乱模型顺序，实现负载均衡
        shuffled = list(providers)
        random.shuffle(shuffled)

        for i, provider in enumerate(shuffled):
            api_key = provider["key"]
            model = provider["model"]
            base_url = provider["url"]
            provider_name = f"{model}#{i+1}"

            try:
                from openai import OpenAI

                client = OpenAI(api_key=api_key, base_url=base_url)
                reply = self._call_with_rate_limit_retry(
                    client, model, message, boss_name, job_name, history, provider_name)
                if reply:
                    logger.info(f"[AI回复生成] {provider_name}: {reply[:50]}...")
                    self._cache.set(message, boss_name, job_name, reply)
                    return reply
            except ImportError:
                logger.warning("未安装 openai 库，无法使用 AI 回复。运行: pip install openai")
                return None
            except Exception as e:
                logger.warning(f"[{provider_name}] 调用失败: {str(e)[:100]}，尝试下一个模型...")
                logger.info(f"[ai_call] api={provider_name} ok=False error={str(e)[:200]}")
                continue

        # 全部模型都失败
        logger.error("所有 AI 模型均调用失败")
        return None

    def _call_chat(self, client, model, message, boss_name, job_name, history):
        user_prompt = build_user_prompt(boss_name, job_name, message, history)
        response = client.chat.completions.create(
            model=model,
            max_tokens=config.AI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw_content = response.choices[0].message.content
        # 保存 AI 元信息供回复记录使用
        self._last_ai_system_prompt = SYSTEM_PROMPT
        self._last_ai_user_prompt = user_prompt
        self._last_ai_model = model
        self._last_ai_raw_response = raw_content
        return raw_content.strip()

    # ---------- 频控与延迟 ----------

    def wait_human_delay(self):
        """模拟人类操作延迟"""
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        logger.debug(f"等待 {delay:.1f} 秒...")
        time.sleep(delay)

    def can_reply(self) -> bool:
        """检查是否超过每小时回复限制"""
        now = time.time()
        if now - self._hour_start > 3600:
            self._reply_count = 0
            self._hour_start = now
        return self._reply_count < config.MAX_REPLIES_PER_HOUR

    def record_reply(self):
        """记录一次回复"""
        self._reply_count += 1
