# BOSS 直聘智能投递助手

自动投递简历 + AI 筛选岗位 + AI 自动回复。整合 `auto_boss`（打招呼/投递）与 `BOSS-auto-reply-bot`（自动回复）两个项目，多账号并行运行，配 Flask + SocketIO 管理界面。

- 打招呼：按「城市 + 关键词」搜索岗位，进详情页读 JD，AI 判匹配度，达标才发招呼语并可投简历
- 回复：监控未读会话，四级决策（关键词规则 → 意图识别 → AI 生成 → 兜底），面试/薪资/要简历自动应对
- 界面：毛玻璃风格单页，暗/亮双主题，实时日志、投递与回复记录、聊天记录 1:1 还原 BOSS 气泡

> 本工具会真实操作你的 BOSS 账号。先用**演练模式**跑一遍，确认它准备发出去的内容再真发。

## 目录

- [安装](#安装)
- [运行](#运行)
- [功能与指标口径](#功能与指标口径)
- [防骚扰与发送闸门](#防骚扰与发送闸门)
- [AI 接口体检与容灾链](#ai-接口体检与容灾链)
- [多账号隔离](#多账号隔离)
- [配置说明](#配置说明)
- [配置生效范围（重要）](#配置生效范围重要)
- [测试](#测试)
- [反爬与容错](#反爬与容错)
- [故障排查](#故障排查)
- [隐私与版本库](#隐私与版本库)
- [项目结构](#项目结构)

## 安装

环境：Python 3.10+，Windows / macOS / Linux。

```bash
git clone https://github.com/huangwei-gem/boss_bot.git
cd boss_bot
pip install -r requirements.txt      # Flask, Flask-SocketIO, DrissionPage, openai, openpyxl
```

### 浏览器（关键）

BOSS 直聘有风控，**必须使用反检测浏览器**：项目内 `cloakbrowser/chrome.exe`（已 patch `navigator.webdriver` 等检测点）。

```jsonc
// bot_config.json
{
  "browser": {
    "chrome_path": "C:/Users/you/orca/boss_bot/cloakbrowser/chrome.exe",
    "user_data_dir": "",          // 留空则按账号自动分配 browser_data/account_N
    "debug_port": 9222
  }
}
```

用系统 Chrome/Edge 也能跑，但更容易触发「安全验证」页；触发后机器人会自动暂停并通知，等你手动过一次验证。

### 首次登录

```bash
python -m boss_bot --web        # 或双击 start.bat
```

界面点「启动」→ 弹出的浏览器里扫码/手机号登录 → 登录态被自动识别（检测未过期登录 Cookie + 实际访问聊天页双重判定），Cookie 按账号写入 `zhipin_cookies.json` / `zhipin_cookies_N.json`，之后免登录。

## 运行

### Web 界面（推荐）

```bash
python -m boss_bot --web        # 自动开浏览器，访问 http://localhost:5000
```

也可以 `cd flask-version && python app.py`，或一键脚本 `start.bat` / `start.sh`（会检查依赖、端口、项目结构）。

界面能力：启动/停止、按账号启停、暂停/恢复打招呼与回复、实时日志、四个指标卡 + 统计范围切换、投递/回复记录与筛选、聊天记录、Excel 导出、AI 接口体检、配置编辑（含高级设置）。

### 命令行

```bash
python -m boss_bot                      # 全部启用账号，打招呼 + 回复
python -m boss_bot --greet              # 只打招呼
python -m boss_bot --reply              # 只自动回复
python -m boss_bot --account 1          # 只跑账号 2（索引从 0 开始）
python -m boss_bot --dry-run            # 演练：照常搜索/AI 判分，不点发送
python -m boss_bot --headless           # 无头浏览器
python -m boss_bot --config my.json     # 指定配置文件
python -m boss_bot --web                # 起管理界面
```

`Ctrl+C` 会走停止流程（关浏览器、落盘统计）。`--greet` 与 `--reply` 互斥；两个都不加就是两个都开（仍受配置文件里的总开关约束）。

### 环境变量

命令行开关通过环境变量下传，因为主循环每轮热重载会重新读配置文件，只改内存对象会被覆盖。

| 变量 | 作用 |
|------|------|
| `BOSS_BOT_ONLY_ENGINE=greet\|reply` | 单侧运行（`--greet/--reply` 走的就是它） |
| `BOSS_BOT_DRY_RUN=1` | 演练模式 |
| `BOSS_BOT_CONFIG=path` | 指定 `bot_config.json` |
| `BOSS_BOT_HEADLESS=1` | 无头 |
| `BOSS_BOT_TEST_MODE=1` + `BOSS_BOT_TEST_PAGE=file://...` | 离线 mock 页面（不碰真实站点） |
| `AI_PROVIDERS=k1\|model1\|url1;k2\|...` | 覆盖 AI 接口列表 |
| `AI_SKIP_UNHEALTHY=false` | 关掉"跳过体检不可用接口" |
| `ENABLE_AI=true\|false`、`LOG_LEVEL`、`NOTIFY_WEBHOOK_URL` | 同名字段覆盖 |

## 功能与指标口径

界面顶部四张卡，口径固定如下（可切「全部账号 / 主账号 / 账号2」）：

| 卡片 | 口径 | 说明 |
|------|------|------|
| 累计投递 | 历史打招呼成功次数 | 持久计数器，不受记录文件截断影响 |
| 已投递 | 当天打招呼成功次数 | 小字显示每日上限（默认 150），到点自动暂停，跨零点自动恢复 |
| 接收简历 | 我方成功发出简历的次数 | 按「发送成功」计，不按点击计 |
| 面试数 | 按会话去重的面试邀约数 | 同一 HR 同一岗位只算一次 |

数据落在 `data/metrics.json`（按账号分桶 + 按天），首次运行会从历史记录回填一次。

回复决策链：

| 优先级 | 层 | 说明 |
|--------|----|------|
| 1 | 关键词规则 | 直接匹配，带疑问/否定上下文过滤 |
| 2 | 意图识别 | 邀约面试 > 询问薪资 > 索要简历 > … |
| 3 | AI 生成 | 完整对话历史 + 个人画像动态提示词，OpenAI 兼容接口 |
| 4 | 兜底 | `ai.fail_action` = `default`（回兜底话术）或 `skip`（不回） |

自动回复的 AI **不受**「AI 智能解析」开关控制，那个开关只管打招呼侧的岗位解析——保证句句有回应。

## 防骚扰与发送闸门

**唯一跳过依据**：当前这段对话自己以 HR 的拒绝收尾、且我已经回复过，才会显示「跳过（避免骚扰）」。

不按昵称跨会话判定——BOSS 上「胡女士/杨女士」这类称呼大量重名，跨会话判定会造成大面积误跳过（历史上真误跳过过 434 条，已清理并留备份）。会话文件岗位名与当前对话不一致时，按「同名不同人」处理，只使用页面实时读到的这段对话。

**演练模式**（`dry_run` / `--dry-run` / 界面「演练模式（只筛不发）」）：搜索、进详情、AI 判分、生成回复全部照做，只在最后一下点击前拦住，共 4 个发送点：打招呼、发简历、发文字回复、简历发送失败后的降级文字。日志里会打 `🧪 演练模式 | 本应打招呼: 岗位名 @ 公司（未真实操作）`。不消耗每日上限，也不打扰 HR。

新增发送点时必须在调用前加 `self._dry_run(...)`，`tests/test_unified.py::DryRunGateTest` 会静态扫描 `main_loop.py` 的发送调用并断言每个前面都有闸门。

## AI 接口体检与容灾链

界面「AI 接口列表 → 全部接口体检」会**给每个接口真发一条 4 个字的测试消息**（`只回复四个字：连接成功`），拿到正文才算可用；推理模型 `content` 为空但 `reasoning_content` 有内容也算可用。不可用的会标出具体原因（Key 失效 / 额度用尽 / 模型名不对 / 地址不对 / 超时）。

- 结果落 `data/ai_health.json`；服务启动时若结果缺失或超过 24 小时会自动补测一轮
- 支持全量体检与单接口重测，进度实时推送
- 空 Key、重复接口（同 Key+地址+模型）会单独标出

**容灾链**（打招呼侧岗位判分）：单岗位最多试 4 个接口、最多花 60 秒；失败的接口进冷却表（鉴权/额度类 30 分钟，其它 5 分钟）。默认 `ai.skip_unhealthy = true`：**相对顺序不变**，只跳过体检在 24 小时内明确标记不可用的接口；若体检显示全部不可用则自动不生效（宁可慢，也不能不筛）。

实测对比：22 个接口中 5-7 个可用时，同一个岗位判分从 **61.4 秒（超时后落到"默认通过"，等于没筛）降到 6.1 秒并给出真实评分与理由**。

判分结果缓存 24 小时（`data/ai_cache.json`），但**超时/解析失败的结果不缓存**，否则坏判定会在缓存期内一直复用。

怀疑 AI 没在真筛岗位时：

```bash
python tools/diagnose_ai_parse.py      # 真实调用几个岗位，打印原始响应与解析结论
python tools/ai_health_check.py        # 命令行跑一轮全量体检
```

## 多账号隔离

每个账号一套独立运行数据，主账号（索引 0）沿用原文件名不迁移，其余加 `_account_N`：

| 数据 | 文件 |
|------|------|
| 浏览器实例 / profile | 各自独立端口 + `browser_data/account_N` |
| 登录 Cookie | 账号配置里的 `cookie_file`，回落到全局 |
| 会话状态（防重复回复/发简历）、人工接管 | `bot_state.json` / `bot_state_account_N.json` |
| 统计 | `bot_stats.json` / `bot_stats_account_N.json` |
| 聊天记录 | `messages/` / `messages/aN_*.json` |
| 打招呼话术、简历图片、消息间隔、岗位任务 | 各自 `accounts[N]` |
| 每日上限计数 | 按 `account_index` 过滤 |
| 自进化数据 | `data/evolution_data.json` / `..._account_N.json`（质量数据同样分文件） |

**全局共享**（有意为之）：AI 配置与接口列表、回复规则/模板、个人画像、关键词。已投递 URL 去重表 `data/chatted_jobs.json` 也是共享的——同一个岗位不让两个号重复投。

## 配置说明

`bot_config.json`（顶层键）：

| 字段 | 说明 |
|------|------|
| `browser` | headless、视口、超时、代理、`chrome_path`、`user_data_dir`、`debug_port` |
| `login` | `wait_timeout`、`cookie_file`、`clear_cookies_on_failure`（登录态失效时删掉本地 Cookie，下次走完整登录） |
| `rate_limit` | 打招呼每小时/每天上限（`max_per_day` 默认 150，界面小字读的就是它） |
| `retry` | `max_attempts` 真正被使用：岗位详情导航重试、聊天输入框查找重试 |
| `accounts` | 多账号列表（城市、关键词、翻页数、招呼语、简历图片、间隔、独立 Cookie） |
| `greet.enabled` / `reply.enabled` | 两个总开关 |
| `ai` | `enabled`、`providers`、`match_threshold`、`max_tokens`、`fail_action`、`rate_limit_wait`、`custom_filter_keywords`、`custom_scoring_prompt`、`skip_unhealthy` |
| `reply` | `check_interval`、`context_message_count`、`max_replies_per_hour`、`min_delay`/`max_delay`、`pause_on_important`、`resume_send_once`、`chat_url` |
| `resume` | 简历信息（学校/专业/学位/技能/经验/目标岗位/自我介绍） |
| `templates` / `reply_rules` / `importance_keywords` | 话术模板、关键词规则、重要事件词 |
| `user_profile` | 个人画像（也单独存 `user_profile.json`） |
| `notify` / `log` | Webhook、日志级别与保留天数 |
| `dry_run` | 演练模式 |
| `self_evolve_enabled` | 自进化记录与评估开关 |
| `theme` | 界面主题。它不由 `UnifiedConfig` 建模，但保存配置时会被保留（写盘前读回旧文件补空位） |

前端写入分两个文件：`bot_config.json`（主源）与 `config_overrides.json`（运行时覆盖，话术/规则/关键词/画像）。两者会双写保持一致。

### 自进化

`self_evolve_enabled` 打开后，运行时会记录每条 AI 回复并在 HR 回消息后评估效果（正/负/无响应），数据写入 `data/evolution_data*.json`，界面 `/api/self_evolve/report` 读取。**它不会自动改写你写的话术和规则**——调整规则/模板需要你在接口上显式触发。

## 配置生效范围（重要）

主循环每轮热重载配置，以下改动**不用重启**：AI 接口列表与阈值、自定义筛选词与打分提示词、跳过不可用接口、打招呼频率限制与重试次数、回复延迟/每小时上限/`max_tokens`/`fail_action`/限流等待、两个总开关、演练模式、自进化开关、话术与规则、个人画像。

以下改动**需要重启**（构造时读取）：浏览器路径与 profile、端口、Cookie 文件路径、账号列表增删、简历图片文件、日志目录、通知 Webhook。

界面上每个输入框都对应真实生效的配置项；如果哪天发现某个控件改了没反应，那是 bug，按 [测试](#测试) 里的三端检测复现并提 issue。

## 测试

两套都要跑：单元测试管逻辑，真机浏览器套件管"打开来真的能用"。

```bash
pytest tests/ -q                      # 277 项，约 10 秒，全部离线（不碰真实数据、不联网）
```

真机套件全部使用项目内 `cloakbrowser/chrome.exe`，且**只做读/切/筛/存配置，绝不点发送、打招呼、发简历**：

```bash
python tools/e2e_dashboard.py         # 33 项：界面渲染、指标卡、记录筛选、开关往返、弹窗、主题、断线横幅、无 JS 报错
python tools/verify_dashboard_ui.py   # 14 项：指标卡口径 + AI 体检展示，产出 tools/verify_dashboard.png
python tools/verify_three_way.py      # BOSS 页面 / 后端存储 / 前端显示 三端逐条比对
python tools/e2e_live_boss.py         # 16 项：反爬自检、登录态、会话读取、岗位解析、AI 真实判分耗时
```

`tests/conftest.py` 会把回复记录、打招呼记录、指标文件重定向到临时目录，跑测试不会污染生产数据。

## 反爬与容错

- 随机消息间隔（按账号配置）、UA 轮换、`--disable-blink-features=AutomationControlled`
- 验证码/风控检测 → 暂停对应侧并推送通知；打招呼连续 3 轮搜索为 0 也会暂停提示人工检查
- 回复侧同样做登录态/风控检查（被踢下线不会整轮空转）
- 浏览器断连自动重连：指数退避 2→4→8→16→32→60 秒，重连后按账号重载 Cookie 并重建引擎
- 岗位 URL 通过转义后的 JS 字面量注入，避免 DOM 内容被当代码执行
- 通知：企业微信 / 飞书 / 钉钉 Webhook

## 故障排查

**「AI 已拒绝过」大量误跳过** → 已修复为只看当前会话；历史误判记录已清理，备份在 `data/reply_records.bak_before_purge_*.json`。

**打招呼像卡死 / 岗位没被 AI 筛** → 十有八九是容灾链在等死接口。看日志有没有 `AI 分析异常` / `AI 响应无法解析`，跑 `python tools/diagnose_ai_parse.py`，再点界面「全部接口体检」。确认 `ai.skip_unhealthy` 为 `true`。

**被跳转到 `.../web/geek/jobs?_security_check=...`** → BOSS 风控。在弹出的浏览器里手动过一次验证，或放慢节奏（调大 `rate_limit.max_per_hour` 的反面：把间隔调大、每天上限调小）。机器人此时会暂停等待。

**「未找到输入框」** → 点「沟通」后聊天窗在**当前页**弹出、URL 不变，所以不能按 URL 判断；输入框查找会按 `retry.max_attempts` 重试并递增等待。日志会打印页面上的 input/textarea 元素与当前 URL 供定位。

**改了配置没生效** → 先查 [配置生效范围](#配置生效范围重要)；仍不生效就是 bug。三端检测脚本能复现：`python tools/verify_three_way.py`。

**两个号串数据** → 检查各自 `cookie_file` 是否独立、`browser_data/account_N` 是否分开；`data/chatted_jobs.json` 是有意共享的。

**端口 5000 被占** → `python kill_port.py 5000`（`start.bat` 已自动做这步）。

## 隐私与版本库

以下文件承载真实账号数据，已在 `.gitignore` 中并且**不再被 git 跟踪**：`bot_config.json`（含 API Key）、`config_overrides.json`、`user_profile.json`、`data/*.json`（投递/回复记录含 HR 对话原文）、`messages/`、`zhipin_cookies*.json`、`browser_data/`、`logs/`。

克隆本仓库后需要自己准备 `bot_config.json`（字段见[配置说明](#配置说明)）。

注意：本仓库**早期历史提交里包含过**这些运行时数据的旧副本。若要保持私密，请把仓库设为私有，或需要改写历史时另行处理。

## 项目结构

```
boss_bot/
├── boss_bot/                    # 核心包
│   ├── __main__.py              # CLI 入口（--greet/--reply/--web/--dry-run/...）
│   ├── unified_config.py        # 统一配置：默认值 → JSON → 环境变量，原子写盘
│   ├── config.py                # 兼容层（模块级常量）
│   ├── browser_launcher.py      # DrissionPage 封装：多浏览器、双标签页、Cookie
│   ├── main_loop.py             # 主循环：双线程并行、健康检查、热重载、多账号
│   ├── greet_engine.py          # 打招呼：搜索/详情/AI 容灾链/发送
│   ├── reply_engine.py          # 回复：四级决策链
│   ├── page_handler.py          # BOSS 页面操作与选择器
│   ├── metrics.py               # 漏斗指标持久化（累计/当日/按账号 + 回填）
│   ├── ai_health.py             # AI 接口体检（真实探测、错误分类、落盘合并）
│   ├── reply_record.py          # 投递/回复记录存储与 Excel 导出
│   ├── message_store.py         # 聊天记录（按账号前缀、时间排序）
│   ├── state_store.py / stats.py / notify.py / intent.py / rules.py / prompts.py
│   └── self_evolve.py           # 回复效果记录与评估
├── flask-version/
│   ├── app.py                   # Flask + SocketIO（REST API、实时推送、体检后台线程）
│   └── templates/index.html     # 单页界面（毛玻璃、双主题、toast、骨架屏）
├── tests/
│   ├── conftest.py              # 会话级隔离：测试不写生产数据
│   └── test_unified.py          # 277 项单元/集成测试
├── tools/                       # 真机实测与诊断脚本（见「测试」小节）
├── data/                        # 运行时数据（已忽略）
├── browser_data/                # 各浏览器 profile（已忽略）
├── start.bat / start.sh         # 一键启动
└── requirements.txt / pytest.ini
```

## 许可证

[MIT License](LICENSE) © huangwei-gem

## 致谢

- **auto_boss** — 自动打招呼/投递引擎
- **BOSS-auto-reply-bot** — 自动回复引擎
