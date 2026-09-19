"""
自动打招呼/投递引擎

从 auto_boss 项目迁移，负责在 BOSS 直聘上自动搜索职位、
发送打招呼消息和投递简历。

核心功能：
- 岗位搜索（关键词 + 城市 + 滚动翻页）
- 岗位浏览和打招呼（自动点击沟通按钮、输入消息、发送）
- AI 匹配分析（多 AI 容灾链，岗位匹配度评分）
- Cookie 管理（保存/加载/清除）
- 反爬策略（随机间隔、User-Agent 轮换）
- 去重管理（持久化已投递记录）
- 重试与容错（指数退避重试装饰器）
- 城市编码映射（硬编码 + API 捕获）
"""

import json
import os
import random
import time
import threading
import hashlib
import logging
from functools import wraps
from typing import Optional, Callable
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

from boss_bot.unified_config import UnifiedConfig, BASE_DIR
from boss_bot.browser_launcher import BrowserManager
from boss_bot.reply_record import GreetRecordStore, GreetRecord

# ─────────────────────────────────────────────
# 路径常量
# ─────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHATS_LOG_FILE = DATA_DIR / "chats_log.json"
CHATTED_DB_FILE = DATA_DIR / "chatted_jobs.json"
CITY_DICT_FILE = DATA_DIR / "city_dict.json"
AI_CACHE_FILE = DATA_DIR / "ai_cache.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# 文件日志
# ─────────────────────────────────────────────
_file_handler = logging.FileHandler(str(LOG_DIR / "greet_engine.log"), encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
_file_logger = logging.getLogger("greet_engine_file")
_file_logger.setLevel(logging.INFO)
_file_logger.addHandler(_file_handler)
_file_logger.propagate = False

# ─────────────────────────────────────────────
# CSS 选择器常量
# ─────────────────────────────────────────────
SELECTOR_NAV = ".user-nav"
SELECTOR_START_CHAT_TEXT = "立即沟通"
SELECTOR_START_CHAT_CONTINUE = "继续沟通"
SELECTOR_INPUT_AREA = ".input-area"
SELECTOR_SEND_BTN = ".send-message"
SELECTOR_CLOSE = ".icon-close"
SELECTOR_BOSS_ACTIVE = ".boss-active-time"
SELECTOR_SCALE = ".icon-scale"
SELECTOR_REC_JOB_LIST = ".rec-job-list"
SELECTOR_JOB_NAME = ".job-name"

# ─────────────────────────────────────────────
# 风控检测关键词
# ─────────────────────────────────────────────
# 验证码/安全验证关键词（出现即视为风控触发）
WIND_CONTROL_CAPTCHA_KEYWORDS = (
    "安全验证", "滑动验证", "验证码", "请完成验证", "拖动滑块",
    "人机验证", "图形验证", "verifyCode", "captcha",
)
# 限制/频控关键词（出现即视为风控触发）
WIND_CONTROL_LIMIT_KEYWORDS = (
    "操作频繁", "稍后再试", "访问太频繁", "请求过于频繁",
    "已被限制", "暂时限制", "限制访问", "请稍候再试",
    "frequent", "too many",
)

# ─────────────────────────────────────────────
# 默认 User-Agent 列表
# ─────────────────────────────────────────────
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ─────────────────────────────────────────────
# 热门城市编码映射（Boss直聘使用）
# ─────────────────────────────────────────────
CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
    "南京": "101190100", "武汉": "101200100", "西安": "101110100",
    "重庆": "101040100", "长沙": "101250100", "苏州": "101190400",
    "天津": "101030100", "郑州": "101180100", "东莞": "101281600",
    "青岛": "101120200", "沈阳": "101070100", "宁波": "101210400",
    "昆明": "101290100", "大连": "101070200", "厦门": "101230200",
    "合肥": "101220100", "佛山": "101280300", "福州": "101230100",
    "哈尔滨": "101050100", "济南": "101120100", "温州": "101210700",
    "长春": "101060100", "石家庄": "101090100", "常州": "101191100",
    "泉州": "101230500", "南宁": "101300100", "贵阳": "101260100",
    "南昌": "101240100", "太原": "101100100", "烟台": "101120500",
    "嘉兴": "101210300", "南通": "101190500", "金华": "101210900",
    "珠海": "101280700", "惠州": "101280300", "徐州": "101190800",
    "海口": "101310100", "乌鲁木齐": "101130100", "绍兴": "101210500",
    "中山": "101281700", "台州": "101210600", "兰州": "101160100",
}


# ─────────────────────────────────────────────
# 重试装饰器
# ─────────────────────────────────────────────

def retry(max_attempts: int = 3, base_delay: float = 2.0, backoff_factor: float = 2.0):
    """重试装饰器：捕获 Exception，指数退避重试。"""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(self, *args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts:
                        delay = base_delay * (backoff_factor ** (attempt - 1)) + random.uniform(0, 1)
                        self._log("WARN", f"重试 {attempt}/{max_attempts}: {e}，等待 {delay:.1f}s")
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


# ─────────────────────────────────────────────
# AI 分析器（多 AI 容灾链）
# ─────────────────────────────────────────────

class AIProviderConfig:
    """单个 AI 接口配置。"""

    def __init__(self, name: str, api_key: str, api_base: str, model: str, timeout: int = 30):
        self.name = name
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_valid(self) -> bool:
        return bool(self.api_key and self.api_base and self.model)


class AIAnalyzerChain:
    """多 AI 容灾链：按顺序尝试多个 AI 接口，自动切换。"""

    _cache_lock = threading.Lock()

    def __init__(
        self,
        providers: list,
        match_threshold: int = 70,
        cache_enabled: bool = True,
        cache_ttl_hours: int = 24,
        log_callback: Optional[Callable] = None,
    ):
        self.providers = []
        for p in providers:
            if isinstance(p, dict):
                self.providers.append(AIProviderConfig(
                    name=p.get("name", "AI"),
                    api_key=p.get("api_key", p.get("key", "")),
                    api_base=p.get("api_base", p.get("url", "")),
                    model=p.get("model", ""),
                    timeout=p.get("timeout", 30),
                ))
            elif isinstance(p, AIProviderConfig):
                self.providers.append(p)

        self.match_threshold = match_threshold
        self.cache_enabled = cache_enabled
        self.cache_ttl = cache_ttl_hours * 3600
        self.log_cb = log_callback

        self.analyzed_count = 0
        self.match_count = 0
        self.cache_hit_count = 0
        self._resume = None
        self._resume_hash = ""

        # 追踪最后一次分析的信息（供 GreetRecord 记录使用）
        self.last_system_prompt = None
        self.last_user_prompt = None
        self.last_model_name = ""
        self.last_raw_response = None

    def _log(self, level: str, msg: str):
        if self.log_cb:
            self.log_cb(f"[AI] [{level}] {msg}")

    def set_resume(self, resume: dict):
        self._resume = resume
        self._resume_hash = hashlib.md5(
            json.dumps(resume, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def analyze_job(self, job: dict) -> dict:
        """分析单个岗位。依次尝试所有 provider，直到成功。"""
        if not self.providers:
            return {"score": 50, "is_match": True, "reason": "未配置 AI 接口", "suggested_greeting": ""}

        # 检查缓存
        if self.cache_enabled and self._resume_hash:
            cache_key = self._make_cache_key(job.get("url", ""), self._resume_hash)
            cache = self._load_cache()
            if cache_key in cache:
                self.cache_hit_count += 1
                self._log("INFO", f"缓存命中: {job.get('job_name', '')}")
                return cache[cache_key]["result"]

        # 依次尝试每个 provider
        prompt = self._build_prompt(job)
        # 保存 prompt 信息供 GreetRecord 记录使用
        self.last_system_prompt = prompt[0]["content"] if len(prompt) > 0 else None
        self.last_user_prompt = prompt[1]["content"] if len(prompt) > 1 else None
        last_error = None
        for provider in self.providers:
            if not provider.is_valid():
                self._log("WARN", f"AI 接口 '{provider.name}' 配置无效，跳过")
                continue
            try:
                self._log("INFO", f"通过 [{provider.name}] ({provider.model}) 分析...")
                result = self._call_provider_api(provider, prompt)
                self.last_model_name = provider.model
                self.analyzed_count += 1
                if result.get("is_match", False):
                    self.match_count += 1

                # 写入缓存
                if self.cache_enabled and self._resume_hash:
                    cache_key = self._make_cache_key(job.get("url", ""), self._resume_hash)
                    cache = self._load_cache()
                    cache[cache_key] = {
                        "result": result,
                        "cached_at": time.time(),
                        "_expires_at": time.time() + self.cache_ttl,
                    }
                    self._save_cache(cache)

                return result

            except Exception as e:
                last_error = e
                self._log("WARN", f"[{provider.name}] 失败: {e}，尝试下一个...")
                continue

        # 全部失败
        self._log("ERROR", f"所有 AI 接口均失败，最后错误: {last_error}")
        return {"score": 50, "is_match": True, "reason": f"AI 分析异常: {last_error}，默认通过", "suggested_greeting": ""}

    def _call_provider_api(self, provider: AIProviderConfig, messages: list) -> dict:
        """调用指定 AI 接口。"""
        url = f"{provider.api_base}/chat/completions"
        payload = json.dumps({
            "model": provider.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": True},
        }).encode("utf-8")

        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {provider.api_key}")

        try:
            with urlopen(req, timeout=provider.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except URLError as e:
            raise Exception(f"API 请求失败: {e}")
        except TimeoutError:
            raise Exception(f"请求超时（{provider.timeout}s）")

        try:
            message = data["choices"][0]["message"]
            content = message.get("content", "") or message.get("reasoning_content", "")
            self.last_raw_response = content
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(content[json_start:json_end])
                return result
            else:
                if content:
                    return {"score": 50, "is_match": True, "reason": content[:200]}
                raise ValueError("响应中未找到 JSON")
        except (KeyError, IndexError, json.JSONDecodeError, ValueError) as e:
            self._log("WARN", f"解析 AI 响应失败: {e}")
            return {"score": 50, "is_match": True, "reason": "解析失败，默认通过"}

    def _build_prompt(self, job: dict) -> list:
        """构建 AI 分析提示词。"""
        resume = self._resume or {}
        system_msg = (
            "你是 Boss直聘智能投递助手的岗位匹配分析专家。你的任务是分析招聘岗位与求职者简历的匹配程度，"
            "给出评分和详细理由。请按 JSON 格式返回结果。"
        )
        user_msg = (
            "【求职者简历】\n"
            f"教育背景：{resume.get('school', '')} "
            f"{resume.get('major', '')} "
            f"{resume.get('degree', '')}\n"
            f"技能：{', '.join(resume.get('skills', []))}\n"
            f"工作经验：{resume.get('experience', '')}\n"
            f"求职意向：{resume.get('target_position', '')}\n\n"
            "【招聘岗位】\n"
            f"岗位名称：{job.get('job_name', '')}\n"
            f"薪资：{job.get('salary', '')}\n"
            f"岗位描述：{job.get('description', '')}\n"
            f"任职要求：{job.get('requirements', '')}\n"
            f"公司：{job.get('company', '')}\n\n"
            "请分析匹配度，按以下 JSON 格式返回（不要包含其他内容）：\n"
            '{\n  "score": 0-100,\n  "is_match": true/false,\n'
            '  "reason": "匹配分析简要说明",\n'
            '  "strengths": ["优势1", "优势2"],\n'
            '  "weaknesses": ["劣势1", "劣势2"],\n'
            '  "suggested_greeting": "基于岗位要求生成的个性化打招呼消息"\n}'
        )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

    def _make_cache_key(self, job_url: str, resume_hash: str) -> str:
        raw = f"{job_url}:{resume_hash}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict:
        if not AI_CACHE_FILE.exists():
            return {}
        try:
            with open(AI_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                now = time.time()
                expired = [k for k, v in data.items() if v.get("_expires_at", 0) < now]
                for k in expired:
                    del data[k]
                return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self, cache: dict):
        AI_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with self._cache_lock:
            with open(AI_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)

    def clear_cache(self):
        if AI_CACHE_FILE.exists():
            AI_CACHE_FILE.unlink()

    def get_stats(self) -> dict:
        return {
            "analyzed": self.analyzed_count,
            "matched": self.match_count,
            "cache_hits": self.cache_hit_count,
            "match_rate": round(self.match_count / max(self.analyzed_count, 1) * 100, 1),
            "threshold": self.match_threshold,
            "providers_total": len(self.providers),
            "providers_valid": sum(1 for p in self.providers if p.is_valid()),
        }


# ─────────────────────────────────────────────
# GreetEngine — 自动打招呼/投递引擎
# ─────────────────────────────────────────────

class GreetEngine:
    """自动打招呼/投递引擎。

    使用 BrowserManager 管理浏览器，UnifiedConfig 提供配置，
    支持 AI 匹配分析、Cookie 管理、反爬策略和去重管理。

    使用方式：
        manager = BrowserManager(config)
        engine = GreetEngine(manager, unified_config, log_callback=print)
        engine.start(tasks=[{"query": "数据分析", "city": "上海"}])
    """

    def __init__(
        self,
        browser_manager: BrowserManager,
        config: UnifiedConfig,
        log_callback: Optional[Callable] = None,
        progress_callback: Optional[Callable] = None,
        greet_event_cb: Optional[Callable] = None,
        wind_control_cb: Optional[Callable] = None,
    ):
        self.browser_manager = browser_manager
        self.config = config
        self.log_cb = log_callback
        self.progress_cb = progress_callback
        self._greet_event_cb = greet_event_cb
        # 风控触发回调 — 触发时通知 main_loop 暂停打招呼并推送前端事件
        # 回调签名: wind_control_cb(message: str, wtype: str) -> None
        # wtype: "captcha"（验证码） | "limit"（限制提示）
        self._wind_control_cb = wind_control_cb

        # 运行状态
        self.running = False
        self._is_logged_in = False
        self._login_event = threading.Event()
        # 风控触发标志 — 触发后停止投递，等待用户手动处理
        self._wind_control_detected = False

        # 统计
        self.applied_count = 0
        self.skipped_count = 0
        self.total_jobs = 0

        # 城市字典（从 API 捕获）
        self._city_dict = {}

        # 当前任务参数
        self._query = ""
        self._city = "上海"
        self._scroll_pages = 5
        self._greeting_message = ""
        self._image_files = []
        self._min_interval = 3
        self._max_interval = 8
        self._cookie_file = "zhipin_cookies.json"
        self._login_required_cb = None

        # 岗位列表
        self.jobs = []

        # 投递队列锁（防止并发操作浏览器）
        self._apply_lock = threading.Lock()

        # AI 分析器
        self._ai_analyzer = None

        # 打招呼记录存储
        self._greet_store = GreetRecordStore()

        # 追踪最后一次 AI 分析的完整信息（供 GreetRecord 记录使用）
        self._last_ai_result = None
        self._last_ai_system_prompt = None
        self._last_ai_user_prompt = None
        self._last_ai_model = ""
        self._last_ai_raw_response = None

        # 从配置加载参数
        self._load_config_params()
        self._load_city_dict()

    def _load_config_params(self):
        """从 UnifiedConfig 读取所有可调参数。"""
        browser_cfg = self.config.browser
        self._headless = browser_cfg.headless
        self._viewport_width = browser_cfg.viewport_width
        self._viewport_height = browser_cfg.viewport_height
        self._page_load_timeout = browser_cfg.page_load_timeout
        self._custom_user_agent = browser_cfg.custom_user_agent
        self._proxy = browser_cfg.proxy
        self._browser_type = browser_cfg.browser_type

        login_cfg = self.config.login
        self._login_wait_timeout = login_cfg.wait_timeout
        self._clear_cookies_on_failure = login_cfg.clear_cookies_on_failure
        self._cookie_file = login_cfg.cookie_file

        rl_cfg = self.config.greet.rate_limit
        self._rate_limit_enabled = rl_cfg.enabled
        self._max_per_hour = rl_cfg.max_per_hour
        self._max_per_day = rl_cfg.max_per_day

        retry_cfg = self.config.greet.retry
        self._retry_max_attempts = retry_cfg.max_attempts
        self._retry_base_delay = retry_cfg.base_delay
        self._retry_backoff_factor = retry_cfg.backoff_factor

        ai_cfg = self.config.ai
        self._ai_enabled = ai_cfg.enabled
        self._ai_threshold = ai_cfg.match_threshold

        # AI providers 列表（从 UnifiedConfig 转换为 AIAnalyzerChain 所需格式）
        self._ai_providers = []
        for p in ai_cfg.providers:
            self._ai_providers.append({
                "name": p.name,
                "api_key": p.api_key,
                "api_base": p.api_base,
                "model": p.model,
                "timeout": p.timeout,
            })
        # 兼容旧格式
        if not self._ai_providers and ai_cfg.api_key:
            self._ai_providers.append({
                "name": "默认",
                "api_key": ai_cfg.api_key,
                "api_base": ai_cfg.api_base,
                "model": ai_cfg.model,
                "timeout": 30,
            })

        self._resume_cfg = {
            "school": self.config.resume.school,
            "major": self.config.resume.major,
            "degree": self.config.resume.degree,
            "skills": self.config.resume.skills,
            "experience": self.config.resume.experience,
            "target_position": self.config.resume.target_position,
            "self_intro": self.config.resume.self_intro,
        }

        # 从 greet.accounts 读取默认任务参数
        if self.config.greet.accounts:
            acc = self.config.greet.accounts[0]
            self._min_interval = acc.message_interval_min
            self._max_interval = acc.message_interval_max
            self._cookie_file = acc.cookie_file
            self._image_files = acc.image_files
            if acc.jobs:
                job = acc.jobs[0]
                self._query = job.query
                self._city = job.city
                self._scroll_pages = job.scroll_pages
                self._greeting_message = job.greeting_message
                self._image_files = job.image_files or self._image_files

    def _log(self, level: str, msg: str):
        """统一日志输出 — 回调 + 文件日志。"""
        if self.log_cb:
            self.log_cb(f"[{level}] {msg}")
        try:
            _file_logger.info(f"[{level}] {msg}")
        except Exception:
            pass

    def _emit_greet_event(self, job: dict, status: str, ai_result: dict = None):
        """推送投递事件到前端表格。

        Args:
            job: 岗位信息字典
            status: "success" | "skip" | "ai_skip" | "already" | "error"
            ai_result: AI 分析结果（可选）
        """
        if not self._greet_event_cb:
            return
        try:
            ai = ai_result or self._last_ai_result or {}
            self._greet_event_cb({
                "job_name": job.get("job_name", ""),
                "company": job.get("company", ""),
                "salary": job.get("salary", ""),
                "status": status,
                "ai_score": ai.get("score", 0),
                "ai_reason": ai.get("reason", ""),
                "ai_match": ai.get("is_match", False),
                "greeting": job.get("_actual_greeting_sent", "")[:60],
            })
        except Exception:
            pass

    def _record_greet(
        self,
        job: dict,
        is_greeted: bool = False,
        is_skipped: bool = False,
        skip_reason: str = "",
        actual_greeting_sent: str = "",
    ):
        """创建并保存一条打招呼/AI分析记录。

        从 self._last_ai_* 属性中获取 AI 分析的完整信息。
        """
        try:
            ai_result = self._last_ai_result or {}
            record = GreetRecord(
                job_name=job.get("job_name", ""),
                job_url=job.get("url", ""),
                company=job.get("company", job.get("company_location", "")),
                salary=job.get("salary", ""),
                job_description=job.get("jd_description", job.get("description", "")),
                job_requirements=job.get("jd_requirements", job.get("requirements", "")),
                ai_score=ai_result.get("score", 0),
                ai_is_match=ai_result.get("is_match", False),
                ai_reason=ai_result.get("reason", ""),
                ai_strengths=ai_result.get("strengths", []),
                ai_weaknesses=ai_result.get("weaknesses", []),
                ai_suggested_greeting=ai_result.get("suggested_greeting", ""),
                system_prompt=self._last_ai_system_prompt,
                user_prompt=self._last_ai_user_prompt,
                ai_model=self._last_ai_model,
                ai_raw_response=self._last_ai_raw_response,
                actual_greeting_sent=actual_greeting_sent,
                is_greeted=is_greeted,
                is_skipped=is_skipped,
                skip_reason=skip_reason,
                account_name=self._cookie_file or "",
            )
            self._greet_store.add(record)
        except Exception as e:
            self._log("WARN", f"记录打招呼信息失败: {e}")

    def _report_progress(self):
        if self.progress_cb:
            self.progress_cb({
                "applied": self.applied_count,
                "skipped": self.skipped_count,
                "total": self.total_jobs,
            })

    # ── 对外接口 ──

    def start(self, tasks=None):
        """启动打招呼任务。

        Args:
            tasks: 可选，多任务列表。每个任务为 dict，包含 query/city/scroll_pages/
                   greeting_message/image_files/message_interval_min/message_interval_max/
                   cookie_file 等字段。如果为 None，使用配置中的默认任务。
        """
        self.running = True
        try:
            self._run(tasks)
        except Exception as e:
            self._log("ERROR", f"引擎异常退出: {e}")
            import traceback
            self._log("ERROR", traceback.format_exc())
        finally:
            self.running = False

    def stop(self):
        """停止打招呼引擎。"""
        self.running = False

    def confirm_login(self) -> None:
        """确认登录完成（外部调用，通知引擎用户已手动登录）。"""
        self._login_event.set()
        # 确认登录后自动导航离开登录页
        instance = self.browser_manager.get_instance()
        if instance:
            try:
                instance.get("https://www.zhipin.com")
            except Exception:
                pass

    def check_login(self) -> bool:
        """检查登录状态。

        Returns:
            已登录返回 True，未登录返回 False。
        """
        instance = self.browser_manager.get_instance()
        if instance is None:
            return False
        try:
            for selector in (SELECTOR_NAV, ".header-login-btn", ".user-nav"):
                nav_ele = instance.ele(selector, timeout=3)
                if nav_ele:
                    text = nav_ele.text
                    if "登录/注册" not in text and text.strip():
                        self._is_logged_in = True
                        return True
            # 尝试检查 URL 是否包含登录页路径
            try:
                current_url = instance.url
                if current_url and "passport" not in current_url and "login" not in current_url:
                    return True
            except Exception:
                pass
            # 导航到首页检查
            instance.get("https://www.zhipin.com")
            self._random_delay(2, 5)
            for selector in (SELECTOR_NAV, ".header-login-btn", ".user-nav"):
                nav_ele = instance.ele(selector, timeout=3)
                if nav_ele:
                    text = nav_ele.text
                    if "登录/注册" not in text and text.strip():
                        self._is_logged_in = True
                        return True
            return False
        except Exception:
            return False

    def search_jobs(self, query: str, city: str, scroll_pages: int = 5) -> list:
        """搜索岗位并返回岗位列表。

        Args:
            query: 搜索关键词，如 "数据分析"
            city: 城市名称，如 "上海"
            scroll_pages: 滚动翻页次数

        Returns:
            岗位信息字典列表，每个包含 job_name/salary/experience/education/
            company_location/url/query 字段
        """
        self._query = query
        self._city = city
        self._scroll_pages = scroll_pages
        self._parse_job_list()
        return self.jobs

    def send_greeting(self, job_info: dict) -> bool:
        """对单个岗位发送打招呼消息。

        Args:
            job_info: 岗位信息字典，需包含 url 字段

        Returns:
            发送成功返回 True，失败返回 False
        """
        job_name = job_info.get('job_name', '')
        job_url = job_info.get('url', '')
        self._log("DEBUG", f"send_greeting 被调用: job={job_name}, url={job_url[:60]}, running={self.running}")
        
        if not self.running:
            self._log("WARN", f"投递跳过: running=False, job={job_name}")
            return False
        if not job_url:
            self._log("WARN", f"投递跳过: url为空, job={job_name}")
            return False
        try:
            success = self._apply_job(job_info)
            if success:
                self.applied_count += 1
                self._save_chat_log(job_info, skipped=False)
                self._log("SUCCESS", f"✅ 已投递: {job_name}")
                self._emit_greet_event(job_info, "success")
                self._record_greet(
                    job_info, is_greeted=True,
                    actual_greeting_sent=job_info.get("_actual_greeting_sent", ""),
                )
            else:
                self.skipped_count += 1
                self._log("WARN", f"⏭️ 跳过: {job_name}")
                self._emit_greet_event(job_info, "skip")
                self._record_greet(job_info, is_skipped=True, skip_reason="投递失败")
            self._report_progress()
            return success
        except Exception as e:
            self._log("WARN", f"发送打招呼异常: {e}")
            self.skipped_count += 1
            self._emit_greet_event(job_info, "error")
            self._report_progress()
            self._record_greet(job_info, is_skipped=True, skip_reason=f"发送异常: {e}")
            return False

    # ── 内部运行逻辑 ──

    def _run(self, tasks=None):
        """运行任务。如果传入 tasks，则为多任务模式（共用浏览器）。"""
        # 构建任务列表
        if tasks is not None:
            self._tasks = tasks
        else:
            # 从配置构建默认任务列表
            self._tasks = []
            for acc in self.config.greet.accounts:
                if not acc.enabled:
                    continue
                for job in acc.jobs:
                    if not job.enabled:
                        continue
                    self._tasks.append({
                        "query": job.query,
                        "city": job.city,
                        "scroll_pages": job.scroll_pages,
                        "greeting_message": job.greeting_message,
                        "image_files": job.image_files or acc.image_files,
                        "message_interval_min": acc.message_interval_min,
                        "message_interval_max": acc.message_interval_max,
                        "cookie_file": acc.cookie_file,
                    })
            if not self._tasks:
                self._tasks = [{
                    "query": self._query,
                    "city": self._city,
                    "scroll_pages": self._scroll_pages,
                    "greeting_message": self._greeting_message,
                    "image_files": self._image_files,
                    "message_interval_min": self._min_interval,
                    "message_interval_max": self._max_interval,
                    "cookie_file": self._cookie_file,
                }]

        # 启动浏览器
        if not self._init_browser():
            return

        # 检查登录
        if not self._check_and_handle_login():
            return

        # 逐任务执行
        for task_idx, task in enumerate(self._tasks):
            if not self.running:
                break

            self._log("INFO", f"━━━ 任务 [{task_idx+1}/{len(self._tasks)}] {task.get('query', '')} @ {task.get('city', '')} ━━━")

            self._query = task.get("query", "")
            self._city = task.get("city", "上海")
            self._scroll_pages = task.get("scroll_pages", 5)
            self._greeting_message = task.get("greeting_message", "")
            self._image_files = task.get("image_files", [])
            self._min_interval = task.get("message_interval_min", 3)
            self._max_interval = task.get("message_interval_max", 8)
            self._cookie_file = task.get("cookie_file", "zhipin_cookies.json")

            # AI 配置随任务刷新（从全局配置读取）
            self._ai_analyzer = None

            self._log("INFO", f"🔍 搜索: {self._city} · {self._query}")
            self._log("INFO", "正在获取岗位列表...")
            self._parse_job_list()

            if self.jobs:
                self._step_browse_jobs()
            else:
                self._log("WARN", "没有找到岗位，跳过此任务")

        self._log("INFO", "✅ 任务完成！")

    def _init_browser(self) -> bool:
        """初始化浏览器（通过 BrowserManager）。"""
        try:
            instance = self.browser_manager.get_instance()
            if instance is not None:
                # 检查连接是否还活着
                try:
                    _ = instance.url
                    return True
                except Exception:
                    self._log("WARN", "浏览器连接已断开，重新启动...")
                    self.browser_manager.close()

            self.browser_manager.launch()
            self._log("INFO", "浏览器已启动")
            return True
        except Exception as e:
            self._log("ERROR", f"浏览器启动失败: {e}")
            return False

    def _check_and_handle_login(self) -> bool:
        """检查登录状态，处理登录流程。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "浏览器未启动")
            return False

        try:
            instance.get("https://www.zhipin.com")
            self._random_delay(2, 3)

            nav_ele = instance.ele(SELECTOR_NAV, timeout=5)
            if nav_ele:
                nav_text = nav_ele.text
                if "登录/注册" in nav_text:
                    self._log("WARN", "需要登录")
                    if self._load_cookies():
                        instance.get("https://www.zhipin.com")
                        self._random_delay(2, 3)
                        nav_ele2 = instance.ele(SELECTOR_NAV, timeout=3)
                        if nav_ele2 and "登录/注册" not in nav_ele2.text:
                            self._is_logged_in = True
                            self._log("INFO", "Cookie 有效，已登录")
                            self._save_cookies()
                        else:
                            self._clear_cookies()
                            instance.get("https://www.zhipin.com/web/user/?ka=header-login")
                            self._random_delay(1, 2)
                            if self._login_required_cb:
                                self._login_required_cb()
                            self._log("INFO", "请手动登录，登录后点击「确认登录」")
                            if not self._wait_for_login():
                                self._log("ERROR", "登录超时")
                                return False
                            self._save_cookies()
                            self._log("SUCCESS", "登录成功")
                    else:
                        instance.get("https://www.zhipin.com/web/user/?ka=header-login")
                        self._random_delay(1, 2)
                        if self._login_required_cb:
                            self._login_required_cb()
                        self._log("INFO", "请手动登录，登录后点击「确认登录」")
                        if not self._wait_for_login():
                            self._log("ERROR", "登录超时")
                            return False
                        self._save_cookies()
                        self._log("SUCCESS", "登录成功")
                else:
                    self._is_logged_in = True
                    self._log("INFO", "已登录状态")
            else:
                self._log("WARN", "需要登录")
                instance.get("https://www.zhipin.com/web/user/?ka=header-login")
                self._random_delay(1, 2)
                if self._login_required_cb:
                    self._login_required_cb()
                self._log("INFO", "请手动登录，登录后点击「确认登录」")
                if not self._wait_for_login():
                    self._log("ERROR", "登录超时")
                    return False
                self._save_cookies()
                self._log("SUCCESS", "登录成功")

            self._log("INFO", "正在获取城市数据...")
            self._capture_city_data()
            return True
        except Exception as e:
            self._log("ERROR", "登录检查异常: " + str(e))
            import traceback
            self._log("ERROR", traceback.format_exc())
            return False

    def _capture_city_data(self):
        """捕获城市数据（监听 city.json 数据包）。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return
        try:
            instance.listen.start("data/city.json")
            instance.refresh()
            self._random_delay(2, 4)
            for packet in instance.listen.steps(timeout=10):
                res = packet.response.body
                if isinstance(res, dict) and "zpData" in res:
                    city_list = res["zpData"].get("hotCityList", [])
                    for city in city_list:
                        if isinstance(city, dict):
                            name = city.get("name", "")
                            code = city.get("code", "")
                            self._city_dict[name] = code
                            self._log("INFO", f"城市: {name} -> {code}")
                    if self._city_dict:
                        self._log("SUCCESS", f"已获取 {len(self._city_dict)} 个城市数据")
                        self._save_city_dict()
                    break
        except Exception as e:
            self._log("WARN", f"城市数据捕获异常: {e}")

    def _save_city_dict(self):
        """保存城市数据到文件。"""
        try:
            with open(CITY_DICT_FILE, "w", encoding="utf-8") as f:
                json.dump(self._city_dict, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_city_dict(self):
        """从文件加载之前捕获的城市数据。"""
        try:
            if CITY_DICT_FILE.exists():
                with open(CITY_DICT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._city_dict.update(data)
                        self._log("INFO", f"已加载 {len(self._city_dict)} 个城市数据(文件)")
        except Exception:
            pass

    def _check_login_expired(self) -> bool:
        """验证登录状态，检查当前页面是否被重定向到登录页。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return False
        try:
            current_url = instance.url
            is_suspicious = "passport" in current_url or "login" in current_url or not current_url
            if is_suspicious:
                self._log("WARN", "检测到登录过期，需要重新登录")
                instance.get("https://www.zhipin.com/web/user/?ka=header-login")
                self._random_delay(1, 2)
                if self._login_required_cb:
                    self._login_required_cb()
                self._log("INFO", "已打开登录页面，请在浏览器中完成登录")
                self._log("INFO", "等待登录确认...")
                if not self._wait_for_login():
                    self._log("ERROR", "登录超时")
                    return False
                self._save_cookies()
                self._log("SUCCESS", "登录成功")
            return True
        except Exception as e:
            self._log("WARN", "登录检查异常: " + str(e))
            if "断开" in str(e) or "disconnected" in str(e).lower():
                self._log("INFO", "尝试重新连接浏览器...")
                if self._reconnect_browser():
                    self._log("INFO", "浏览器重连成功")
                    return True
                self._log("ERROR", "浏览器重连失败")
                return False
            return False

    def _reconnect_browser(self) -> bool:
        """浏览器断连后尝试重连。"""
        try:
            self.browser_manager.close()
            return self._init_browser()
        except Exception as e:
            self._log("ERROR", f"重连失败: {e}")
            return False

    def _wait_for_login(self) -> bool:
        """等待用户手动登录。"""
        if not self._login_event.wait(timeout=self._login_wait_timeout):
            return False
        self._random_delay(2, 5)
        return self.check_login()

    def _build_search_url(self, query: str, city: str) -> str:
        """构建搜索 URL。"""
        city_code = self._get_city_id(city) if city else ""
        self._log("INFO", f"城市: {city}, 编码: {city_code}")
        if city_code:
            return f"https://www.zhipin.com/web/geek/jobs?query={query}&city={city_code}&industry=&position="
        else:
            return f"https://www.zhipin.com/web/geek/jobs?query={query}&industry=&position="

    def _parse_job_list(self):
        """解析岗位列表。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "浏览器未启动")
            self.jobs = []
            return

        self._log("INFO", "正在解析岗位列表...")

        search_url = self._build_search_url(self._query, self._city)
        self._log("INFO", f"访问搜索页面: {search_url}")
        instance.get(search_url)
        self._random_delay(3, 6)

        self._log("INFO", f"开始滚动 {self._scroll_pages} 次...")
        for i in range(self._scroll_pages):
            if not self.running:
                break
            try:
                instance.scroll.to_bottom()
                self._random_delay(1, 3)
                self._log("INFO", f"已滚动 {i+1}/{self._scroll_pages} 次")
            except Exception:
                self._log("WARN", "页面被刷新，等待页面加载完成后重试...")
                self._random_delay(2, 4)
                try:
                    instance.scroll.to_bottom()
                    self._random_delay(1, 3)
                except Exception:
                    pass

        job_url_elements = instance.eles(SELECTOR_JOB_NAME)
        full_job_urls = []
        for elem in job_url_elements:
            href = elem.attr("href")
            if href:
                if href.startswith("/"):
                    href = "https://www.zhipin.com" + href
                full_job_urls.append(href)

        self._log("INFO", f"共找到 {len(full_job_urls)} 个岗位链接")

        processed_jobs = []
        rec_list_ele = instance.ele(SELECTOR_REC_JOB_LIST, timeout=3)
        if rec_list_ele:
            job_name_list = rec_list_ele.texts()
            self._log("INFO", f"从 rec-job-list 解析出 {len(job_name_list)} 条文本")
            for idx, job_str in enumerate(job_name_list):
                parts = job_str.split("\n")
                if len(parts) < 4:
                    continue
                first_part = parts[0]
                salary_start = len(first_part)
                for marker in ["K", "元/月", "元/天", "薪"]:
                    m_idx = first_part.find(marker)
                    if m_idx != -1 and m_idx < salary_start:
                        salary_start = m_idx
                job_name = first_part[:salary_start].strip() if salary_start < len(first_part) else first_part
                salary = first_part[salary_start:].strip() if salary_start < len(first_part) else ""
                processed_jobs.append({
                    "job_name": job_name,
                    "salary": salary,
                    "experience": parts[1] if len(parts) > 1 else "",
                    "education": parts[2] if len(parts) > 2 else "",
                    "company_location": parts[3] if len(parts) > 3 else "",
                    "url": full_job_urls[idx] if idx < len(full_job_urls) else "",
                    "query": self._query,
                })
        else:
            self._log("INFO", "未找到 rec-job-list，直接使用链接")
            for u in full_job_urls:
                processed_jobs.append({
                    "job_name": "", "salary": "", "url": u, "query": self._query
                })

        self._log("INFO", f"解析出 {len(processed_jobs)} 条岗位信息")
        self.jobs = processed_jobs

    def _get_city_id(self, city_name: str) -> str:
        """获取城市编码。优先使用 API 捕获数据，再使用硬编码映射。"""
        if self._city_dict and city_name in self._city_dict:
            code = self._city_dict[city_name]
            self._log("INFO", f"城市 {city_name} 编码 (API): {code}")
            return str(code)
        if city_name in CITY_CODES:
            self._log("INFO", f"城市 {city_name} 编码 (硬编码): {CITY_CODES[city_name]}")
            return CITY_CODES[city_name]
        self._log("WARN", f"未找到城市 {city_name} 的编码")
        return ""

    def _resolve_images(self) -> list:
        """解析作品图片路径。"""
        import glob
        static_dir = BASE_DIR / "static"
        dashboard_dir = static_dir / "dashboard"
        dashboard_dir.mkdir(parents=True, exist_ok=True)

        resolved = []

        if self._image_files:
            for img in self._image_files:
                if isinstance(img, str):
                    if img.startswith("dashboard/") or img.startswith("dashboard\\"):
                        full_path = str(static_dir / img.replace("\\", "/"))
                    elif os.path.isabs(img):
                        full_path = img
                    else:
                        full_path = str(dashboard_dir / img)
                    if os.path.isfile(full_path):
                        resolved.append(full_path)
                    else:
                        self._log("WARN", f"配置图片不存在: {full_path}")

        # 兜底：扫描 dashboard 目录
        if not resolved:
            for ext in ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"]:
                for f in glob.glob(str(dashboard_dir / ext)):
                    resolved.append(f)

        final_resolved = list(dict.fromkeys(resolved))
        if final_resolved:
            self._log("INFO", f"共准备 {len(final_resolved)} 张作品图片")
        else:
            self._log("WARN", "没有作品图片")
        return final_resolved

    def _init_ai(self):
        """初始化 AI 分析器（懒加载）。"""
        if self._ai_analyzer is not None:
            return self._ai_analyzer
        if not self._ai_enabled:
            return None
        try:
            if self._ai_providers:
                self._ai_analyzer = AIAnalyzerChain(
                    providers=self._ai_providers,
                    match_threshold=self._ai_threshold,
                    log_callback=lambda msg: self._log("INFO", msg),
                )
            else:
                self._log("WARN", "AI 已启用但未配置任何 provider")
                return None
            self._ai_analyzer.set_resume(self._resume_cfg)
            self._log("INFO", f"🤖 AI 智能解析已启用（阈值: {self._ai_threshold}）")
            return self._ai_analyzer
        except Exception as e:
            self._log("WARN", f"AI 分析器初始化失败（降级为普通投递）: {e}")
            return None

    def _analyze_job_with_ai(self, job: dict):
        """用 AI 分析岗位匹配度。返回 (匹配结果, 耗时秒数)，未启用时返回 (None, 0)。"""
        analyzer = self._init_ai()
        if not analyzer:
            return None, 0
        ai_job = {
            "job_name": job.get("job_name", ""),
            "salary": job.get("salary", ""),
            "description": job.get("description", job.get("jd_description", "")),
            "requirements": job.get("requirements", job.get("jd_requirements", "")),
            "company": job.get("company", job.get("company_location", "")),
            "url": job.get("url", ""),
        }
        try:
            _start = time.time()
            result = analyzer.analyze_job(ai_job)
            duration = time.time() - _start
            score = result.get("score", 50)
            is_match = result.get("is_match", True)
            # 保存 AI 分析的完整信息供 GreetRecord 记录使用
            self._last_ai_result = result
            self._last_ai_system_prompt = analyzer.last_system_prompt
            self._last_ai_user_prompt = analyzer.last_user_prompt
            self._last_ai_model = analyzer.last_model_name
            self._last_ai_raw_response = analyzer.last_raw_response
            self._log("INFO", f"🤖 AI 匹配度: {score}/100 ({duration:.1f}s) —— {result.get('reason', '')[:80]}")
            return (result, duration) if (is_match and score >= self._ai_threshold) else (None, duration)
        except Exception as e:
            self._log("WARN", f"AI 分析异常，按通过处理: {e}")
            self._last_ai_result = None
            return None, 0

    def _step_browse_jobs(self):
        """遍历岗位列表并投递。"""
        resolved_images = self._resolve_images()
        self._image_files = resolved_images
        self._log("INFO", f"最终准备发送 {len(self._image_files)} 张作品图片")

        self.total_jobs = len(self.jobs)
        self._report_progress()

        for idx, job in enumerate(self.jobs):
            if not self.running:
                break
            if self._rate_limit_enabled and self.applied_count >= self._max_per_hour:
                self._log("WARN", f"已达到每小时上限 {self._max_per_hour}，暂停 30 分钟")
                if not self._wait_or_stop(1800):
                    break
            self._log("INFO", f"处理 [{idx+1}/{self.total_jobs}] {job.get('job_name', '未知岗位')}")
            self._random_delay(self._min_interval, self._max_interval)

            # 先检查是否已沟通过
            if self._is_already_chatted(job):
                self._log("INFO", f"⏭️ 已沟通过: {job.get('job_name', '')}")
                self.skipped_count += 1
                self._report_progress()
                self._save_chat_log(job, skipped=True)
                self._emit_greet_event(job, "already")
                self._record_greet(job, is_skipped=True, skip_reason="已沟通过")
                continue

            # AI 智能匹配
            has_ai = self._ai_enabled and bool(self._ai_providers)
            if has_ai:
                ai_result, ai_duration = self._analyze_job_with_ai(job)
                if ai_result is None and self._init_ai() is not None:
                    self._log("WARN", f"🤖 AI 判定不匹配，跳过: {job.get('job_name', '')}")
                    self._fetch_jd_for_job(job)
                    self.skipped_count += 1
                    self._save_chat_log(job, skipped=True, ai_result=None, ai_duration=ai_duration)
                    self._report_progress()
                    self._emit_greet_event(job, "ai_skip")
                    self._record_greet(job, is_skipped=True, skip_reason="AI判定不匹配")
                    continue
                if ai_result and ai_result.get("suggested_greeting"):
                    self._greeting_message = ai_result["suggested_greeting"]
            else:
                ai_result = None
                ai_duration = 0

            if not self._check_login_expired():
                break

            try:
                success = self._apply_job(job)
                if success:
                    self.applied_count += 1
                    self._save_chat_log(job, skipped=False, ai_result=ai_result, ai_duration=ai_duration)
                    self._log("SUCCESS", f"✅ 已投递: {job.get('job_name', '')}")
                    self._emit_greet_event(job, "success", ai_result)
                    self._record_greet(
                        job, is_greeted=True,
                        actual_greeting_sent=job.get("_actual_greeting_sent", ""),
                    )
                else:
                    self.skipped_count += 1
                    self._log("WARN", f"⏭️ 跳过: {job.get('job_name', '')}")
                    self._emit_greet_event(job, "skip", ai_result)
                    self._record_greet(job, is_skipped=True, skip_reason="投递失败")
            except Exception as e:
                self._log("WARN", f"投递异常: {e}")
                self.skipped_count += 1
                self._emit_greet_event(job, "error")
                self._record_greet(job, is_skipped=True, skip_reason=f"投递异常: {e}")
            self._report_progress()

    def _fetch_jd_for_job(self, job: dict):
        """为 AI 跳过的岗位抓取 JD 信息（仅读取详情页，不投递）。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return
        url = job.get("url", "")
        if not url:
            return
        try:
            instance.run_js(f"window.location.href = '{url}'")
            self._random_delay(3, 5)
            try:
                job_desc_elem = instance.ele(".job-sec-text", timeout=3)
                if job_desc_elem:
                    job["jd_description"] = job_desc_elem.text
            except Exception:
                pass
            try:
                req_elem = instance.ele(".requirements", timeout=2)
                if req_elem:
                    job["jd_requirements"] = req_elem.text
            except Exception:
                pass
            if not job.get("jd_requirements"):
                job["jd_requirements"] = job.get("jd_description", "")
            self._log("INFO", f"[JD] 已抓取: {job.get('job_name','')} ({len(job.get('jd_description',''))}字)")
        except Exception as e:
            self._log("WARN", f"[JD] 抓取失败: {e}")

    def _handle_disconnect(self) -> bool:
        """处理页面断开连接，尝试恢复。"""
        instance = self.browser_manager.get_instance()
        try:
            self._log("WARN", "开始处理页面断开，尝试恢复浏览器...")

            # 1. 轻量级恢复：导航到 about:blank
            if instance:
                try:
                    instance.get('about:blank')
                    self._random_delay(1, 2)
                    _ = instance.url
                    self._log("INFO", "轻量级恢复（about:blank）成功")
                    return True
                except Exception:
                    self._log("WARN", "轻量级恢复失败，尝试重初始化浏览器...")

            # 2. 重新初始化浏览器
            self.browser_manager.close()
            self._random_delay(3, 6)
            if not self._init_browser():
                self._log("ERROR", "重新初始化浏览器失败")
                return False
            self._log("INFO", "浏览器重新初始化成功")

            # 3. 重新加载 cookie 并检查登录
            self._load_cookies()
            self._random_delay(2, 4)

            instance = self.browser_manager.get_instance()
            for _ in range(3):
                try:
                    instance.get("https://www.zhipin.com")
                    self._random_delay(2, 3)
                    _ = instance.url
                    break
                except Exception:
                    self._random_delay(2, 3)

            if self.check_login():
                self._log("INFO", "页面断开后重新登录成功")
                return True

            try:
                instance.get("https://www.zhipin.com")
                self._random_delay(2, 3)
                self._log("INFO", "页面断开后恢复成功，继续执行")
            except Exception as e:
                self._log("WARN", f"恢复后导航到首页失败: {e}")
            return True
        except Exception as e:
            self._log("ERROR", f"处理页面断开异常: {e}")
            import traceback
            self._log("ERROR", traceback.format_exc())
            return False

    def _apply_job(self, job: dict, _disconnect_retry: int = 0) -> bool:
        """投递一个岗位。"""
        with self._apply_lock:
            return self._apply_job_inner(job, _disconnect_retry)

    def _apply_job_inner(self, job: dict, _disconnect_retry: int = 0) -> bool:
        """实际投递逻辑（内部方法）。"""
        if not self.running:
            return False
        url = job.get("url", "")
        if not url:
            return False

        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "浏览器未启动")
            return False

        # 如果页面已断开，先尝试恢复
        if _disconnect_retry == 0:
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "页面已断开，尝试恢复...")
                if self._handle_disconnect():
                    instance = self.browser_manager.get_instance()
                    _disconnect_retry = 1
                else:
                    return False

        try:
            # ── 1. 导航到岗位详情页 ──
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "导航前页面已断开，尝试恢复...")
                if not self._handle_disconnect():
                    return False
                instance = self.browser_manager.get_instance()

            for _retry in range(3):
                try:
                    self._log("INFO", f"导航到: {url}")
                    instance.run_js(f"window.location.href = '{url}'")
                    self._random_delay(3, 6)
                    self._log("INFO", f"导航后URL: {instance.url}")
                    break
                except Exception as _e:
                    self._log("WARN", f"页面加载重试: {_e}")
                    self._random_delay(2, 4)
            else:
                self._log("WARN", "页面加载失败，跳过此岗位")
                return False

            # 检查是否被重定向到登录页
            try:
                current_url = instance.url
                if "passport" in current_url or "login" in current_url:
                    self._log("WARN", "访问岗位详情时被重定向到登录页")
                    if self._login_required_cb:
                        self._login_required_cb()
                    self._log("INFO", "请重新登录，登录后点击「确认登录」")
                    if not self._wait_for_login():
                        self._log("ERROR", "登录超时")
                        return False
                    self._save_cookies()
                    self._log("SUCCESS", "登录成功")
                    try:
                        instance.get(url)
                        self._random_delay(3, 6)
                    except Exception:
                        return False
            except Exception:
                self._log("WARN", "页面断开")
                return False

            # ── 2. 查找沟通按钮 ──
            chat_btn = self._find_chat_button(timeout=8)
            if chat_btn is None:
                self._log("WARN", "未找到沟通按钮")
                return False

            btn_text = chat_btn.text
            if "继续沟通" in btn_text:
                self._log("INFO", "该岗位之前已投递过（继续沟通），跳过")
                return False

            # ── 3. 获取 JD 信息 ──
            job_description = ""
            job_requirements = ""
            try:
                job_desc_elem = instance.ele(".job-sec-text", timeout=3)
                if job_desc_elem:
                    job_description = job_desc_elem.text
                    self._log("INFO", "岗位描述: " + job_description[:100] + "...")
            except Exception:
                pass
            try:
                req_elem = instance.ele(".requirements", timeout=2)
                if req_elem:
                    job_requirements = req_elem.text
            except Exception:
                pass
            if not job_requirements:
                job_requirements = job_description
            job["jd_description"] = job_description
            job["jd_requirements"] = job_requirements
            self._log("INFO", f"JD 描述长度: {len(job_description)} 字符")

            # ── 4. 点击立即沟通 ──
            self._log("INFO", "开始点击沟通按钮...")
            chat_btn = instance.ele(".btn btn-startchat", timeout=5)
            if not chat_btn:
                self._log("WARN", "未找到沟通按钮!")
                return False
            self._log("INFO", f"找到沟通按钮，文本: {chat_btn.text}")
            chat_btn.click()
            self._log("INFO", "已点击沟通按钮，等待输入框...")

            # ── 5. 输入消息 ──
            greeting = self._greeting_message or self.config.greet.accounts[0].jobs[0].greeting_message if self.config.greet.accounts and self.config.greet.accounts[0].jobs else ""
            if not greeting:
                from boss_bot.unified_config import DEFAULT_GREETING
                greeting = DEFAULT_GREETING
            # 保存实际发送的打招呼语供 GreetRecord 记录使用
            job["_actual_greeting_sent"] = greeting
            self._log("INFO", f"打招呼语来源: {'AI定制' if self._greeting_message else '默认模板'}")
            input_area = instance.ele(".input-area", timeout=10)
            if not input_area:
                self._log("WARN", "未找到输入框!")
                return False
            self._log("INFO", "找到输入框，输入消息...")
            input_area.input(greeting)
            self._log("INFO", "消息已输入")

            # ── 6. 点击发送 ──
            instance.ele(".send-message").click()
            self._random_delay(1, 2)

            # 发送后检测页面是否断开
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "发送消息后页面连接断开，但消息可能已发送成功")
                self._mark_chatted(job)
                return True

            # ── 7. 发送图片 ──
            self._send_images_after_message()

            # ── 清理状态 ──
            try:
                close_btn = instance.ele(".icon-close", timeout=2)
                if close_btn:
                    close_btn.click()
                    self._random_delay(1, 2)
            except Exception:
                pass

            # ── 关闭当前 tab，回到列表页 ──
            try:
                instance.close_current_tab()
                self._random_delay(1, 2)
            except Exception:
                pass

            return True

        except Exception as e:
            self._log("WARN", "发送消息异常: " + str(e))
            import traceback
            self._log("WARN", traceback.format_exc())
            return False

    def _send_images_after_message(self):
        """在发送消息之后上传图片。"""
        instance = self.browser_manager.get_instance()
        if instance is None or not self._image_files:
            return

        # 先关闭当前聊天窗口，再重新打开
        try:
            close_btn = instance.ele(".icon-close", timeout=2)
            if close_btn:
                close_btn.click()
                self._random_delay(1, 2)
        except Exception:
            pass

        # 检查聊天窗口是否打开
        input_area = None
        try:
            input_area = instance.ele(".input-area", timeout=3)
        except Exception:
            pass

        if not input_area:
            self._log("INFO", "聊天窗口未打开，尝试重新打开...")
            chat_btn = self._find_chat_button(timeout=5)
            if chat_btn:
                try:
                    chat_btn.click()
                    self._random_delay(1, 2)
                except Exception as e:
                    self._log("WARN", f"重新打开聊天窗口失败: {e}")
                    return
            else:
                self._log("WARN", "未找到沟通按钮，无法上传图片")
                return

        # 上传图片 - 去重后依次上传
        seen = set()
        for img_path in self._image_files:
            if img_path in seen:
                continue
            seen.add(img_path)
            if os.path.isfile(img_path):
                abs_path = os.path.abspath(img_path)
                uploaded = self._upload_image(abs_path)
                if uploaded:
                    self._log("INFO", "已上传图片: " + os.path.basename(img_path))
                else:
                    self._log("WARN", "上传图片失败: " + os.path.basename(img_path))
                self._random_delay(1, 2)

    def _find_chat_button(self, timeout=5):
        """查找沟通按钮。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return None
        time.sleep(1)

        # 严格参考源文件：.btn btn-startchat（DrissionPage AND 语法）
        try:
            btn = instance.ele(".btn btn-startchat", timeout=timeout)
            if btn:
                return btn
        except Exception:
            pass

        # 备选：文本匹配
        for chat_text in ("立即沟通", "继续沟通"):
            try:
                btn = instance.ele(f"text:{chat_text}", timeout=2)
                if btn:
                    return btn
            except Exception:
                pass

        return None

    def _upload_image(self, img_path):
        """上传单张图片。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return False
        if not os.path.isfile(img_path):
            self._log("WARN", f"上传图片文件不存在: {img_path}")
            return False

        abs_path = os.path.abspath(img_path)

        # 优先：set.upload_files()
        try:
            instance.set.upload_files(abs_path)
            instance.wait.upload_paths_inputted()
            self._random_delay(2, 3)
            return True
        except Exception:
            pass

        # 备选1：直接找 input[type=file]
        try:
            file_input = instance.ele("tag:input@@type=file", timeout=3)
            if file_input:
                file_input.input(abs_path)
                self._random_delay(2, 3)
                return True
        except Exception:
            pass

        # 备选2：click.to_upload()
        try:
            btn = instance.ele(".toolbar-btn-content icon btn-sendimg tooltip tooltip-top", timeout=5)
            if btn:
                btn.click.to_upload(abs_path)
                self._random_delay(2, 3)
                return True
        except Exception:
            pass

        return False

    def _random_delay(self, min_sec: float, max_sec: float):
        """随机延迟（反爬策略）。"""
        if not self.running:
            return
        time.sleep(random.uniform(min_sec, max_sec))

    def _wait_or_stop(self, seconds: float) -> bool:
        """等待指定秒数，期间检查是否被停止。"""
        interval = 5
        for _ in range(int(seconds / interval)):
            if not self.running:
                return False
            time.sleep(interval)
        return self.running

    # ── Cookie 管理 ──

    def _load_cookies(self) -> bool:
        """加载 Cookie。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return False
        try:
            cookie_name = self._cookie_file if self._cookie_file else "zhipin_cookies.json"
            paths_to_try = []
            if not os.path.isabs(cookie_name):
                paths_to_try.append(str(DATA_DIR / cookie_name))
            else:
                paths_to_try.append(cookie_name)
            paths_to_try.append(str(DATA_DIR / "zhipin_cookies.json"))

            loaded = False
            for p in paths_to_try:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        cookies = json.load(f)
                    instance.set.cookies(cookies)
                    self._log("INFO", f"已加载 Cookie: {p}")
                    loaded = True
                    break
            if not loaded:
                self._log("WARN", "未找到 Cookie 文件")
            return loaded
        except Exception as e:
            self._log("WARN", f"Cookie 加载失败: {e}")
            return False

    def _save_cookies(self):
        """保存 Cookie。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return
        try:
            cookie_name = self._cookie_file if self._cookie_file else "zhipin_cookies.json"
            dst = str(DATA_DIR / cookie_name) if not os.path.isabs(cookie_name) else cookie_name
            cookies = instance.cookies()
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            self._log("INFO", f"已保存 Cookie: {dst}")
            fallback = str(DATA_DIR / "zhipin_cookies.json")
            if dst != fallback:
                import shutil
                shutil.copy2(dst, fallback)
        except Exception as e:
            self._log("WARN", f"Cookie 保存失败: {e}")

    def _clear_cookies(self):
        """清除失效 Cookie。"""
        if self._clear_cookies_on_failure:
            try:
                cookie_name = self._cookie_file if self._cookie_file else "zhipin_cookies.json"
                dst = str(DATA_DIR / cookie_name) if not os.path.isabs(cookie_name) else cookie_name
                if os.path.exists(dst):
                    os.remove(dst)
                self._log("INFO", f"已清除失效 Cookie: {dst}")
            except Exception:
                pass

    # ── 去重管理 ──

    def _is_already_chatted(self, job: dict) -> bool:
        """检查是否已沟通过。"""
        url = job.get("url", "")
        if not url:
            return False
        try:
            if CHATTED_DB_FILE.exists():
                with open(CHATTED_DB_FILE, "r", encoding="utf-8") as f:
                    chatted = set(json.load(f))
                return url in chatted
        except Exception:
            pass
        return False

    def _mark_chatted(self, job: dict):
        """标记岗位为已沟通。"""
        url = job.get("url", "")
        if not url:
            return
        try:
            chatted = set()
            if CHATTED_DB_FILE.exists():
                with open(CHATTED_DB_FILE, "r", encoding="utf-8") as f:
                    chatted = set(json.load(f))
            chatted.add(url)
            with open(CHATTED_DB_FILE, "w", encoding="utf-8") as f:
                json.dump(list(chatted), f, ensure_ascii=False)
        except Exception:
            pass

    # ── 聊天日志 ──

    def _save_chat_log(self, job: dict, skipped: bool = False, ai_result: dict = None, ai_duration: float = 0):
        """保存聊天日志。"""
        try:
            logs = []
            if CHATS_LOG_FILE.exists():
                with open(CHATS_LOG_FILE, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            logs.append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "job_name": job.get("job_name", ""),
                "company": job.get("company_location", ""),
                "salary": job.get("salary", ""),
                "query": self._query,
                "city": self._city,
                "skipped": skipped,
                "ai_score": ai_result.get("score", "") if ai_result else "",
                "ai_reason": ai_result.get("reason", "") if ai_result else "",
            })
            with open(CHATS_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(logs[-500:], f, ensure_ascii=False, indent=2)
        except Exception:
            pass
