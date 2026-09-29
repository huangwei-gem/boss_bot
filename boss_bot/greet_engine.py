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
import re
import random
import time
import threading
import hashlib
import logging
from datetime import datetime
from typing import Optional, Callable
from urllib.request import Request, urlopen
from urllib.error import URLError

from boss_bot.unified_config import UnifiedConfig, BASE_DIR, resolve_path, write_json_atomic
from boss_bot.browser_launcher import BrowserManager
from boss_bot.reply_record import GreetRecord, _get_greet_store

# ─────────────────────────────────────────────
# 路径常量
# ─────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHATTED_DB_FILE = DATA_DIR / "chatted_jobs.json"
CITY_DICT_FILE = DATA_DIR / "city_dict.json"
AI_CACHE_FILE = DATA_DIR / "ai_cache.json"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# 找不到聊天输入框时的页面快照与归因
# ─────────────────────────────────────────────
# 历史 172 条跳过记录都是同一句"未找到输入框"，把 logs 里 116 次的现场逐条
# 分类后其实是三件不同的事：61 次页面被换成手机+短信验证码登录框（登录态掉了）、
# 45 次沟通抽屉压根没在页内渲染、8 次标签页连接已断。处置方式完全不同，
# 所以失败原因必须按现场分档写出来。
#
# 快照必须连 contenteditable 一起查：BOSS 的聊天输入框是
# #chat-input.chat-input 这个 contenteditable div，旧 dump 只抓 input/textarea，
# 于是"页面上没输入框"这个结论本身可能是瞎的。
CHAT_SNAPSHOT_JS = r'''
return (function(){
  function cls(el){
    var c = el.className;
    if (typeof c === 'string' && c.trim()) return c.trim();
    return (el.getAttribute && el.getAttribute('type')) || '';
  }
  var inputs = [];
  var nodes = document.querySelectorAll('input, textarea');
  for (var i = 0; i < nodes.length && inputs.length < 30; i++) {
    var v = cls(nodes[i]);
    if (v) inputs.push(v);
  }
  var sels = ['#chat-input', '.chat-input', '[contenteditable="true"]', '.input-area',
              '.chat-container', '.chat-popup', '.drawer', '.modal-content', '.send-message'];
  var found = [];
  for (var j = 0; j < sels.length; j++) {
    if (document.querySelector(sels[j])) found.push(sels[j]);
  }
  var txt = document.body ? (document.body.innerText || "") : "";
  var captcha = !!document.querySelector(".nc-container, .verify-wrap, .geetest_panel, .verify-box, .captcha-box")
                || txt.indexOf("安全验证") >= 0 || txt.indexOf("拖动滑块") >= 0;
  return JSON.stringify({url: location.href, inputs: inputs,
                         chat_elements: found, captcha: captcha});
})();
'''

# 快照里"确实能打字发消息"的那几个元素，与只能证明抽屉存在的那些
_INPUTISH = ("#chat-input", ".chat-input", '[contenteditable="true"]', ".input-area")
_LOGIN_CLS = ("ipt-phone", "ipt-sms")


def chat_failure_reason(snap):
    """把失败瞬间的页面快照归成一句能照着修的原因。"""
    snap = snap or {}
    err = str(snap.get("error") or "")
    low = err.lower()
    if "断开" in err or "disconnect" in low or "connection" in low or "refused" in low:
        return "聊天页与浏览器连接已断开（标签页被关或被别的线程抢走）"

    inputs = [str(c).lower() for c in (snap.get("inputs") or [])]
    url = str(snap.get("url") or "")
    if snap.get("captcha") or "_security_check" in url:
        return "BOSS 弹出人机验证，需要人工在浏览器窗口完成（超时会自动跳过）"
    if any(any(k in c for k in _LOGIN_CLS) for c in inputs):
        return "BOSS 要求重新登录（页面出现手机号+短信验证码框），登录态已失效"
    if "/web/user" in url:
        return "BOSS 要求重新登录（页面被送到登录页 %s），登录态已失效" % url[:50]

    chat = list(snap.get("chat_elements") or [])
    if chat and not any(c in _INPUTISH for c in chat):
        return "聊天抽屉容器已出现但输入框没渲染（页面卡在半成品状态）"
    if not chat:
        if "job_detail" in url:
            return "点了「立即沟通」但聊天抽屉没在这个标签页里出现（URL 仍停在岗位详情页）"
        return "页面已跳走且没有聊天元素（当前 URL: %s）" % (url[:60] or "未知")
    return "未找到聊天输入框，页面上有抽屉相关元素: %s" % ", ".join(chat[:4])


# ─────────────────────────────────────────────
# 文件日志
# ─────────────────────────────────────────────
_file_handler = logging.FileHandler(str(LOG_DIR / "greet_engine.log"), encoding="utf-8")
# 时间必须带日期：只有 HH:MM:SS 时跨天的日志分不开，事后归因会把不同天的
# 同一现象当成一次（这次查"未找到输入框"就被这个坑过一次）
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s",
                                             datefmt="%Y-%m-%d %H:%M:%S"))
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


class AIResponseUnusable(Exception):
    """接口回了话，但没回可信判分（空正文/截断/没有 JSON/没有 score）。

    必须走异常而不是返回兜底 dict：兜底 dict 会被容灾链当成"这个接口成功了"，
    于是既不换下一个接口，又把坏接口回报成健康 —— AI 筛岗静默失效。
    """


class AIAnalyzerChain:
    """多 AI 容灾链：按顺序尝试多个 AI 接口，自动切换。

    容灾链可能有二十几个接口，每个 30s 超时。不加约束的话，一个岗位最坏要
    串行等完全部接口（实测每个岗位 7~8 分钟），打招呼线程看起来就像卡死。
    因此这里限制单岗位的尝试数量与总耗时，并对连续失败的接口做冷却。
    """

    _cache_lock = threading.Lock()

    # 单个岗位最多尝试多少个接口、最多花多少秒
    MAX_ATTEMPTS_PER_JOB = 4
    JOB_BUDGET_SECONDS = 60
    # 带 thinking 的接口实测 1024 token 会被思考吃光（正文为空 + finish=length），
    # 默认给到 1600；真被截断时换接口，不再原地"默认通过"。
    DEFAULT_ANALYZE_MAX_TOKENS = 1600
    # 接口失败后的冷却时间（秒）：鉴权/额度类错误冷却更久
    COOLDOWN_AUTH_SECONDS = 1800
    COOLDOWN_OTHER_SECONDS = 300

    def __init__(
        self,
        providers: list,
        match_threshold: int = 70,
        cache_enabled: bool = True,
        cache_ttl_hours: int = 24,
        log_callback: Optional[Callable] = None,
        custom_filter_keywords: list = None,
        custom_scoring_prompt: str = "",
        skip_unhealthy: bool = True,
        analyze_max_tokens: int = 0,
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
            elif hasattr(p, "name") and hasattr(p, "api_key"):
                # duck typing: 支持 AIProvider dataclass 或任何具有相同属性的对象
                self.providers.append(AIProviderConfig(
                    name=getattr(p, "name", "AI"),
                    api_key=getattr(p, "api_key", ""),
                    api_base=getattr(p, "api_base", ""),
                    model=getattr(p, "model", ""),
                    timeout=getattr(p, "timeout", 30),
                ))

        self.match_threshold = match_threshold
        self.cache_enabled = cache_enabled
        self.cache_ttl = cache_ttl_hours * 3600
        self.log_cb = log_callback
        self.custom_filter_keywords = custom_filter_keywords or []
        self.custom_scoring_prompt = custom_scoring_prompt or ""
        self.analyze_max_tokens = analyze_max_tokens or self.DEFAULT_ANALYZE_MAX_TOKENS

        self.analyzed_count = 0
        self.match_count = 0
        self.cache_hit_count = 0
        # 没能真判分、落到"默认通过"的岗位数 —— 面板用它回答"AI 到底筛没筛"
        self.fallback_count = 0
        self._resume = None
        self._resume_hash = ""

        # 追踪最后一次分析的信息（供 GreetRecord 记录使用）
        self.last_system_prompt = None
        self.last_user_prompt = None
        self.last_model_name = ""
        self.last_raw_response = None
        # 失败接口冷却表：provider.name -> 恢复时间戳
        self._cooldown_until: dict = {}
        # 是否按体检结果跳过已知不可用的接口
        self._skip_unhealthy = skip_unhealthy
        self._unhealthy_cache = None

    HEALTH_STALE_SECONDS = 24 * 3600

    def _unhealthy_names(self) -> set:
        """体检在 24h 内明确标记"不可用"的接口名（缓存 5 分钟，避免每岗位读盘）。

        容灾链的相对顺序不变，只是不再把 30s 预算浪费在已知打不通的接口上；
        全部接口都被标记不可用时不生效（宁可慢，也不能完全不筛岗位）。
        """
        now = time.time()
        if self._unhealthy_cache and now - self._unhealthy_cache[0] < 300:
            return self._unhealthy_cache[1]
        names: set = set()
        try:
            from boss_bot.ai_health import (HEALTH_FILE, STATUS_UNAVAILABLE,
                                            provider_key, load_health)
            data = load_health()
            updated = data.get("updated_at") or ""
            try:
                from datetime import datetime as _dt
                age = now - _dt.strptime(updated, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                age = self.HEALTH_STALE_SECONDS + 1  # 没有时间戳就当作过期
            results = data.get("results") or {}
            if age <= self.HEALTH_STALE_SECONDS and results:
                for p in self.providers:
                    entry = {"api_base": p.api_base, "model": p.model, "name": p.name}
                    hit = results.get(provider_key(entry)) or results.get(
                        provider_key(dict(entry, api_base=entry["api_base"] + "/")))
                    if hit and hit.get("status") == STATUS_UNAVAILABLE:
                        names.add(p.name)
        except Exception as e:
            self._log("DEBUG", f"读取 AI 体检结果失败，本次不跳过任何接口: {e}")
            names = set()
        self._unhealthy_cache = (now, names)
        return names

    def _cool_down(self, provider, error: Exception):
        """把失败的接口临时拉黑，避免每个岗位都重踩同一个坑。

        401/403/404/额度类错误短期内不会自己恢复，冷却时间长一些。
        """
        text = str(error).lower()
        permanent = any(k in text for k in
                        ("401", "403", "404", "free", "quota", "insufficient",
                         "unauthorized", "invalid_api_key", "not found"))
        seconds = (self.COOLDOWN_AUTH_SECONDS if permanent
                   else self.COOLDOWN_OTHER_SECONDS)
        self._cooldown_until[provider.name] = time.time() + seconds
        self._log("WARN", f"接口 [{provider.name}] 冷却 {seconds // 60} 分钟")

    def _report_health(self, provider, ok: bool, error: str = ""):
        """把真实调用的成败回写体检表（ping 得通、真提示词超时的接口靠这个揪出来）。"""
        try:
            from boss_bot.ai_health import report_runtime_result
            report_runtime_result(provider, ok, error)
            if not ok:
                self._unhealthy_cache = None   # 下一个岗位就按新结果跳过，不用等 5 分钟
        except Exception as e:
            self._log("DEBUG", f"回写 AI 体检结果失败: {e}")

    def _log(self, level: str, msg: str):
        if self.log_cb:
            self.log_cb(f"[AI] [{level}] {msg}")

    def set_resume(self, resume: dict):
        self._resume = resume
        self._resume_hash = hashlib.md5(
            json.dumps(resume, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def analyze_job(self, job: dict) -> dict:
        """分析单个岗位。依次尝试所有 provider，直到成功。

        AI 完全不可用时返回带 `ai_error` 标记的结果，调用方据此区分
        「AI 说这个岗位不匹配」和「AI 没给出判断」——两者的处理方式相反。
        """
        if not self.providers:
            self.fallback_count += 1
            return {"score": 50, "is_match": True, "ai_error": True,
                    "reason": "未配置 AI 接口，按默认话术通过", "suggested_greeting": ""}

        # 检查缓存
        if self.cache_enabled and self._resume_hash:
            cache_key = self._make_cache_key(job.get("url", ""), self._resume_hash)
            cache = self._load_cache()
            if cache_key in cache:
                self.cache_hit_count += 1
                self._log("INFO", f"缓存命中: {job.get('job_name', '')}")
                # 命中的记录也要带上当初判分的接口名，否则面板会把它当"没跑 AI"
                self.last_model_name = (cache[cache_key] or {}).get("model", "")
                self.last_raw_response = None
                return cache[cache_key]["result"]

        # 依次尝试 provider — 受单岗位尝试数与总耗时双重限制
        # 上一个岗位的接口名/原文不能留到这条记录上（by_model 统计会算错）
        self.last_model_name = ""
        self.last_raw_response = None
        prompt = self._build_prompt(job)
        # 保存 prompt 信息供 GreetRecord 记录使用
        self.last_system_prompt = prompt[0]["content"] if len(prompt) > 0 else None
        self.last_user_prompt = prompt[1]["content"] if len(prompt) > 1 else None
        last_error = None
        now = time.time()
        deadline = now + self.JOB_BUDGET_SECONDS
        attempts = 0
        skipped_cooling = 0
        unhealthy = self._unhealthy_names() if self._skip_unhealthy else set()
        if unhealthy and len(unhealthy) >= len(self.providers):
            # 一个都不剩说明体检结果本身不可信（或全部真挂了），照原顺序硬试
            self._log("WARN", "体检显示所有接口都不可用，本轮不跳过任何接口")
            unhealthy = set()
        for provider in self.providers:
            if not provider.is_valid():
                self._log("WARN", f"AI 接口 '{provider.name}' 配置无效，跳过")
                continue
            if provider.name in unhealthy:
                continue
            until = self._cooldown_until.get(provider.name, 0)
            if until > now:
                skipped_cooling += 1
                continue
            if attempts >= self.MAX_ATTEMPTS_PER_JOB:
                break
            if time.time() >= deadline:
                self._log("WARN", f"AI 分析超出 {self.JOB_BUDGET_SECONDS}s 预算，"
                                  f"放弃剩余接口（已试 {attempts} 个）")
                break
            attempts += 1
            try:
                self._log("INFO", f"通过 [{provider.name}] ({provider.model}) 分析...")
                result = self._call_provider_api(provider, prompt)
                self._cooldown_until.pop(provider.name, None)
                self._report_health(provider, ok=True)
                self.last_model_name = provider.model
                self.analyzed_count += 1
                if result.get("is_match", False):
                    self.match_count += 1

                # 写入缓存 —— 只缓存真判断，超时/解析失败的结果缓存 24 小时
                # 会让同一个岗位永远"默认通过"，等于悄悄跳过筛选
                if self.cache_enabled and self._resume_hash and not result.get("ai_error"):
                    cache_key = self._make_cache_key(job.get("url", ""), self._resume_hash)
                    cache = self._load_cache()
                    cache[cache_key] = {
                        "result": result,
                        "model": provider.model,
                        "cached_at": time.time(),
                        "_expires_at": time.time() + self.cache_ttl,
                    }
                    self._save_cache(cache)

                return result

            except Exception as e:
                last_error = e
                self._cool_down(provider, e)
                self._report_health(provider, ok=False, error=str(e))
                self._log("WARN", f"[{provider.name}] 失败: {e}，尝试下一个...")
                continue

        if skipped_cooling:
            self._log("INFO", f"{skipped_cooling} 个 AI 接口在冷却中，已跳过")
        if unhealthy:
            self._log("INFO", f"按体检结果跳过 {len(unhealthy)} 个已知不可用的 AI 接口")

        # 全部失败
        self._log("ERROR", f"所有 AI 接口均失败，最后错误: {last_error}")
        self.fallback_count += 1
        return {"score": 50, "is_match": True, "ai_error": True,
                "reason": f"AI 分析异常: {last_error}，默认通过", "suggested_greeting": ""}

    def _extract_json(self, text: str) -> dict:
        """从模型正文里抠出那个 JSON 对象。

        三种真实输出都得吃下（实测样本）：纯 JSON、```json 代码块、
        前后夹带中文说明。括号按字符串状态配对，避免尾随说明里的 "}"
        把截取区间带偏（旧实现用首 { 到末 }，遇到 "（详见附录}）" 直接解析失败）。
        """
        body = text.strip()
        fence = re.search(r"```(?:json)?\s*(.+?)\s*```", body, re.S)
        if fence:
            body = fence.group(1).strip()
        start = body.find("{")
        if start < 0:
            raise AIResponseUnusable(f"响应里没有 JSON（前 60 字：{body[:60]}）")

        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(body)):
            ch = body[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(body[start:i + 1])
                    except json.JSONDecodeError as e:
                        raise AIResponseUnusable(f"JSON 不完整: {e}")
        raise AIResponseUnusable(f"JSON 没有闭合（末 40 字：{body[-40:]}）")

    def _normalize_result(self, result) -> dict:
        """把模型给的 JSON 收敛成一份可信判分，缺判分就抛。"""
        if not isinstance(result, dict):
            raise AIResponseUnusable(f"返回的不是 JSON 对象：{str(result)[:60]}")

        score = None
        raw_score = result.get("score")
        if raw_score is not None:
            try:
                score = max(0, min(100, int(float(raw_score))))
            except (TypeError, ValueError):
                score = None

        is_match = result.get("is_match")
        if score is None and not isinstance(is_match, bool):
            raise AIResponseUnusable(
                "返回里没有可用的 score/is_match，无法判分"
                f"（前 60 字：{json.dumps(result, ensure_ascii=False)[:60]}）")

        if score is None:
            # 只给了结论没给分：按阈值折算一个分，别让调用方拿默认 50 误判
            score = self.match_threshold if is_match else max(0, self.match_threshold - 1)
        if not isinstance(is_match, bool):
            is_match = score >= self.match_threshold

        result["score"] = score
        result["is_match"] = is_match

        # 自定义筛选条件是硬否决：模型自己承认命中就不能因为分高而放行
        veto = str(result.get("veto_hit") or "").strip()
        if veto and is_match:
            result["is_match"] = False
            result["score"] = min(score, max(0, self.match_threshold - 1))
            result["reason"] = f"命中硬性筛选条件「{veto}」，不予通过。{result.get('reason', '')}"
        return result

    def _call_provider_api(self, provider: AIProviderConfig, messages: list) -> dict:
        """调用指定 AI 接口，返回一份可信判分；拿不到就抛 AIResponseUnusable。"""
        url = f"{provider.api_base}/chat/completions"
        payload = json.dumps({
            "model": provider.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": self.analyze_max_tokens,
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
            choice = data["choices"][0]
        except (KeyError, IndexError, TypeError):
            raise AIResponseUnusable(
                f"响应缺少字段 choices（{str(data.get('msg') or data)[:120]}）")

        return self._parse_completion(choice)

    def _parse_completion(self, choice: dict) -> dict:
        """把一次 chat completion 的 choice 收敛成判分，不可用就抛。

        单独拆出来是为了让诊断脚本（tools/measure_ai_quality.py）用的是
        生产同一份判定，而不是自己抄一套"看起来能解析"。
        """
        message = choice.get("message") or {}
        finish = choice.get("finish_reason") or ""
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or "").strip()
        self.last_raw_response = content or reasoning

        if not content and finish == "length":
            # 推理型接口把预算全花在 thinking 上，正文一个字没写：
            # 这不是"这个岗位不匹配"，是这个接口这次没给出判断
            raise AIResponseUnusable(
                f"正文被截断（thinking 写了 {len(reasoning)} 字，"
                f"max_tokens={self.analyze_max_tokens} 不够）")

        body = content or reasoning
        if not body:
            raise AIResponseUnusable("模型未返回正文（content 与 reasoning_content 均为空）")

        return self._normalize_result(self._extract_json(body))

    def _build_prompt(self, job: dict) -> list:
        """构建 AI 分析提示词。"""
        resume = self._resume or {}
        system_msg = (
            "你是 Boss直聘智能投递助手的岗位匹配分析专家。你的任务是分析招聘岗位与求职者简历的匹配程度，"
            "给出评分和详细理由。请按 JSON 格式返回结果。"
        )
        if self.custom_scoring_prompt:
            system_msg += "\n\n【用户自定义打分要求】\n" + self.custom_scoring_prompt
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
        )
        if self.custom_filter_keywords:
            keywords_str = "、".join(self.custom_filter_keywords)
            user_msg += (
                f"【用户硬性筛选条件】\n"
                f"以下是求职者设定的否决条件：{keywords_str}\n"
                "只要岗位命中其中任意一条（例如岗位是外包驻场、城市不在范围内、"
                "学历/经验要求不满足），is_match 必须为 false，"
                "并把命中的那条原样写进 veto_hit；没命中则 veto_hit 留空字符串。\n"
                "命中否决条件时不要因为薪资或其它方面不错而加分放行。\n\n"
            )
        veto_field = ('  "veto_hit": "命中的否决条件原文，没有则留空",\n'
                      if self.custom_filter_keywords else "")
        user_msg += (
            "请分析匹配度，按以下 JSON 格式返回（不要包含其他内容）：\n"
            '{\n  "score": 0-100,\n  "is_match": true/false,\n'
            + veto_field +
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
        with self._cache_lock:
            write_json_atomic(AI_CACHE_FILE, cache)

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

    # 多账号共用 data/chatted_jobs.json，写盘要跨实例串行 + 合并
    _chatted_lock = threading.Lock()
    # 去重快照的重读间隔（秒）：另一账号打过的岗位本账号要能在下一轮看到
    CHATTED_REFRESH_SECONDS = 60

    def __init__(
        self,
        browser_manager: BrowserManager,
        config: UnifiedConfig,
        log_callback: Optional[Callable] = None,
        progress_callback: Optional[Callable] = None,
        greet_event_cb: Optional[Callable] = None,
        wind_control_cb: Optional[Callable] = None,
        account_index: int = 0,
    ):
        self.browser_manager = browser_manager
        self.config = config
        # 话术/简历图片按账号取：写死 accounts[0] 会让账号2 用主账号的
        # 打招呼语和简历，两个号发出去的内容一模一样
        self.account_index = account_index
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
        # 已沟通岗位集合的进程内缓存（首次使用时读盘）
        self._chatted_cache = None
        self._chatted_loaded_at = 0.0

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
        self._greeting_source = "默认"
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

        # 打招呼记录存储（使用全局单例，确保与 API 层、清空操作共享同一实例）
        self._greet_store = _get_greet_store()

        # 追踪最后一次 AI 分析的完整信息（供 GreetRecord 记录使用）
        self._last_ai_result = None
        self._last_ai_system_prompt = None
        self._last_ai_user_prompt = None
        self._last_ai_model = ""
        self._last_ai_raw_response = None
        # AI 本次有没有真给出判断（见 _record_greet 的 ai_error）+ 本次判分耗时
        self._last_ai_duration_ms = 0

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
        self._cookie_file = login_cfg.cookie_file

        # 频率限制 / 重试次数由 reload_runtime_settings() 统一赋值（见文件末尾）
        retry_cfg = self.config.greet.retry
        self._retry_base_delay = retry_cfg.base_delay
        self._retry_backoff_factor = retry_cfg.backoff_factor

        self._apply_ai_config()

        # 岗位去重集合（基于URL+公司名+岗位名，仅当前运行期间有效）
        self._applied_job_keys = set()

        self.reload_runtime_settings()

    def _apply_ai_config(self):
        """把 config.ai 摊平成引擎字段；配置真的变了就丢掉容灾链，让它按新配置重建。

        热重载只换 config 对象不会重建容灾链 —— 阈值、否决关键词、单次预算改了
        要重启才生效，这正是"改了必须生效"的反例。签名相同则什么都不动，
        免得每轮热重载把冷却表清零、又开始重踩已知坏接口。
        """
        ai = self.config.ai
        sig = (ai.enabled, ai.match_threshold, ai.analyze_max_tokens,
               tuple(ai.custom_filter_keywords or []), ai.custom_scoring_prompt,
               ai.skip_unhealthy,
               tuple((p.name, p.model, p.api_base, bool(p.api_key)) for p in ai.providers))
        if getattr(self, "_ai_config_sig", None) == sig:
            return
        rebuilt = self._ai_analyzer is not None
        self._ai_config_sig = sig

        self._ai_enabled = ai.enabled
        self._ai_threshold = ai.match_threshold
        self._ai_custom_filter_keywords = ai.custom_filter_keywords
        self._ai_custom_scoring_prompt = ai.custom_scoring_prompt
        self._ai_skip_unhealthy = ai.skip_unhealthy
        self._analyze_max_tokens = ai.analyze_max_tokens

        # AI providers 列表（从 UnifiedConfig 转换为 AIAnalyzerChain 所需格式）
        self._ai_providers = []
        for p in ai.providers:
            self._ai_providers.append({
                "name": p.name,
                "api_key": p.api_key,
                "api_base": p.api_base,
                "model": p.model,
                "timeout": p.timeout,
            })
        # 兼容旧格式
        if not self._ai_providers and ai.api_key:
            self._ai_providers.append({
                "name": "默认",
                "api_key": ai.api_key,
                "api_base": ai.api_base,
                "model": ai.model,
                "timeout": 30,
            })

        if rebuilt:
            self._log("INFO", "AI 配置已变更，下一个岗位按新配置重建容灾链")
            self._ai_analyzer = None

    def reload_runtime_settings(self):
        """从 self.config 重读「改了就该立刻生效」的字段。

        构造时读一次是不够的：主循环每轮热重载会换掉 config 对象，
        话术/简历图片/间隔若仍停留在首次快照，前端改了就要重启才生效。
        """
        self._resume_cfg = {
            "school": self.config.resume.school,
            "major": self.config.resume.major,
            "degree": self.config.resume.degree,
            "skills": self.config.resume.skills,
            "experience": self.config.resume.experience,
            "target_position": self.config.resume.target_position,
            "self_intro": self.config.resume.self_intro,
        }

        self._apply_ai_config()

        rl = self.config.greet.rate_limit
        self._rate_limit_enabled = rl.enabled
        self._max_per_hour = rl.max_per_hour
        self._max_per_day = rl.max_per_day
        self._retry_max_attempts = self.config.greet.retry.max_attempts

        # 从 greet.accounts 读取本账号的默认任务参数
        acc = self._account()
        if acc:
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
                self._greeting_source = "岗位配置"
                self._image_files = job.image_files or self._image_files

    def _account(self):
        """本引擎所属账号的配置；索引越界时回落到第一个账号。"""
        accounts = self.config.greet.accounts
        if not accounts:
            return None
        return accounts[self.account_index] if self.account_index < len(accounts) else accounts[0]

    def _account_label(self) -> str:
        """记录里显示的账号名 — 用配置里的名字，不再写 cookie 文件名。"""
        name = getattr(self._account(), "name", "") or ""
        return name or f"账号{self.account_index}"

    def _log(self, level: str, msg: str):
        """统一日志输出 — 回调 + 文件日志。"""
        if self.log_cb:
            self.log_cb(f"[{level}] {msg}")
        try:
            _file_logger.info(f"[{level}] {msg}")
        except Exception:
            pass

    def _emit_greet_event(self, job: dict, status: str, ai_result: dict = None,
                          skip_reason: str = ""):
        """推送投递事件到前端表格。

        Args:
            job: 岗位信息字典
            status: "success" | "skip" | "Bai_skip" | "already" | "error"
            ai_result: AI 分析结果（可选）
            skip_reason: 跳过原因（可选，用于前端显示）
        """
        if not self._greet_event_cb:
            return
        try:
            ai = ai_result or self._last_ai_result or {}
            # 关键修复：确保 ai_reason 完整透传到前端
            # 1. 优先使用 ai_result/skip_reason 中的 reason
            # 2. 其次从 skip_reason 中提取（当 skip_reason 格式为 "AI判定不匹配: xxx"）
            # 3. 最后回退到 self._last_ai_result
            ai_reason = ai.get("reason", "")
            if not ai_reason and skip_reason and "AI判定不匹配" in skip_reason:
                # skip_reason 格式: "AI判定不匹配: 具体原因..."，提取冒号后的部分
                ai_reason = skip_reason
            emit_data = {
                "job_name": job.get("job_name", ""),
                "company": job.get("company", "") or job.get("company_location", ""),
                "salary": job.get("salary", ""),
                "status": status,
                "ai_score": ai.get("score", 0),
                "ai_reason": ai_reason,
                "ai_match": ai.get("is_match", False),
                "greeting": job.get("_actual_greeting_sent", "")[:60],
                "url": job.get("url", ""),
                "skip_reason": skip_reason or job.get("_last_skip_reason", ""),
                "is_skipped": status in ("skip", "ai_skip", "already", "error"),
                # 前端按账号切记录，实时推送的行也要带账号，否则切到账号2
                # 时新推送的行情会串进主账号的表格
                "account_index": self.account_index,
                "account_name": self._account_label(),
            }
            self._greet_event_cb(emit_data)
        except Exception:
            pass

    def _record_greet(
        self,
        job: dict,
        is_greeted: bool = False,
        is_skipped: bool = False,
        skip_reason: str = "",
        actual_greeting_sent: str = "",
        status: str = "",
    ):
        """创建并保存一条打招呼/AI分析记录。

        从 self._last_ai_* 属性中获取 AI 分析的完整信息。

        Args:
            job: 岗位信息字典
            is_greeted: 是否成功打招呼
            is_skipped: 是否跳过
            skip_reason: 跳过原因
            actual_greeting_sent: 实际发送的打招呼语
            status: 状态 (pending/applied/skipped/failed)，留空则自动推导
        """
        try:
            ai_result = self._last_ai_result or {}
            # 推导 status（若调用方未指定）
            if not status:
                if is_greeted:
                    status = "applied"
                elif is_skipped:
                    reason = skip_reason or ""
                    if any(kw in reason for kw in ("失败", "异常", "错误")):
                        status = "failed"
                    else:
                        status = "skipped"
                else:
                    status = "pending"
            # 打招呼语：优先实际发送的，其次 AI 建议的，最后当前配置的
            greeting_message = (
                actual_greeting_sent
                or ai_result.get("suggested_greeting", "")
                or self._greeting_message
            )
            record = GreetRecord(
                job_name=job.get("job_name", ""),
                job_url=job.get("url", ""),
                ai_error=bool(ai_result.get("ai_error")),
                ai_duration_ms=self._last_ai_duration_ms,
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
                account_name=self._account_label(),
                account_index=self.account_index,
                status=status,
                greeting_message=greeting_message,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
            success, fail_reason = self._apply_job(job_info)
            if success:
                self.applied_count += 1
                self._log("SUCCESS", f"✅ 已投递: {job_name}")
                # _apply_job_inner 在点发送那一刻已经即时记过，这里再记一次
                # 同一岗位就会出现两条记录
                if not job_info.get("_recorded"):
                    self._emit_greet_event(job_info, "success")
                    self._record_greet(
                        job_info, is_greeted=True,
                        actual_greeting_sent=job_info.get("_actual_greeting_sent", ""),
                    )
            else:
                self.skipped_count += 1
                self._log("WARN", f"⏭️ 跳过: {job_name}（原因: {fail_reason}）")
                self._emit_greet_event(job_info, "skip", skip_reason=fail_reason or "投递失败-原因未知")
                self._record_greet(job_info, is_skipped=True, skip_reason=fail_reason or "投递失败-原因未知")
            self._report_progress()
            return success
        except Exception as e:
            self._log("WARN", f"发送打招呼异常: {e}")
            self.skipped_count += 1
            self._emit_greet_event(job_info, "error", skip_reason=f"发送异常: {e}")
            self._report_progress()
            self._record_greet(job_info, is_skipped=True, skip_reason=f"发送异常: {e}")
            return False

    # ── 内部运行逻辑 ──


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


    def _wait_for_login(self) -> bool:
        """等待用户手动登录。"""
        # 必须先 clear：这个 event 只在 confirm_login 里 set 过、从不复位，
        # 人工登录过一次之后这里就会永远立刻返回，真掉登录时变成 300 秒空转
        self._login_event.clear()
        if not self._login_event.wait(timeout=self._login_wait_timeout):
            return False
        self._random_delay(2, 5)
        return self.check_login()

    def _build_search_url(self, query: str, city: str) -> str:
        """构建搜索 URL。"""
        from urllib.parse import quote
        city_code = self._get_city_id(city) if city else ""
        self._log("INFO", f"城市: {city}, 编码: {city_code}")
        encoded_query = quote(query, safe="")
        if city_code:
            return f"https://www.zhipin.com/web/geek/jobs?query={encoded_query}&city={city_code}&industry=&position="
        else:
            return f"https://www.zhipin.com/web/geek/jobs?query={encoded_query}&industry=&position="

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

        # ── 反爬解码：BOSS直聘用 Unicode 私用区字符（E030-E039）代替数字 ──
        # \ue030→0, \ue031→1, ..., \ue039→9；其他私用区字符（E000-F8FF）跳过
        def _decode_anti_scrape(text):
            """将BOSS直聘的反爬Unicode字符映射回数字。"""
            if not text:
                return text
            result = []
            for ch in text:
                code = ord(ch)
                if 0xe030 <= code <= 0xe039:
                    result.append(str(code - 0xe030))
                elif 0xe000 <= code <= 0xf8ff:
                    # 其他私用区字符直接跳过
                    continue
                else:
                    result.append(ch)
            return ''.join(result)

        processed_jobs = []

        # ── 逆向结果：用精确 CSS 选择器分别提取每个字段 ──
        # 真实结构：div.job-card-wrap > li.job-card-box >
        #   div.job-info > div.job-title > a.job-name + span.job-salary
        #            > ul.tag-list > li (经验/学历)
        #   div.job-card-footer > a.boss-info > span.boss-name (公司名)
        #                      > span.company-location (地区)
        # 关键：岗位名(.job-name)是干净的，反爬字符只在薪资(.job-salary)里。
        # 旧代码用 rec-job-list.texts() 整体解析，把岗位名和薪资合并到同一段，
        # 导致正则无法分离，数字被混入岗位名，薪资只剩"K"。
        job_cards = (
            instance.eles(".job-card-wrap", timeout=5)
            or instance.eles("li.job-card-box", timeout=3)
            or instance.eles(".job-card-wrapper", timeout=3)
            or instance.eles(".job-card-left", timeout=3)
        )
        self._log("INFO", f"找到 {len(job_cards)} 个岗位卡片(.job-card-wrap)")

        if job_cards:
            for idx, card in enumerate(job_cards):
                try:
                    # 岗位名：a.job-name（干净，无反爬字符）
                    name_el = card.ele(".job-name", timeout=1)
                    job_name = _decode_anti_scrape(name_el.text if name_el else "")

                    # 薪资：span.job-salary（反爬Unicode，必须解码）
                    sal_el = card.ele(".job-salary", timeout=1) or card.ele(".salary", timeout=1)
                    salary = _decode_anti_scrape(sal_el.text if sal_el else "")

                    # 标签：.tag-list li（经验、学历等）
                    tag_els = card.eles(".tag-list li", timeout=1)
                    tags = [_decode_anti_scrape(t.text).strip() for t in tag_els if t.text]
                    experience = tags[0] if len(tags) > 0 else ""
                    education = tags[1] if len(tags) > 1 else ""

                    # 公司名：.boss-name（逆向发现不是 .company-name）
                    comp_el = card.ele(".boss-name", timeout=1) or card.ele(".company-name", timeout=1)
                    company = _decode_anti_scrape(comp_el.text if comp_el else "")

                    # 地区：.company-location（逆向发现不是 .job-area）
                    area_el = card.ele(".company-location", timeout=1) or card.ele(".job-area", timeout=1)
                    location = _decode_anti_scrape(area_el.text if area_el else "").strip()

                    # URL：优先从 .job-name 的 href 获取
                    url = ""
                    if name_el:
                        href = name_el.attr("href")
                        if href:
                            url = "https://www.zhipin.com" + href if href.startswith("/") else href
                    if not url:
                        url = full_job_urls[idx] if idx < len(full_job_urls) else ""

                    # 兜底：精确选择器都没拿到时，回退到 texts() 按\n分割
                    if not job_name and not salary and not company:
                        card_texts = card.texts()
                        if card_texts:
                            parts = [_decode_anti_scrape(p) for p in card_texts[0].split("\n")]
                            if parts:
                                first_part = parts[0]
                                salary_pattern = r'(\d+\D{1,2}\d+[Kk]·?\d*薪?|\d+\D{1,2}\d+元/[月天小时]|\d+\D{1,2}\d+[Kk]|\d+K·?\d*薪?)'
                                salary_match = re.search(salary_pattern, first_part)
                                if salary_match:
                                    job_name = first_part[:salary_match.start()].strip()
                                    salary = salary_match.group()
                                else:
                                    job_name = first_part.strip()
                                if len(parts) > 1 and not experience:
                                    experience = parts[1]
                                if len(parts) > 2 and not education:
                                    education = parts[2]
                                if len(parts) > 3 and not company:
                                    company_location_raw = parts[3]
                                    if "·" in company_location_raw:
                                        dot_parts = company_location_raw.split("·")
                                        first_segment = dot_parts[0].strip()
                                        if " " in first_segment:
                                            sp = first_segment.rsplit(" ", 1)
                                            company = sp[0].strip()
                                            location = sp[1].strip() + "·" + "·".join(dot_parts[1:])
                                        else:
                                            company = first_segment
                                            location = "·".join(dot_parts[1:])
                                    elif " " in company_location_raw:
                                        sp = company_location_raw.rsplit(" ", 1)
                                        company = sp[0].strip()
                                        location = sp[1].strip()
                                    else:
                                        company = company_location_raw.strip()

                    # 清理岗位名末尾的破折号
                    job_name = job_name.rstrip("-–—").strip()

                    if idx < 3:
                        self._log("DEBUG", f"解析岗位[{idx}]: name={job_name}, salary={salary}, company={company}, location={location}")

                    processed_jobs.append({
                        "job_name": job_name,
                        "salary": salary,
                        "experience": experience,
                        "education": education,
                        "company": company,
                        "company_location": ((company + " " + location).strip()) if (company or location) else "",
                        "location": location,
                        "url": url,
                        "query": self._query,
                    })
                except Exception as e:
                    self._log("WARN", f"解析卡片 {idx} 失败: {e}")
                    # 失败时用链接兜底
                    if idx < len(full_job_urls):
                        processed_jobs.append({
                            "job_name": "", "salary": "", "url": full_job_urls[idx],
                            "query": self._query,
                        })
        else:
            self._log("INFO", "未找到岗位卡片，回退到 rec-job-list 文本解析")
            rec_list_ele = instance.ele(SELECTOR_REC_JOB_LIST, timeout=3)
            if rec_list_ele:
                job_name_list = rec_list_ele.texts()
                self._log("INFO", f"从 rec-job-list 解析出 {len(job_name_list)} 条文本")
                for idx, job_str in enumerate(job_name_list):
                    parts = job_str.split("\n")
                    if len(parts) < 4:
                        continue
                    parts = [_decode_anti_scrape(p) for p in parts]
                    first_part = parts[0]
                    if idx < 3:
                        self._log("DEBUG", f"解码后文本[{idx}]: parts={parts}")

                    salary_pattern = r'(\d+\D{1,2}\d+[Kk]·?\d*薪?|\d+\D{1,2}\d+元/[月天小时]|\d+\D{1,2}\d+[Kk]|\d+K·?\d*薪?)'
                    salary_match = re.search(salary_pattern, first_part)
                    if salary_match:
                        job_name = first_part[:salary_match.start()].strip()
                        salary = salary_match.group()
                    else:
                        job_name = first_part.strip()
                        salary = ""
                        if len(parts) > 1:
                            second_part = parts[1].strip()
                            salary_match2 = re.search(salary_pattern, second_part)
                            if salary_match2:
                                salary = salary_match2.group()
                            elif second_part and ("K" in second_part or "元" in second_part):
                                salary = second_part

                    job_name = job_name.rstrip("-–—").strip()
                    company_location = parts[3] if len(parts) > 3 else ""
                    company = ""
                    location = ""
                    if "·" in company_location:
                        dot_parts = company_location.split("·")
                        first_segment = dot_parts[0].strip()
                        if " " in first_segment:
                            sp = first_segment.rsplit(" ", 1)
                            company = sp[0].strip()
                            location = sp[1].strip() + "·" + "·".join(dot_parts[1:])
                        else:
                            company = first_segment
                            location = "·".join(dot_parts[1:])
                    elif " " in company_location:
                        sp = company_location.rsplit(" ", 1)
                        company = sp[0].strip()
                        location = sp[1].strip()
                    else:
                        company = company_location.strip()

                    processed_jobs.append({
                        "job_name": job_name,
                        "salary": salary,
                        "experience": parts[1] if len(parts) > 1 else "",
                        "education": parts[2] if len(parts) > 2 else "",
                        "company": company,
                        "company_location": company_location,
                        "location": location,
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
                    custom_filter_keywords=self._ai_custom_filter_keywords,
                    custom_scoring_prompt=self._ai_custom_scoring_prompt,
                    skip_unhealthy=self._ai_skip_unhealthy,
                    analyze_max_tokens=self._analyze_max_tokens,
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
            # 分析器都建不起来同样属于「AI 不可用」，不能当成不匹配把岗位全丢掉
            self._last_ai_duration_ms = 0
            return {"score": 50, "is_match": True, "ai_error": True,
                    "reason": "AI 分析器初始化失败，按默认通过", "suggested_greeting": ""}, 0
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
            self._last_ai_duration_ms = int(duration * 1000)
            self._log("INFO", f"🤖 AI 匹配度: {score}/100 ({duration:.1f}s) —— {result.get('reason', '')[:80]}")
            if result.get("ai_error"):
                # AI 没给出判断（接口全挂/无法解析）≠ AI 判定不匹配，按默认话术放行
                self._log("WARN", "⚠️ AI 未能给出判断，本轮按默认通过继续打招呼")
                return result, duration
            return (result, duration) if (is_match and score >= self._ai_threshold) else (None, duration)
        except Exception as e:
            self._log("WARN", f"AI 分析异常，按通过处理: {e}")
            self._last_ai_result = {"ai_error": True, "reason": f"AI 分析异常: {e}"}
            self._last_ai_duration_ms = 0
            return {"score": 50, "is_match": True, "ai_error": True,
                    "reason": f"AI 分析异常: {e}", "suggested_greeting": ""}, 0


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

    def _apply_job(self, job: dict, _disconnect_retry: int = 0):
        """投递一个岗位。

        Returns:
            (success, fail_reason) 元组：
            - 成功: (True, "")
            - 失败: (False, 具体失败原因字符串)
        """
        with self._apply_lock:
            return self._apply_job_inner(job, _disconnect_retry)

    def _apply_job_inner(self, job: dict, _disconnect_retry: int = 0):
        """实际投递逻辑（内部方法）。

        Returns:
            (success, fail_reason) 元组：
            - 成功: (True, "")
            - 失败: (False, 具体失败原因字符串)
        """
        if not self.running:
            return False, "运行已停止"
        url = job.get("url", "")
        if not url:
            return False, "岗位URL为空"

        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "浏览器未启动")
            return False, "浏览器未启动"

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
                    return False, "页面断开且恢复失败"

        try:
            message_sent = False  # 跟踪消息是否已发送
            # ── 1. 导航到岗位详情页 ──
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "导航前页面已断开，尝试恢复...")
                if not self._handle_disconnect():
                    return False, "导航前页面断开且恢复失败"
                instance = self.browser_manager.get_instance()

            for _retry in range(self._retry_max_attempts):
                try:
                    self._log("INFO", f"导航到: {url}")
                    # json.dumps 生成的是转义好的 JS 字符串字面量：岗位 URL 里出现
                    # 单引号时手工拼 '...' 会截断语句，等于把页面 DOM 内容当代码执行
                    instance.run_js(f"window.location.href = {json.dumps(url)}")
                    self._random_delay(3, 6)
                    self._log("INFO", f"导航后URL: {instance.url}")
                    break
                except Exception as _e:
                    self._log("WARN", f"页面加载重试: {_e}")
                    self._random_delay(2, 4)
            else:
                self._log("WARN", "页面加载失败，跳过此岗位")
                return False, "页面加载失败"

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
                        return False, "登录超时"
                    self._save_cookies()
                    self._log("SUCCESS", "登录成功")
                    try:
                        instance.get(url)
                        self._random_delay(3, 6)
                    except Exception:
                        return False, "登录后重新访问岗位失败"
            except Exception:
                self._log("WARN", "页面断开")
                return False, "页面断开"

            # ── 2. 查找沟通按钮 ──
            chat_btn = self._find_chat_button(timeout=8)
            if chat_btn is None:
                self._log("WARN", "未找到沟通按钮")
                return False, "未找到沟通按钮"

            btn_text = chat_btn.text
            if "继续沟通" in btn_text:
                self._log("INFO", "该岗位之前已投递过（继续沟通），跳过")
                return False, "该岗位之前已投递过（继续沟通）"

            # ── 3. 获取 JD 信息 ──
            job_description = ""
            job_requirements = ""
            try:
                job_desc_elem = instance.ele(".job-sec-text", timeout=3)
                if job_desc_elem:
                    job_description = job_desc_elem.text
                    self._log("INFO", "岗位描述: " + job_description[:100] + "...")
            except Exception as e:
                self._log("DEBUG", f"读取岗位描述失败: {e}")
            try:
                req_elem = instance.ele(".requirements", timeout=2)
                if req_elem:
                    job_requirements = req_elem.text
            except Exception as e:
                self._log("DEBUG", f"读取任职要求失败: {e}")
            if not job_description and not job_requirements:
                # JD 是 AI 判分的主要依据，取不到时只凭标题/薪资很容易判成"不匹配"，
                # 事后翻日志必须能看出是这一类原因，而不是 AI 乱打分
                self._log("WARN", f"⚠️ 详情页未取到 JD（{job.get('job_name', '')}），"
                                  f"AI 仅按标题/薪资判分，跳过原因可能失真")
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
                return False, "未找到沟通按钮（点击阶段）"
            self._log("INFO", f"找到沟通按钮，文本: {chat_btn.text}")
            # 记录点击前的标签页ID集合，用于识别新打开的标签页
            browser = instance._get_browser() if hasattr(instance, '_get_browser') else None
            pre_tab_ids = set(browser.tab_ids) if browser else set()
            chat_btn.click()
            self._log("INFO", "已点击沟通按钮，等待输入框...")
            # 等待聊天窗口加载（参考原项目auto_boss: timeout=10秒）
            # 关键修复：BOSS直聘点击"立即沟通"后聊天窗口为页面内弹出层（popup），
            # 弹出层加载比新标签页慢，需要更长等待时间。从5-8秒增加到8-12秒。
            self._random_delay(8, 12)

            # 关键修复：不依赖弹窗容器检测，直接尝试查找输入框。
            # BOSS直聘的弹窗可能用各种 class 名，硬编码检测列表容易漏判。
            # 弹窗检测只作为辅助日志，不影响后续输入框查找流程。
            # 如果能找到输入框，说明弹窗已弹出；找不到再检查风控/验证弹窗。
            chat_popup_selectors = [
                ".chat-container", ".chat-popup", ".chat-modal", ".drawer",
                ".modal-content", ".message-input", "#chat-input", ".chat-input",
                ".input-area", ".chat-footer",
            ]
            popup_detected = False
            for popup_sel in chat_popup_selectors:
                try:
                    popup = instance.ele(popup_sel, timeout=2)
                    if popup:
                        self._log("DEBUG", f"检测到聊天弹窗容器: {popup_sel}")
                        popup_detected = True
                        break
                except Exception:
                    pass
            if not popup_detected:
                # 辅助日志：未检测到弹窗容器，但不中断流程，继续尝试查找输入框
                self._log("INFO", "未检测到聊天弹窗容器（不影响流程，将继续查找输入框）")
                # 检查是否有风控/验证弹窗（仅记录日志，不中断）
                for risk_sel in [".error-tip", ".verify-modal", ".security-tip", ".risk-modal", ".captcha"]:
                    try:
                        risk_elem = instance.ele(risk_sel, timeout=1)
                        if risk_elem:
                            self._log("WARN", f"检测到风控/验证弹窗: {risk_sel}，文本: {risk_elem.text[:100] if risk_elem.text else ''}")
                    except Exception:
                        pass

            # 尝试获取新打开的聊天标签页（BOSS 点"沟通"后通常新开标签页）
            # 关键修复：新标签页严格通过 browser_manager.get_greet_chat_tab() 管理，
            # 与回复引擎的 _chat_tab 严格区分，避免抢占。
            chat_tab = None
            if browser:
                try:
                    # 优先通过 tab_ids 差检新打开的标签页（比 latest_tab 更可靠）
                    # latest_tab 返回最新激活的标签页，但可能恰好是回复引擎的 _chat_tab
                    post_tab_ids = set(browser.tab_ids)
                    new_tab_ids = post_tab_ids - pre_tab_ids
                    # 先检查新打开的标签页
                    for tid in new_tab_ids:
                        try:
                            t = browser.get_tab(tid)
                            t_url = t.url or ""
                            self._log("DEBUG", f"新标签页: {t_url}")
                            if "chat" in t_url or "message" in t_url:
                                chat_tab = t
                                self._log("INFO", f"识别到新打开的聊天标签页: {t_url}")
                                break
                        except Exception:
                            pass
                    # 关键修复：删除 latest_tab 回退逻辑。
                    # latest_tab 返回最近激活的标签页，如果回复引擎刚操作过 _chat_tab，
                    # latest_tab 就会返回回复引擎的聊天标签页，导致打招呼引擎在回复标签页上发消息。
                    # 如果 tab_ids 差集没找到新标签页，chat_tab 保持 None，
                    # 后续会尝试在当前搜索标签页(instance)上查找输入框（BOSS可能in-page导航）。
                    if chat_tab is None:
                        self._log("DEBUG", "tab_ids 差集未找到新聊天标签页，将检查当前搜索标签页是否in-page导航")
                except Exception as e:
                    self._log("DEBUG", f"获取新打开标签页失败: {e}")

            # 将识别到的聊天标签页注册到 browser_manager._greet_chat_tab
            # 这样后续 close_greet_chat_tab() 可以精确关闭它，不影响回复引擎的 _chat_tab
            if chat_tab is not None and self.browser_manager is not None:
                try:
                    from boss_bot.browser_launcher import BrowserInstance, _IS_MACOS
                    # 包装为 BrowserInstance 并注册到 browser_manager
                    self.browser_manager._greet_chat_tab = BrowserInstance(
                        chrome_page=chat_tab if not _IS_MACOS else None,
                        chromium=browser if _IS_MACOS else None,
                        tab=chat_tab if _IS_MACOS else None,
                    )
                    self._log("DEBUG", "已将聊天标签页注册到 browser_manager._greet_chat_tab")
                except Exception as e:
                    self._log("DEBUG", f"注册聊天标签页失败: {e}")

            # ── 5. 输入消息 ──
            # 优先级：AI定制 > 岗位配置 > 默认模板
            greeting = self._greeting_message
            if not greeting:
                acc = self._account()
                if acc and acc.jobs:
                    greeting = acc.jobs[0].greeting_message
            if not greeting:
                from boss_bot.unified_config import DEFAULT_GREETING
                greeting = DEFAULT_GREETING
                self._greeting_source = "默认模板"
            # 保存实际发送的打招呼语供 GreetRecord 记录使用
            job["_actual_greeting_sent"] = greeting
            self._log("INFO", f"打招呼语来源: {self._greeting_source}, 内容: {greeting[:50]}...")
            # 等待聊天窗口加载
            self._random_delay(3, 5)

            # 检查是否有弹窗阻止（简历弹窗等），自动关闭
            try:
                for popup_sel in [".panel-resume", ".sentence-popover", ".dialog-content", ".modal-content", ".resume-guide"]:
                    popup = instance.ele(popup_sel, timeout=1)
                    if popup:
                        self._log("WARN", f"检测到弹窗: {popup_sel}，尝试关闭...")
                        try:
                            close = instance.ele(".icon-close", timeout=1)
                            if close:
                                close.click()
                                self._random_delay(1, 2)
                        except Exception:
                            pass
            except Exception:
                pass

            # ── 查找聊天输入框 ──
            # 关键修复：BOSS直聘点击"立即沟通"后，聊天窗口是**页面内弹出层（popup）**，不是新标签页。
            # 查找顺序调整为：①当前页面 → ②当前页面iframe → ③新标签页 → ④遍历所有标签页
            # 严格参考原项目 auto_boss: self.dp.ele(".input-area", timeout=10)
            input_area = None
            greet_chat_instance = None  # 打招呼专用的临时聊天 BrowserInstance

            # 统一的输入框选择器列表（按优先级排序）
            # BOSS直聘聊天页面输入框实际是 #chat-input（contenteditable div，class=chat-input）
            # 扩展选择器覆盖各种可能的聊天输入框形态
            chat_input_selectors = [
                "#chat-input",
                ".chat-input",
                ".input-area",
                'textarea[placeholder*="回复"]',
                'textarea[placeholder*="输入"]',
                ".message-input textarea",
                ".chat-footer textarea",
                "tag:textarea",
                "[contenteditable=true]",
                'div[contenteditable="true"]',
            ]

            # 关键修复：增加重试机制（次数按 greet.retry.max_attempts 配置，递增等待）
            # 日志显示"未找到输入框"时URL还在job_detail页面，
            # 说明弹窗可能延迟弹出，需要重试查找
            _input_attempts = max(1, self._retry_max_attempts)
            for _input_retry in range(_input_attempts):
                if input_area:
                    break
                if not self.running:
                    return False, "运行已停止"
                # 验证页一直等不到输入框，重试循环会空转几分钟；认出来就直接交给人工
                if self._on_captcha_page(instance):
                    return False, ("BOSS 弹出人机验证，需要人工在浏览器窗口完成"
                                   "（超过 60 秒未处理会自动跳过）")
                if _input_retry > 0:
                    _retry_wait = 3 + _input_retry * 2  # 第2次等5秒，第3次等7秒
                    self._log("INFO", f"输入框查找重试 {_input_retry+1}/{_input_attempts}，等待 {_retry_wait} 秒...")
                    self._interruptible_sleep(_retry_wait)

                # ① 优先在当前页面查找输入框（BOSS点击沟通后通常在当前页面弹出聊天窗口）
                # 关键修复2：参考原项目auto_boss，点击沟通后聊天窗口在当前页面弹出（不新开标签页），
                # URL不变，所以不检查URL是否含"chat"，直接在当前页面查找输入框
                try:
                    for sel in chat_input_selectors:
                        try:
                            input_area = instance.ele(sel, timeout=3)
                            if input_area:
                                self._log("INFO", f"在当前页面找到输入框: {sel}")
                                greet_chat_instance = instance
                                break
                        except Exception:
                            pass
                except Exception:
                    pass

                # ② 当前页面没找到，遍历当前页面的所有iframe查找
                # 关键修复：BOSS直聘聊天输入框可能在iframe中，日志显示"发现 2 个iframe"
                if not input_area:
                    try:
                        iframes = instance.eles("tag:iframe", timeout=2)
                        if iframes:
                            self._log("INFO", f"当前页面发现 {len(iframes)} 个iframe，尝试在iframe中查找输入框")
                            for iframe in iframes:
                                try:
                                    for sel in chat_input_selectors:
                                        try:
                                            input_area = iframe.ele(sel, timeout=3)
                                            if input_area:
                                                self._log("INFO", f"在当前页面iframe中找到输入框: {sel}")
                                                greet_chat_instance = instance
                                                break
                                        except Exception:
                                            pass
                                    if input_area:
                                        break
                                except Exception:
                                    pass
                    except Exception as e:
                        self._log("DEBUG", f"当前页面iframe查找失败: {e}")

                # ③ 当前页面和iframe都没找到，再在新打开的聊天标签页中查找（如果有新标签页）
                # 关键修复：不修改 instance._page/_tab（那会破坏搜索标签页），
                # 而是创建一个独立的 BrowserInstance 包装 chat_tab 用于操作
                if not input_area and chat_tab:
                    try:
                        from boss_bot.browser_launcher import BrowserInstance, _IS_MACOS
                        greet_chat_instance = BrowserInstance(
                            chrome_page=chat_tab if not _IS_MACOS else None,
                            chromium=browser if _IS_MACOS else None,
                            tab=chat_tab if _IS_MACOS else None,
                        )
                        for sel in chat_input_selectors:
                            try:
                                input_area = greet_chat_instance.ele(sel, timeout=5)
                                if input_area:
                                    self._log("INFO", f"在聊天标签页找到输入框: {sel}")
                                    break
                            except Exception:
                                pass
                        if not input_area:
                            # 重置 greet_chat_instance，避免误用未找到输入框的实例
                            greet_chat_instance = None
                    except Exception as e:
                        self._log("DEBUG", f"在聊天标签页查找输入框失败: {e}")
                        greet_chat_instance = None

                # ④ 最后兜底：遍历所有标签页查找（不过滤URL）
                # 关键：排除回复引擎专用的 _chat_tab，避免抢占
                if not input_area:
                    try:
                        browser = instance._get_browser() if hasattr(instance, '_get_browser') else None
                        if browser:
                            all_tabs = browser.tab_ids
                            # 获取回复引擎 _chat_tab 的 tab_id，用于排除
                            # 关键修复：使用 browser_manager.get_reply_tab_id() 方法替代内联获取，
                            # 统一排除逻辑，避免多处重复代码导致不一致
                            reply_chat_tab_id = None
                            if self.browser_manager is not None:
                                reply_chat_tab_id = self.browser_manager.get_reply_tab_id()
                            if reply_chat_tab_id:
                                self._log("DEBUG", f"回复引擎专用标签页 tab_id: {reply_chat_tab_id}，遍历时将排除")
                            if len(all_tabs) > 1:
                                self._log("INFO", f"当前页面未找到输入框，遍历 {len(all_tabs)} 个标签页（排除回复引擎标签页）")
                                for tab_id in all_tabs:
                                    # 跳过回复引擎的 _chat_tab，避免打招呼引擎抢占
                                    if reply_chat_tab_id and tab_id == reply_chat_tab_id:
                                        self._log("DEBUG", "  跳过回复引擎专用标签页")
                                        continue
                                    try:
                                        tab = browser.get_tab(tab_id)
                                        tab_url = tab.url
                                        self._log("DEBUG", f"  检查标签页: {tab_url}")
                                        # 不过滤URL，在每个标签页中尝试查找输入框
                                        for sel in chat_input_selectors:
                                            try:
                                                input_area = tab.ele(sel, timeout=3)
                                                if input_area:
                                                    # 守护日志：确认找到输入框的标签页不是回复引擎的标签页
                                                    if reply_chat_tab_id:
                                                        current_tab_id = getattr(tab, 'tab_id', None) or getattr(tab, '_tab_id', None)
                                                        if current_tab_id == reply_chat_tab_id:
                                                            self._log("ERROR", "严重BUG：打招呼引擎试图使用回复引擎标签页！跳过此标签页。")
                                                            input_area = None
                                                            continue
                                                    self._log("INFO", f"在标签页 {tab_url} 中找到输入框: {sel}")
                                                    # 包装为 greet_chat_instance，不修改 instance
                                                    try:
                                                        from boss_bot.browser_launcher import BrowserInstance, _IS_MACOS
                                                        greet_chat_instance = BrowserInstance(
                                                            chrome_page=tab if not _IS_MACOS else None,
                                                            chromium=browser if _IS_MACOS else None,
                                                            tab=tab if _IS_MACOS else None,
                                                        )
                                                        # 同步注册到 browser_manager
                                                        if self.browser_manager is not None:
                                                            self.browser_manager._greet_chat_tab = greet_chat_instance
                                                    except Exception:
                                                        pass
                                                    break
                                            except Exception:
                                                pass
                                        if input_area:
                                            break
                                    except Exception:
                                        pass
                    except Exception as e:
                        self._log("DEBUG", f"标签页遍历失败: {e}")

            if not input_area:
                snap = {"url": "", "inputs": [], "chat_elements": [], "error": ""}
                try:
                    snap = json.loads(instance.run_js(CHAT_SNAPSHOT_JS) or "{}")
                except Exception as e:
                    snap["error"] = str(e)
                reason = chat_failure_reason(snap)
                self._log("WARN", f"未找到输入框｜{reason}")
                self._log("WARN", f"  现场 url={str(snap.get('url'))[:80]} "
                                  f"抽屉元素={snap.get('chat_elements') or '无'} "
                                  f"input样式={list(snap.get('inputs') or [])[:8]}")
                if "登录" in reason:
                    self._log("WARN", "登录态已失效，之后每个岗位都会卡在同一个地方，"
                                      "请先在浏览器窗口里重新登录 BOSS")
                return False, reason
            self._log("INFO", "找到输入框，输入消息...")
            input_area.input(greeting)
            self._log("INFO", "消息已输入")

            # ── 6. 点击发送 ──
            # 关键修复：发送按钮在聊天标签页中查找，不在搜索标签页(instance)中
            # 优先使用 greet_chat_instance（打招呼专用临时标签页），回退到 instance
            send_search_instance = greet_chat_instance if greet_chat_instance else instance
            message_sent = False
            try:
                # 尝试多个发送按钮选择器
                send_btn = None
                for send_sel in [".btn-send", ".btn-v2.btn-sure-v2.btn-send", ".send-message", "tag:button@@type=submit", ".chat-send"]:
                    try:
                        send_btn = send_search_instance.ele(send_sel, timeout=3)
                        if send_btn:
                            self._log("INFO", f"找到发送按钮: {send_sel}")
                            break
                    except Exception:
                        pass
                if send_btn:
                    send_btn.click()
                    message_sent = True
                else:
                    self._log("WARN", "未找到发送按钮，尝试按回车发送")
                    input_area.input("\n")
                    message_sent = True
            except Exception as send_e:
                self._log("WARN", f"点击发送按钮失败: {send_e}")
                # 尝试按回车发送
                try:
                    input_area.input("\n")
                    message_sent = True
                except Exception:
                    pass
            self._random_delay(1, 2)

            # 消息已经发出去了，先落库+推前端：下面还有图片上传、关弹窗、
            # 关临时标签页，全跑完要 5~30 秒，那期间界面看不到投递结果
            self._mark_chatted(job)
            self._record_sent_now(job)

            # 发送后检测页面是否断开
            try:
                _ = instance.url
            except Exception:
                self._log("WARN", "发送消息后页面连接断开，但消息可能已发送成功")
                self._mark_chatted(job)
                # 即使页面断开，也要尝试关闭临时聊天标签页
                if self.browser_manager is not None:
                    try:
                        self.browser_manager.close_greet_chat_tab()
                    except Exception:
                        pass
                return True, ""

            # ── 7. 发送图片 ──
            self._send_images_after_message()

            # ── 清理状态 ──
            # 关键修复：在聊天标签页中关闭弹窗，不在搜索标签页中
            try:
                close_btn = send_search_instance.ele(".icon-close", timeout=2)
                if close_btn:
                    close_btn.click()
                    self._random_delay(1, 2)
            except Exception:
                pass

            # ── 关闭打招呼专用的临时聊天标签页，回到搜索标签页 ──
            # 关键修复：通过 browser_manager.close_greet_chat_tab() 精确关闭临时标签页，
            # 不影响回复引擎的 _chat_tab，也不关闭搜索标签页
            if greet_chat_instance is not None and self.browser_manager is not None:
                try:
                    self.browser_manager.close_greet_chat_tab()
                    self._random_delay(1, 2)
                    self._log("INFO", "已关闭打招呼临时聊天标签页，回到搜索标签页")
                except Exception as e:
                    self._log("DEBUG", f"关闭打招呼临时标签页异常: {e}")
            elif greet_chat_instance is not None:
                # 兜底：直接关闭
                try:
                    greet_chat_instance.close_current_tab()
                    self._random_delay(1, 2)
                except Exception:
                    pass

            return True, ""

        except Exception as e:
            self._log("WARN", "发送消息异常: " + str(e))
            import traceback
            self._log("WARN", traceback.format_exc())
            # 异常路径也要清理临时聊天标签页，避免标签页累积
            if self.browser_manager is not None:
                try:
                    self.browser_manager.close_greet_chat_tab()
                except Exception:
                    pass
            # 如果消息已发送但后续步骤出错，仍标记为成功
            if message_sent:
                self._log("WARN", "消息已发送但后续步骤出错，标记为成功")
                self._mark_chatted(job)
                return True, ""
            return False, f"发送消息异常: {e}"

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
            input_area = instance.ele(".input-area", timeout=10)
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

    def _record_sent_now(self, job: dict):
        """投递成功的瞬间就落库 + 推前端，不等收尾动作跑完。

        原来记录要等图片上传、关弹窗、关临时标签页全部做完（5~30 秒）才写，
        界面在这段时间里是"BOSS 上已经投了、记录里还没有"。
        """
        if job.get("_recorded"):
            return
        job["_recorded"] = True
        try:
            self._emit_greet_event(job, "success")
            self._record_greet(job, is_greeted=True,
                               actual_greeting_sent=job.get("_actual_greeting_sent", ""))
        except Exception as e:
            # 即时记录失败不影响投递本身，外层 send_greeting 还会补记一次
            job["_recorded"] = False
            self._log("WARN", f"即时记录投递结果失败，改由收尾路径补记: {e}")

    def _random_delay(self, min_sec: float, max_sec: float):
        """随机延迟（反爬策略）。"""
        if not self.running:
            return
        time.sleep(random.uniform(min_sec, max_sec))

    def _interruptible_sleep(self, seconds: float):
        """可被停止打断的等待。

        投递路径上原来用裸 time.sleep，一次就是 5~7 秒且不看 running，
        点"停止/暂停"要等整个重试循环跑完才生效——最长能拖几分钟。
        """
        deadline = time.time() + max(0.0, seconds)
        while self.running and time.time() < deadline:
            time.sleep(min(0.2, max(0.05, deadline - time.time())))

    def _on_captcha_page(self, instance) -> bool:
        """当前页面是不是 BOSS 的人机验证页。"""
        try:
            from boss_bot.page_handler import CAPTCHA_PROBE_JS, classify_health
            probe = instance.run_js(CAPTCHA_PROBE_JS, as_expr=True)
            return classify_health(instance.url or "", probe) == "captcha"
        except Exception:
            return False


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
                # 前端「我已登录」保存在项目根目录，这里也要能找到
                paths_to_try.append(str(BASE_DIR / cookie_name))
            else:
                paths_to_try.append(cookie_name)
            paths_to_try.append(str(DATA_DIR / "zhipin_cookies.json"))
            paths_to_try.append(str(BASE_DIR / "zhipin_cookies.json"))

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
            cookie_name = self._cookie_file or "zhipin_cookies.json"
            dst = str(resolve_path(cookie_name))
            cookies = instance.cookies()
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            self._log("INFO", f"已保存 Cookie: {dst}")
            # 这里以前还会把本账号的 Cookie 再 copy 一份到公共的 zhipin_cookies.json，
            # 等于把 B 号的登录态盖到 A 号头上，多账号下必须各写各的
        except Exception as e:
            self._log("WARN", f"Cookie 保存失败: {e}")


    # ── 去重管理 ──

    def _load_chatted(self) -> set:
        """读取已沟通岗位集合，进程内缓存，超过 CHATTED_REFRESH_SECONDS 重读一次。

        缓存不能永久有效：另一个账号刚打过的岗位，本账号如果在跑一整晚，
        永远读到自己那份旧快照就会重复招呼同一个 HR。
        """
        now = time.time()
        if self._chatted_cache is None or (now - self._chatted_loaded_at) > self.CHATTED_REFRESH_SECONDS:
            urls = set()
            try:
                if CHATTED_DB_FILE.exists():
                    with open(CHATTED_DB_FILE, "r", encoding="utf-8") as f:
                        urls = set(json.load(f))
            except Exception as e:
                self._log("WARN", f"读取去重库失败，按未沟通过处理: {e}")
                urls = set()
            # 本地已标记但还没落盘成功的，不能因为重读又丢了
            urls |= (self._chatted_cache or set())
            self._chatted_cache = urls
            self._chatted_loaded_at = now
        return self._chatted_cache

    def _is_already_chatted(self, job: dict) -> bool:
        """检查是否已沟通过。"""
        url = job.get("url", "")
        if not url:
            return False
        return url in self._load_chatted()

    def _mark_chatted(self, job: dict):
        """标记岗位为已沟通。

        去重库两个账号共用一份文件（同一个岗位让两个号都打一遍，HR 会收到
        两条一模一样的招呼）。但每个引擎各持一份内存副本，整文件重写会把
        对方刚加的 URL 抹掉 → 下次重复招呼。所以写入前先读盘并集合并。
        """
        url = job.get("url", "")
        if not url:
            return
        if url in self._load_chatted():
            return
        with GreetEngine._chatted_lock:
            try:
                merged = set()
                if CHATTED_DB_FILE.exists():
                    with open(CHATTED_DB_FILE, "r", encoding="utf-8") as f:
                        merged = set(json.load(f))
                merged |= (self._chatted_cache or set())
                merged.add(url)
                write_json_atomic(CHATTED_DB_FILE, sorted(merged))
                self._chatted_cache = merged
            except Exception as e:
                # 写失败必须报出来：静默丢一条就等于下次重复打招呼
                self._log("WARN", f"写入去重库失败，可能重复打招呼: {e}")

    # ── 聊天日志 ──

