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
- [判分复盘（AI 说"不符合"之后追问为什么）](#判分复盘ai-说不符合之后追问为什么)
- [boss-apply：可分发的 skill 版本（子项目①）](#boss-apply可分发的-skill-版本子项目)
- [多账号](#多账号)
- [聊天记录与 BOSS 对齐](#聊天记录与-boss-对齐)
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

**AI 输出必须像一句人话才允许发**（`reply_engine.reply_rejection`，2026-09-30 加）：
盘上查出 7 条把模型**思考过程**当回复发给 HR 的记录（最长 507 字，含"首先，分析对话上下文："、
整段英文 "Let me analyze the conversation context:"、以及"用户是求职者…根据要求：- 简洁"这种
提示词回显）。两条来路都堵：① `_call_chat` 里旧的 `content or reasoning_content` —— 推理型接口
正文为空时直接把 reasoning 发出去，现在正文为空一律抛错换接口，思考过程永远不进发送队列；
② 模型把"先分析再回复"这条规则写进了正文本身。判据现算自 `data/reply_records.json`：
31 条 AI 回复里 24 条正常的最长 120 字，7 条异常的最短 307 字，所以 160 字这条线两边都碰不到；
再加提示词脚手架字样（招聘方称呼/最近对话记录/对方最新消息/分析：/根据要求…）与 markdown 列表结构。
不合格就抛错走容灾链换下一个接口，全部不合格才轮到 `fail_action` —— 宁可不发，不发草稿。
回归：`tests/test_reply_output_guard.py`（含"拿真实记录全量过一遍，不许误杀能发的回复"）。

## 防骚扰与发送闸门

**唯一跳过依据**：当前这段对话自己以 HR 的拒绝收尾、且我已经回复过，才会显示「跳过（避免骚扰）」。

不按昵称跨会话判定——BOSS 上「胡女士/杨女士」这类称呼大量重名，跨会话判定会造成大面积误跳过（历史上真误跳过过 434 条，已清理并留备份）。会话文件岗位名与当前对话不一致时，按「同名不同人」处理，只使用页面实时读到的这段对话。

**演练模式**（`dry_run` / `--dry-run` / 界面「演练模式（只筛不发）」）：搜索、进详情、AI 判分、生成回复全部照做，只在最后一下点击前拦住，共 4 个发送点：打招呼、发简历、发文字回复、简历发送失败后的降级文字。日志里会打 `🧪 演练模式 | 本应打招呼: 岗位名 @ 公司（未真实操作）`。不消耗每日上限，也不打扰 HR。

新增发送点时必须在调用前加 `self._dry_run(...)`，`tests/test_unified.py::DryRunGateTest` 会静态扫描 `main_loop.py` 的发送调用并断言每个前面都有闸门。

## AI 接口体检与容灾链

界面「AI 接口列表 → 全部接口体检」会**给每个接口真发一条 4 个字的测试消息**（`只回复四个字：连接成功`），拿到正文才算可用；推理模型 `content` 为空但 `reasoning_content` 有内容也算可用。不可用的会标出具体原因（Key 失效 / 额度用尽 / 模型名不对 / 地址不对 / 超时）。超时先重试一次再判死（第二次才通的会在备注里写明"第 2 次才通"），单次网络抖动不算接口坏。

- 结果落 `data/ai_health.json`；服务启动时若结果缺失或超过 24 小时会自动补测一轮
- 支持全量体检与单接口重测，进度实时推送
- 空 Key、重复接口（同 Key+地址+模型）会单独标出

**容灾链**（打招呼侧岗位判分）：单岗位最多试 4 个接口、最多花 60 秒；失败的接口进冷却表（鉴权/额度类 30 分钟，其它 5 分钟）。默认 `ai.skip_unhealthy = true`：**相对顺序不变**，只跳过体检在 24 小时内明确标记不可用的接口；若体检显示全部不可用则自动不生效（宁可慢，也不能不筛）。

实测对比：22 个接口中 5-7 个可用时，同一个岗位判分从 **61.4 秒（超时后落到"默认通过"，等于没筛）降到 6.1 秒并给出真实评分与理由**。

判分结果缓存 24 小时（`data/ai_cache.json`），但**超时/解析失败的结果不缓存**，否则坏判定会在缓存期内一直复用。

**体检的盲区**：探活用 4 个字的短提示词，有些接口短消息秒回、真岗位提示词却 30 秒不回话，体检于是永远标它"可用"。现在真实调用的超时**和截断**都会回写体检表：同一接口连续两次才标不可用（原因写作"真实岗位分析连续 2 次超时 / 正文被截断"），之后它能真回话时标记自动撤销，体检自己测出的结论不会被覆盖。

**什么才算"这次判分失败"**（2026-09-28 修正，这是 AI 筛岗静默失效的主因）：过去接口回空正文、回一段没有 JSON 的话、回一个被截断的思考，全都算"这个接口成功了"——于是既不换下一个接口，又把坏接口回报成健康，岗位直接落到"默认通过"，界面和日志都看不出来。现在这几类一律抛异常走容灾切换：

| 返回长什么样 | 现在的判定 |
| --- | --- |
| `finish_reason=length` 且正文为空 | 正文被截断（thinking 写了 N 字，max_tokens 不够） |
| `content` 与 `reasoning_content` 都空 | 模型未返回正文 |
| 正文里没有 JSON / JSON 不闭合 | 响应里没有 JSON / JSON 不完整 |
| 有 JSON 但既没 `score` 也没 `is_match` | 无法判分（旧代码带着默认 50 分回去，反而把岗位误判成"不匹配"丢掉） |

只有**全部**接口都拿不到判分才落到"默认通过"，并且这条记录会打上 `ai_error`。只给分不给结论的返回按 `ai.match_threshold` 折算出 `is_match`，分数一律钳到 0~100。

**判分预算** `ai.analyze_max_tokens`（默认 1600，界面「判分预算 tokens」）：带思考的接口会把 token 花在 reasoning 上。实测同一岗位同一提示词，1024 时 AskDiandian-Dots3 稳定正文为空（思考写了 1770 字后被截断），提到 1600 后 15.7 秒给出 85 分的真实判分。

**JSON 提取**：纯 JSON、` ```json ` 代码块、前后夹带中文说明都吃得住；括号按字符串状态配对，尾随说明里出现 `}` 也不会把 JSON 切坏。

**自定义筛选关键词=硬否决**：过去只是提示词里一句"适当扣分"，模型给高分照样放行。现在要求命中任一条件时把 `is_match` 置 false 并回填 `veto_hit`，容灾链见到 `veto_hit` 直接判不匹配、把分数压到阈值以下——外包驻场、城市不对、学历不符这类条件这才算拦得住。

**提示词/画像改了立刻生效**：`system_rules`、`user_prompt_template`、个人画像过去在进程启动时快照一次，界面上编辑完要重启才生效；现在每次生成回复都现读。AI 阈值/预算/否决关键词/接口列表变更后，容灾链在下一个岗位重建（配置没变不重建，免得清空冷却表又开始重踩已知坏接口）。界面「恢复默认」提示词用的也是引擎真正在用的那份默认值（后端 `defaults` 字段）——以前前端和 app.py 各抄了一份只有 10 条规则的副本，点一次就把削弱版写进配置，丢掉"先读完整上下文""被拒绝后别再推销"这些约束。

**判分质量面板**：AI 卡片里的「判分质量」显示 真判分 / 兜底 / 平均耗时（`GET /api/ai/quality?account=N`，来自打招呼记录的 `ai_error`、`ai_duration_ms`，并按接口给出 `by_model`）。兜底率高就是"AI 名义上开着、实际没在筛"，界面按 >0% 黄、≥20% 红标色。

实测节奏（22 个接口、头两个就是那种"探活得通、真问不回"的）：第 1 个岗位仍要白等约 61 秒并落到默认通过，慢接口随即进 5 分钟冷却；第 2 个岗位起实测 5.5~5.8 秒给出真实评分。也就是说**每轮运行最多一个岗位没被 AI 筛过**，不会一路放行。

怀疑 AI 没在真筛岗位时：

```bash
python tools/measure_ai_quality.py --failover  # 逐个接口真判分 + 生产解析判定 + 容灾链整体
python tools/ai_health_check.py                # 命令行跑一轮全量体检
```

实测（19 个有 Key 的接口，`max_tokens=1600`）：6 个能给出可信判分，其余是 429/404/403 且都在 1 秒内失败；容灾链整体 7.9 秒拿到真实评分与理由，没有兜底。

## 判分复盘（AI 说"不符合"之后追问为什么）

规则：AI 判某个岗位不匹配时，如果分数只是**差一点**（`阈值-15 ≤ score < 阈值`），就把这条判定顶回给
**同一个接口**追问一句"到底卡在哪一条硬性要求"，四个字段回来存进这条记录：

| 字段 | 含义 |
|------|------|
| `blocking_requirement` | 没过的那条硬性要求（尽量用 JD 原文） |
| `evidence_missing` | 简历里缺什么证据 |
| `fixable_by_resume` | `true`=会做但简历没写；`false`=确实不具备 |
| `score_if_fixed` | 补齐后模型愿意给的分 |

三条边界都是有意为之：

- **只问 AI，绝不问 HR**。"方便说一下哪里不合适吗"是主动触达，撞[防骚扰红线](#防骚扰与发送闸门)，还会在 BOSS 那边留下大批不回话的会话。
- **只追边界带**。当天 95 条 AI 不匹配里有 52 条落在 `55-69`（54.7%），而 10 分以下的多是明显不合适，追问不出可操作的东西；配额因此优先给分数最高的那几条。
- **每号每轮限量**（`ai.probe_max_per_round`，默认 5，界面可改，读入时夹在 0~20）。一次追问≈一次判分：当天 371 条判分的**中位 5.4 秒、p90 20 秒**，所以 5 次大约是每号每轮多 27 秒、网络差时接近 100 秒。填 0 就是关掉。

原因怎么用：**只出建议，不自动改配置**。`GET /api/ai/judgement_review?account=N&days=7` 按
`blocking_requirement` 归并出排行，再折成三类建议（`add_resume_evidence` → `resume.skills`、
`add_scoring_rule` → `ai.custom_scoring_prompt`、`add_veto_keyword` → `ai.custom_filter_keywords`），
每条都带 `auto_applied:false`，界面上点「采纳」才由 `POST /api/ai/judgement_review/adopt`
写进去——只写那一个字段，AI 段走账号覆盖（改账号2 不会顺手改掉基准），简历段永远写全局
（两个号对同一个 HR 说出互相矛盾的自我介绍更糟）。同一条原因要出现 **2 次以上**才出建议，
一次就动配置等于让单次误判长期挡掉一类岗位。

界面两处：AI 面板卡片一行「判分复盘：今日追问 N 条 · 近 7 天 M 条 · 主因 X」，点开看排行与建议；
打招呼记录表里被追问过的那行，原因下面有「🔎 追问原因」可展开。

口径与设计依据见 `docs/superpowers/specs/2026-09-29-reject-reason-followup-design.md`。

## boss-apply：可分发的 skill 版本（子项目①）

同一个 BOSS 直聘，另一种形态：**纯 skill、零安装依赖**，跑在别人自己的浏览器上。
它在 `~/.agents/skills/browser-skill` 之上做编排，**不实现任何浏览器驱动**。

- 源码：仓库根 `boss-apply/`（`SKILL.md` + `references/` + `scripts/`）
- 打包：`python tools/build_boss_apply_skill.py` → `dist/boss-apply.skill`
- 使用者前置：装 `bsk` CLI + browser-skill 扩展，浏览器里保持 BOSS 直聘已登录，
  然后 `python boss-apply/scripts/check_env.py` 自检通过
- 与主程序的关系：本仓库的 `boss_bot/` 仍是开发者自用（cloakbrowser + Flask 面板）；
  skill 面向别人，走使用者自己的真实浏览器与指纹

四条内置红线：只用使用者自己已登录的浏览器（永不代填手机号/验证码）、
每轮默认 20 条硬上限 50、风险由使用者自己认、拿不到可信信息就不发。

设计与边界见 `docs/superpowers/specs/2026-09-30-boss-skill-apply-design.md`。
**本仓库不做真机验证**：那条路要求 cloakbrowser，而 skill 必须跑在使用者的真实浏览器里。
DOM 事实由 `tests/test_boss_apply_package.py` 逐条比对生产代码锁住。

## 多账号

### 一个「数据范围」开关管全部

指标卡下面的 chips 就是数据范围：**选中某个账号，所有面板和按钮都只作用于这个账号**——

- 打招呼记录、回复记录/聊天列表、指标四卡、Excel/JSON 导出、清空记录
- 启动 / 停止 / 暂停打招呼 / 恢复打招呼 / 暂停回复 / 恢复回复 / 确认登录 → 走 `/api/accounts/N/...`；选「全部账号」才走全局接口。按钮上会写出作用范围（如「暂停打招呼（账号2）」），操作条上有一枚范围标签
- 实时推送（socket）的行按 `account_index` 过滤，别的号推来的行不会混进当前表格
- 点上面的账号标签（`主账号` / `账号2`）等同于切范围，切完自动重拉

登录框弹出时会记住**是哪个账号**要登录，「我已登录」只发给它——以前点一次会给所有账号发确认，把还在等扫码的另一个号也顶过去。

### 账号状态点

每个标签上有两个点：

| 点 | 含义 |
|----|------|
| 左（运行） | 灰=未启动；蓝闪=初始化中；黄=等你登录；蓝=已暂停（打招呼+回复都停）；绿闪=运行中。悬停显示阶段、登录失败原因、是否在回复某个会话 |
| 右（Cookie） | 绿=登录态文件有效；红=失效（悬停看原因）；灰=未测；黄闪=检测中。点一下强制重测该账号 |

Cookie 点由 `GET /api/accounts/cookies` 供数（只查文件，不启动浏览器），随状态轮询自动刷新，登录成功 0.8 秒后强制重测一次。**以前它只在手点之后才有值**，所以账号 2 登录成功了一直挂着红点。带浏览器的深度检测不再放在界面上——它会和正在跑的会话抢同一个 profile。

### 数据归属

每个账号一套独立运行数据，主账号（索引 0）沿用原文件名不迁移，其余加 `_account_N`：

| 数据 | 文件 |
|------|------|
| 浏览器实例 / profile | 各自独立端口 + `browser_data/account_N` |
| 登录 Cookie | 账号配置里的 `cookie_file`，回落到全局 |
| 会话状态（防重复回复/发简历）、人工接管 | `bot_state.json` / `bot_state_account_N.json` |
| 统计 | `bot_stats.json` / `bot_stats_account_N.json` |
| 聊天记录 | `messages/姓名#公司.json` / `messages/aN_姓名#公司.json`；**合并视图里会话身份是「姓名+公司+账号」**——两个号聊到同一家公司的同一个人是真实存在的（实测 李女士 @ 深圳市极客星球电…，账号1 存 9 条、账号2 存 7 条），只按 chat_id 分组会让其中一段对话在界面上彻底消失，标已读还会标到另一个号。**红点数字只认 BOSS 自己报的**（回复轮次扫侧栏时把 `.notice-badge` 存成 `unread_on_boss`，读完归零）；以前是"本地数 HR 消息且没标已读"，实测账号1 显示 53、账号2 显示 118，而 BOSS 说 13 / 1 —— 我们只存了点开的 2~3 条，从没点开的会话永远挂着未读。没观察到过就不画数 |
| 打招呼记录、回复记录 | **共用** `data/greet_records.json`、`data/reply_records.json`，每条带 `account_index`，接口按 `?account=` 过滤，导出与清空同样按账号收口 |
| 打招呼话术、简历图片、消息间隔、岗位任务 | 各自 `accounts[N]` |
| AI 判分标准与回复参数 | `accounts[N].settings`，**只存与全局基准不同的字段**（见下） |
| 每日上限计数 | 按 `account_index` 过滤 |
| 自进化数据 | `data/evolution_data.json` / `..._account_N.json`（质量数据同样分文件） |
| 风控/封号通知 | 事件带 `account_index` 与账号名，前端横幅会写「[账号2] …」 |

**每账号一套配置**：两个号投的是不同工种（一个数据分析、一个运营），判分阈值、打分提示词、回复模板、投递频率都不该共用一套。`accounts[N].settings` 存的是**稀疏覆盖**（形如 `{"ai": {"match_threshold": 88}}`），运行时 `UnifiedConfig.apply_account(N)` 把基准深拷贝后盖上这几项，所以：基准改了没动过的账号跟着变，账号改回和基准一样就变成空覆盖（等于取消独立）。可覆盖的段由 `ACCOUNT_OVERLAY_CONTAINERS` 决定：`ai`、`reply`、`templates`、`rate_limit`、`retry`、`login`、`notify`、`browser`（`browser` 里 `debug_port` 与 `user_data_dir` 锁死——那两个是账号索引算出来的 9222+idx / `account_{idx}`，界面改了等于两个号抢同一个浏览器）。界面上「数据范围」选中某号时，AI 面板标题会写明「仅账号「X」独立生效」，保存时请求带 `account`，后端 `_fold_into_account_overlay` 把生效值折回该号覆盖、全局段退回基准值 —— 少了这一步，改账号 2 的阈值会顺手改掉所有账号。判分复盘的「采纳」也必须先在 `apply_account(N)` 之后的生效值上追加再折回，否则第二次采纳会把第一次的覆盖整段替换成空（真机实测点出来的，`tests/test_judgement_review.py::test_第二次采纳不会抹掉第一次的覆盖` 锁住）。

**仍然全局共享（有意为之）**：简历信息 `resume`、AI 接口列表与 Key、回复规则、个人画像、关键词，以及**已投递 URL 去重表 `data/chatted_jobs.json`**——同一个岗位不让两个号各打一次招呼，否则 HR 会收到两条一模一样的消息；简历按号覆盖更糟，两个号会对同一个人说出两套学历。

共享带来的两个坑都已修：

- 两个引擎各持一份内存副本、写入时整档覆盖 → 后写的把先写的抹掉，会重复招呼。现在写入前读盘取并集，并用类级锁串行。
- 快照只在首次读盘、永不更新 → 账号 1 整晚看不到账号 2 打过的岗位。现在每 60 秒重读一次并保留本地新增。
- **端口上的浏览器未必是本账号的**：DrissionPage 的 `set_local_port` 是"端口上已经有浏览器就直接连"。
  2026-09-29 主号整天 0 投递就是这么来的——一个一次性取证脚本（`ChromiumOptions` 既不设端口也不设
  profile）留下孤儿浏览器占住 9222，profile 是 `Temp\DrissionPage\userData\9222`，没任何登录态，
  主号连上它一进 BOSS 就是手机号+短信验证墙。现在 `assert_port_is_free_for` 会在启动前用
  `Get-CimInstance Win32_Process` 读出端口上那个浏览器的 `--user-data-dir`：是本账号的照旧复用，
  不是就直接拒绝并报出 PID、两边的 profile 和 `taskkill /PID x /T /F`。判断不了（老 CLI 不带
  profile）时不拦——宁可放行也不能把人堵死。

**按天归档**（`data/archive/日期/`）现在一天只执行一次：记录文件是共用的，两个账号各自归档时，后跑的那个会把已被清空的空文件归档掉，等于丢一整天数据。清空改走存储单例（内存 + 磁盘一起清），否则引擎下一次写记录会把旧记录整团写回。

历史记录的账号归属修复：

```bash
python -X utf8 tools/backfill_greet_record_account.py           # 预演，打印会改多少条
python -X utf8 tools/backfill_greet_record_account.py --apply   # 真正写盘，自动留 .bak
```

### 并发真机实测口径

两个号同时跑起来才会暴露的问题（页面互抢、Cookie 误判、AI 限流互踩），单测覆盖不到，
每次改登录态/并发相关代码后按这个口径复跑一遍：

```bash
# 面板开着，bot_config.json 里 dry_run=true（四个发送点全拦），两个号 enabled=true
curl -X POST http://localhost:5000/api/start
curl http://localhost:5000/api/status   # 每 10 秒看一次 phase / login_reason / 各自 stats
curl -X POST http://localhost:5000/api/stop
```

要同时核对的三件事（2026-09-29 23:29 那轮的实测值）：

| 核对项 | 通过标准 | 该轮结果 |
|--------|----------|----------|
| 两个号都在跑 | 两边 `phase` 都进 `running`，且 `greet_rounds`/`reply_rounds` 都在涨 | 36 秒内双号 `running`；主号 1 轮招呼 + 39 轮回复，账号2 2 轮招呼 + 2 轮回复 |
| 没发出去东西 | `logs/boss_bot.log` 里只有 `🧪 演练模式 \| 本应…（未真实操作）` | 73 条"本应打招呼"，0 条真实发送 |
| Cookie 文件没动 | 跑前跑后 sha256 一致，`data/stale_cookies/` 不增文件 | 两个文件 sha256 前后完全一致，归档目录仍是 1 个（23:01 那轮的） |

守卫本身也单独在现场验过（只读，`_discard_stale_cookies`/`save_cookies` 打桩成打印）：会话页判
`logged_in`；把标签页开去 `/web/user` 但浏览器里登录项还在时判 `uncertain`，
`_recheck_before_archive()` 返回 `False`（= 不许动 Cookie）；回会话页又判 `logged_in`。
这条就是 23:01 那轮把主号会话归档掉的那个现场，现在它只打一行 WARN 就跳过本轮。

## 聊天记录与 BOSS 对齐

回复记录 / 聊天面板的目标是「和 BOSS 上看到的一模一样」。下面三条口径都是真机实测定下来的
（`tools/compare_boss_chat.py`、`tools/probe_chat_identity.py`，账号0 侧栏 34 行）：

| 问题 | 实测结论 | 做法 |
|------|----------|------|
| 同昵称是几个人 | 只用姓名 30/34 唯一，陈女士 / 唐女士 / 刘女士 / 易女士 各 2 个 | 会话身份 = **姓名 + 公司**（公司就挂在侧栏那一行的 `.name-box` 上，不用点开就能读到；取不到时退到岗位名），文件 `messages/姓名#公司.json` |
| 顺序按什么排 | 线上时间标签只在分段处出现（存量 306 条里 123 条没有 time），`昨天 21:54`、`09-23 21:37` 还解析不成时间戳 | 顺序与去重都按 `data-mid`（雪花 id，同一会话内自上而下单调递增，实测全部验证）；时间标签原样显示，前端不再换算一次 |
| 卡片与自记条目 | 简历 / PK 分析这类卡片 `.text-content` 是空的、`innerText` 才有内容；`[简历已发送]` 线上根本没有这条 | 每条消息带 `kind`：`bubble` 气泡 / `card` 卡片 / `system` BOSS 系统条 / `action` 引擎自记（默认不画，勾「显示引擎标记」才显示） |

点开会话也不假设「点到的就是选中的」：侧栏会随时重排，所以 `enter_chat` 要求
**点击行 = `selected` 行 = 顶栏姓名** 三者对齐（同名但公司不同同样判失败），
对不上就跳过这一段，绝不把消息记进别人的文件里。

存量数据按新规矩重采（全程只读，不点任何发送/打招呼按钮）：

```bash
python -X utf8 tools/resync_boss_chat.py              # 归档旧文件 + 全部重采 + 逐条校验
python -X utf8 tools/resync_boss_chat.py --limit 8 --no-archive
python -X utf8 tools/compare_boss_chat.py --n 5       # 只看差异，不写数据
```

`resync` 的验收口径是「本地尾部 N 条 == 线上这一屏」，逐条比正文、方向和时间标签。
BOSS 每个会话只给 2-3 条历史（`.chat-content` 的 `scrollHeight == clientHeight`，滚动和滚轮都加载不出更多），
所以更早的历史只能靠机器人运行时逐轮攒下来，没法事后补。旧格式文件（没有 `chat_id`、按昵称合并的那批）
不做拆分——同名的两段对话在同一个文件里已经分不干净了——2026-09-29 重同步完成后已按用户要求删除。

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
| `ai` | `enabled`、`providers`、`match_threshold`、`max_tokens`（回复输出预算）、`analyze_max_tokens`（岗位判分输出预算，带思考的接口要给够）、`fail_action`、`rate_limit_wait`、`custom_filter_keywords`（硬否决条件）、`custom_scoring_prompt`、`skip_unhealthy`、`probe_max_per_round`（判分复盘：每号每轮最多追问几条不匹配原因，0=关掉，读入时夹在 0~20） |
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

`self_evolve_enabled` 打开后，运行时会记录每条 AI 回复并在 HR 回消息后评估效果（正/负/无响应），数据写入 `data/evolution_data*.json`，界面 `/api/self_evolve/report` 读取。**它不会自动改写你写的话术和规则**——报告里的"规则调整"是建议条目（`auto_applied: false`），命中模式时说的一律是"引擎内置防护已覆盖"（拒绝检测、自我介绍去重、拒绝后不发简历、重复消息跳过、切题要求），不是它替你改了配置。

## 配置生效范围（重要）

主循环每轮热重载配置，以下改动**不用重启**：AI 接口列表与阈值、判分预算 `analyze_max_tokens`、自定义筛选词与打分提示词、跳过不可用接口、复盘追问上限 `probe_max_per_round`、打招呼频率限制与重试次数、回复延迟/每小时上限/`max_tokens`/`fail_action`/限流等待、两个总开关、演练模式、自进化开关、话术与规则、个人画像、AI 提示词（`system_rules` / `user_prompt_template`，每次生成回复现读）。

以下改动**需要重启**（构造时读取）：浏览器路径与 profile、端口、Cookie 文件路径、账号列表增删、简历图片文件、日志目录、通知 Webhook。

界面上每个输入框都对应真实生效的配置项；如果哪天发现某个控件改了没反应，那是 bug，按 [测试](#测试) 里的三端检测复现并提 issue。

## 测试

两套都要跑：单元测试管逻辑，真机浏览器套件管"打开来真的能用"。

```bash
pytest tests/ -q                      # 726 项，约 56 秒，全部离线（不碰真实数据、不联网）
```

真机套件全部使用项目内 `cloakbrowser/chrome.exe`，且**只做读/切/筛/存配置，绝不点发送、打招呼、发简历**：

```bash
python tests/e2e_greet_records_ui.py  # 15 项：打招呼记录表实时推送/去重/日期筛选/数据范围切换（独立浏览器，只开本地面板）
python tests/e2e_account_ui.py        # 8 项：左侧账号行只管登录、右侧数据范围唯一切换入口、招呼语输入框真的写回配置
python tests/e2e_per_account_config.py # 9 项：每账号一套配置。临时目录副本 + 第二个 Flask(5055)，点完不碰真实 bot_config.json
python tests/e2e_judgement_review_ui.py # 19 项：判分复盘一行/明细展开/追问上限往返/采纳按钮真写配置/记录里 🔎 追问原因可展开/切账号跟着变（要先跑过一轮攒出追问数据）
python tests/e2e_account_scope_ui.py    # 19 项：开机零点击就有数据范围条、点 chip 立刻高亮且记录跟着切、右侧无登录入口、左侧登录+状态点、招呼语写进本号不污染别号、新增账号跟到手（临时副本 + Flask(5056)）
python tests/e2e_review_ui.py           # 14 项：复盘行取真接口数、明细排行、上限落盘、连点两条采纳各归各位（AI 段进账号覆盖、简历段进全局）（临时副本 + Flask(5057)）
python tests/run_dashboard_on_temp_panel.py # 用当前代码起一个临时副本面板(5059)，在它上面跑 tools/e2e_dashboard.py 的 70 项——改了 app.py/模板但不想动正在用的 5000 面板时用这个（面板进程不热重载 Python 代码，只热重载模板）
python tools/e2e_dashboard.py         # 70 项：界面渲染、指标卡、记录筛选、开关往返、弹窗、主题、断线横幅、无 JS 报错；含 20 项多账号范围 + 7 项 AI 预算/判分质量/提示词默认值断言 + 1 项配置异常响应不覆盖界面
python tools/e2e_live_boss.py         # 26 项：反爬自检、登录态、会话读取、岗位解析、AI 真实判分、多账号归属（含另开账号2 浏览器比对会话）
python tools/verify_dashboard_ui.py   # 14 项：指标卡口径 + AI 体检展示，产出 tools/verify_dashboard.png
python tools/verify_three_way.py      # BOSS 页面 / 后端存储 / 前端显示 三端逐条比对
python tools/measure_ai_quality.py    # 逐个接口真判分 + 生产解析判定（要联网，只发分析请求）
python tools/diagnose_ai_providers.py --models   # 不可用接口归因：代理/直连各打 3 次 + 官方模型清单核对
python tools/check_account_isolation.py         # 多账号隔离自检：端口/profile/cookie 文件/账号身份 四层比对
python tools/two_account_login_check.py         # 逐账号实连登录态：连自己的端口+profile，新标签页验会话页（全程只读）
```

**多账号一定要用 `check_account_isolation.py` 验，别只看"有两个槽位两份文件"**：2026-09-29 查
"第二个浏览器打开是空的"时发现，`zhipin_cookies.json` 和 `zhipin_cookies_1.json` 里的 `wt2`
（BOSS 登录态令牌）**完全相同**——两个槽位登录的是同一个账号，多账号从来没成立过。
端口、profile 目录、文件路径全部分开也查不出这件事，必须拿登录字段（`wt2`/`wbg`/`zp_at`）算指纹比对；
`bst`、`__zp_stoken__` 这类风控字段每次都变，算进去会把同一个号看成两个。
配套修掉的是 `BrowserManager`：以前 `user_data_dir` 为空时干脆不传 `--user-data-dir`，
Chrome 于是退回默认用户目录，两号共用一份 profile（表现就是主账号 `浏览器连接失败 127.0.0.1:9222` 直接退出）。
现在任意来源的 profile 都会按账号再分一层，落到 `browser_data/account_{i}`。

### 体检里"不可用"分别是什么原因

`tools/diagnose_ai_providers.py` 把同一个请求**走系统代理**和**不走代理**各打 `--repeat`（默认 3）次再归因。
之所以要打多次：限流和抖动是按次发生的，2026-09-28 那版单次抽样得出的"代理/网络问题：直连能通，走代理失败"
第二天复查就被推翻了——归因必须带"通 m/n"才可信。

2026-09-29 实测（19 个接口 = 8 可用 + 11 不可用；每条路由各 3 次）：

**那 11 行已经按用户决定从 `bot_config.json` 里删掉了**（备份在 `data/archive/2026-09-29/bot_config.before_ai_trim.json`），
配置里现在只剩 8 行可用的。下面这张表留着，是为了下次再看到同类返回码时不用重新查一遍。

| 数量 | 现象 | 代理 vs 直连 | 是不是代理的问题 | 能做什么 |
|------|------|--------------|------------------|----------|
| 6 | `403 FreeTierError：只能在 OpenCode 客户端内使用` | 0/3 vs 0/3，报文逐字相同 | 不是 | 官方文档写明免费模型 "available on OpenCode"，客户端限定，改不了 |
| 4 | `404 page not found`（约 1s） | 0/3 vs 0/3 | 不是 | 模型名缺命名空间：官方 id 是 `z-ai/glm-5.3`、`z-ai/glm-5.3-flash`、`moonshotai/kimi-k3`、`deepseek-ai/deepseek-v4.1-flash` |
| 1 | `400 Model is unavailable` | 0/3 vs 0/3 | 不是 | `deepseek-v4-flash-free` 已下线，官方 81 个 id 里没有它 |

**请求本身是对的**，这点已按官方口径核对：`GET https://integrate.api.nvidia.com/v1/models` 代理和直连**都是 200、81 个模型**，
说明 base_url 和路径没错；404 是网关按模型名路由、认不出没带 `org/` 前缀的短名。把 id 换成官方值后 404 消失、
请求能进到模型，但 45~60 秒不回话（两条路由一样）——那是 NVIDIA 免费额度排队，不是我们的报文问题。
OpenCode 侧同理：官方文档确认 base_url 就是 `https://opencode.ai/zen/v1`，403 是策略不是写错；
这个 Key 整条路都堵死——非 free 的 `big-pickle` 一样 403，付费 `deepseek-v4-flash` 回 `402 Insufficient account funds`，
所以 7 行 OpenCode 要么充值要么删掉。

关于代理，三条实测结论：

1. Windows 上 `requests` **和** httpx（OpenAI SDK 用的）都不只认环境变量，**还读注册表代理**，
   所以体检和判分实际都从 `127.0.0.1:7897` 出去（实测出口 IP `188.253.124.80`，环境变量一个都没设）。
2. 上面 11 个不可用**没有一个**是代理造成的：两边返回码和报文完全一致。
3. 家里代理是在**帮忙**而不是添乱：`AMD-DeepSeek-V4.1` 走代理 4/4 全 200（1.4~4.2s）、直连 4 次只通 1 次；
   境外站直连还会被直接 RST（`api.ipify.org` 直连 `ConnectionResetError 10054`）。
   **别为了排障去关代理**，判断这类问题用 `diagnose_ai_providers.py`，别靠猜。

多账号范围这一层用"拦住 fetch / window.open / confirm，只记不真发"的方式验证控制路由与文案，所以不会真的改动运行状态。

> `tests/e2e_api.py` 是老的全量接口脚本，它会用测试值整体覆写 `简历 / 回复规则 / 模板 / AI 列表`，只还原个人画像。要跑它先备份 `bot_config.json`。

`tests/conftest.py` 会把回复记录、打招呼记录、指标文件重定向到临时目录，跑测试不会污染生产数据。

## 反爬与容错

- 随机消息间隔（按账号配置）、UA 轮换、`--disable-blink-features=AutomationControlled`
- 验证码/风控检测 → 暂停对应侧并推送通知；打招呼连续 3 轮搜索为 0 也会暂停提示人工检查
- 回复侧同样做登录态/风控检查（被踢下线不会整轮空转）
- 浏览器断连自动重连：指数退避 2→4→8→16→32→60 秒，重连后按账号重载 Cookie 并重建引擎
- 岗位 URL 通过转义后的 JS 字面量注入，避免 DOM 内容被当代码执行
- 通知：企业微信 / 飞书 / 钉钉 Webhook

## 故障排查

**「AI 已拒绝过」大量误跳过** → 已修复为只看当前会话；历史误判记录已清理（当时的备份已随残留清理一并删除）。

**两个账号串成同一个号** → 跑 `python tools/check_account_isolation.py`。2026-09-29 修掉两处真实泄漏：
`page_handler` 确认登录时写的是**全局** `config.COOKIE_FILE`（账号2 登录会把主账号那份覆盖成自己的会话），
`greet_engine._save_cookies` 存完自己的还要 `shutil.copy2` 一份到公共 `zhipin_cookies.json` 当"兜底"。
现在两处都只写本账号那一份，且路径一律 `resolve_path()` 锚到项目根（界面从 `flask-version/` 启动，
返回相对名会按 CWD 落到别处）。

**界面上删掉的 AI 接口又回来了** → 以前 `app._ensure_config()` 只在第一次读盘，之后一直用内存那份，
外部改动（脚本清理、手改文件）看不见，且界面上任意一次保存会把旧内容整份写回磁盘。
现在按 `(mtime, size)` 指纹发现外部改动就重读；文件被写坏时保留内存里那份好配置。

**打招呼像卡死 / 岗位没被 AI 筛** → 先看 AI 卡片里的「判分质量」：兜底率就是"没被 AI 真正判过的岗位占比"。日志里找 `AI 分析异常`（后面会带上具体原因：正文被截断 / 没有 JSON / 未返回正文 / 无法判分），再跑 `python tools/measure_ai_quality.py --failover` 逐个接口看生产解析吃不吃得下，最后点界面「全部接口体检」。确认 `ai.skip_unhealthy` 为 `true`；带思考的接口把 `ai.analyze_max_tokens` 调大（界面「判分预算 tokens」）。

**页面地址里出现 `?_security_check=…`** → 这**不是**验证码。2026-09-30 12:08:24 带着这个参数落地的岗位页，12:08:25 照常读到 JD、12:08:27 点中"立即沟通"、12:08:31 招呼语已经发出——它是 BOSS 的静默风控参数，而且不会自己消失。以前"URL 命中就算验证码"，两个号的打招呼在 12:20~12:23 各被停了 3 次，而浏览器窗口里根本没有验证图（取证：`python tools/captcha_false_positive_check.py`）。

现在只认页面证据：可见的挑战容器（`.nc-container`、`#tcaptcha` 等，实测宽高 >4 且没被 `display:none` 藏着）或强文案（安全验证 / 请完成验证 / 拖动滑块 / 滑动验证 / 人机验证 / 图形验证）。裸"验证码"三个字不再算判据——岗位 JD、HR 消息、登录框的"获取验证码"按钮里都有这三个字。

判据全项目**只有一份**：`page_handler.CAPTCHA_BOX_SELECTORS` / `CAPTCHA_STRONG_WORDS` / `captcha_evidence()`，打招呼侧、会话侧、投递失败归因三处共用（以前各写一套，才出现"后端说验证码、页面上没影"）。每次判定都把判据连同 URL 写进日志：`页面判据=captcha（可见挑战容器 .nc-container）URL=…`，前端横幅也带这句，误判当场能看出来。

行为不变：确诊 → 推横幅 → `_captcha_gate()` 每 2 秒复查，人工过了就继续；超时只跳过当前任务，**连续 3 次**都没人应答才真暂停（点恢复即可）。阈值是 `main_loop.py` 的 `CAPTCHA_WAIT_SECONDS / CAPTCHA_POLL_SECONDS / CAPTCHA_STRIKES_TO_PAUSE`。

以前"程序卡在那张验证图不动"是几个 bug 叠出来的，逐个记在这里免得复发：

| 根因 | 位置 | 说明 |
|------|------|------|
| 验证码探针**从来没执行成功过** | `browser_launcher.BrowserInstance.run_js` | 它是 `run_js(self, script, *args)`，不收 `as_expr` 关键字；`check_health` 用 `run_js(js, as_expr=True)` 调用 → 直接 TypeError → 被 except 兜成 `unknown` |
| 未知结果被当成健康 | `page_handler.check_health` | `return result if result in ("ok","captcha") else "ok"`，纯图形验证页（正文没有"验证码"三字）判成 ok 继续自动操作 |
| 没有聊天处理器时永远报健康 | `main_loop._check_health` | `_chat_handler is None` 直接 `return "ok"`，回复引擎还没建就完全检测不到风控 |
| 认出来了也解不开 | `main_loop` 两侧循环 | 一遇验证码就 `_greet_paused = True`，而自动恢复只认"每日上限"标记；回复侧的 `_reply_paused` 分支在 `_hot_reload_config()` 之前就 `continue`，自动恢复函数根本走不到 |
| URL 标记单独定罪 | `page_handler.classify_health` | `"_security_check" in url` 就直接返回 captcha：页面可用也定罪，而那个参数不会自己消失 → 60 秒永远等不到"已恢复"，三次就把打招呼停了 |
| 打招呼侧探针读不到值 | `main_loop._check_health` | IIFE 脚本调用时没带 `as_expr=True` → DrissionPage 把它包成函数体、里面没有 return → 恒为 `undefined`，页面证据一条都拿不到，只剩上面那个不可靠的地址 |

探针脚本的形态与调用方式必须配对：`(function(){…})()` 这类**表达式**要带 `as_expr=True`；以 `return` 开头的**函数体**反而不能带（会变成顶层 return）。这条有回归锁 `tests/test_page_probes.py`，活体复核脚本 `tools/probe_as_expr_check.py`。

另外投递路径上的重试等待原来是裸 `time.sleep`（不看 `running`），单岗位的光标重试循环能空转 3~10 分钟，
点"停止"也醒不过来；现在换成 `_interruptible_sleep()`，并在循环里先查 `_on_captcha_page()`，
是验证页就直接返回"需要人工验证"而不是继续找输入框。
`_login_event` 以前只 set 不 clear，人工登录过一次之后 `_wait_for_login` 永远立刻返回，真掉登录时变成 300 秒空转。

验证方式：判据的回归在 `tests/test_captcha_gate.py`，里面直接复现这次的误判——
地址带风控参数、页面无挑战证据 → 判 `ok`；同一地址加上可见容器 → 判 `captcha`。
真机只读复核跑 `python tools/captcha_false_positive_check.py`，它逐标签页打印地址、
命中的文字/DOM 判据，以及同一份探针带/不带 `as_expr` 各自的返回值。

**「未找到输入框」** → 这句现在只是类别，记录里会带上现场原因（`chat_failure_reason()` 按页面快照分档）。2026-09-29 把 `logs/` 里 116 次失败的现场逐条分类，实际是三件事：

| 现场 | 占比 | 真实含义 | 处理 |
|------|------|----------|------|
| 页面出现 `ipt-phone` + `ipt-sms` | 61/116 | **登录态掉了**，BOSS 把岗位页换成手机+短信验证码框 | 在浏览器窗口里重新登录；不重登则每个岗位都卡这里 |
| 只剩 `ipt-search`/`city-code`，URL 仍在 `job_detail` | 45/116 | 点「立即沟通」后聊天抽屉压根没在这个标签页渲染 | 见下：抽屉是页面内弹出层，URL 不变，所以不能按 URL 判断；重试按 `retry.max_attempts` 递增等待 |
| 抛"与页面的连接已断开" | 8/116 | 标签页被关/被另一个线程抢走 | 多账号别共用一个浏览器端口（今天主账号就是这样启动失败的） |

旧版这里只会打一句"未找到输入框"，而且 dump 只抓 `tag:input`/`tag:textarea`——BOSS 聊天框是
`#chat-input.chat-input` 这个 **contenteditable div**，旧 dump 根本看不见它，所以"页面上没有输入框"
这个结论本身是瞎的。现在 `CHAT_SNAPSHOT_JS` 一次把 input 样式、contenteditable、抽屉容器、URL 全捞回来再归因。
`logs/greet_engine.log` 的时间戳也补上了日期（原来只有 `HH:MM:SS`，跨天日志分不开）。

**「未找到沟通按钮」** → 同样只是类别，记录里现在带现场原因（`chat_button_failure_reason()`
按"请求的 URL"和"导航后落在哪"分档）。2026-09-29 界面又冒出 9 条这句，把 `logs/` 里 20 次
pre-click 命中逐条比对导航前后的 URL，其实是三件事：

| 落地 | 次数 | 真实含义 | 处理 |
|------|------|----------|------|
| `/web/geek/chat`（请求的是 `job_detail/xxx`） | 9/20 | 该岗位**此前已沟通**，BOSS 把详情页直接跳成会话页 | 现在标"已沟通"并进界面「已沟通」档，下一轮不再撞同一岗位 |
| 另一个 `job_detail/yyy` | 2/20 | 原岗位已下线，BOSS 重定向到相似职位 | 原因写"岗位已下线" |
| 同一个 `job_detail/xxx` | 9/20 | 页面到位但没有可点的沟通按钮（过期/需完善简历/新版布局） | 原因里带上抓到的按钮文本+class 与页面提示，下次发生即可定位 |

`CHAT_SNAPSHOT_JS` 因此多抓两项：可见按钮（`文本|class`，最多 8 个）和提示条文案。真机验证过
健康岗位页能抓到 `立即沟通|btn btn-startchat`，会话页能抓到 `发送|btn-v2 btn-sure-v2 btn-send`，
所以"这里确实没有按钮"不再是猜的。

另外去掉一处误导：图片上传那步跑在招呼语**已经发出之后**，回不到会话窗口时它打的也是
"未找到沟通按钮，无法上传图片"（历史 19 次），看记录的人以为岗位没投出去。现在这句改成
"招呼语已发出，但回不到会话窗口，图片未上传"。

顺带记录一个反证：招呼语确实发得出去——`messages/` 里 38 个会话有 20 个含我方发出的招呼语原文，
所以成功路径上的 `.input-area` 是真聊天框，不是假成功。

**招呼语只有两级，且没有兜底** → 岗位专属 → 账号自定义，两级都空就**不发**这个岗位
（`GREETING_MISSING_REASON`，在点「立即沟通」之前就拦，日志写"未配置招呼语，跳过这个岗位"）。
以前这里挂着 `DEFAULT_GREETING`：界面预填、岗位兜底、账号空缺回落三处都用它，结果是
两个号都可能把"应聘数据分析岗位"那段系统模板发给 HR——发出去的话不是用户自己的话。
现在 `DEFAULT_GREETING` 只剩一个用途：`pick_greeting` 拿它当**标记**，识别历史配置里那些
"从没被改过、当初被默认串填进去"的岗位文案（否则每个岗位都算"岗位定制"，账号级自定义永远不生效），
它不再作为任何回落文案出现。
副作用要说清楚：**两个号的 `greeting_message` 现在都是空的，不写就不投**。

**BOSS 的第二种打招呼机制** → 点「立即沟通」后平台自己把招呼语发出去了，弹一个
「已向BOSS发送消息 / 留在此页 / 继续沟通」的对话框，页面里没有可输入的抽屉。以前认出弹窗就直接
记一句"BOSS 平台自己发出了招呼语"跳过，等于把打招呼语交回平台预设，账号自定义那段的文案永远发不出去。
现在 `_auto_greet_followup()` 会点「继续沟通」进会话，用 `LAST_MINE_BUBBLE_JS` 读我方最后一条已发出的
气泡（`.message-item.item-myself .text-content`，class 从 `tools/chat_page_structure.json` 的真实 dump 核出来）：

| 读到的 | 处置 | 记录 |
|--------|------|------|
| 折空白后等于本号招呼语 | 一个字都不再发 | 已投递，日志写"BOSS 自动发出的就是本号招呼语" |
| 不等 / 我方气泡为 0 条 | 在会话里输入本号招呼语并发送 | 已投递，日志写"已补发本号招呼语" |
| 进不去会话（读不到 `.message-item`） | 不盲发，避免对着搜索页打字 | 保持原来的"平台已自动发送"归因 |

判定只按空白折叠后比对：气泡里会带渲染用的空格换行，逐字符比会把"已经是我们这句"误判成不一致，
于是对同一个 HR 发两遍。真机（只读）验过探针在 BOSS 会话页的表现——没点开会话时
`chat_page:false`（所以不会盲发），点开后 `total:4, mine:1`，读到的是
"您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。"。
另外新版岗位列表页把「立即沟通」收进了右侧详情栏（`A.btn.btn-startchat`，卡片里一个都没有），
引擎本来就是进详情页再找按钮，这条路径没受影响。

**刚推送的行闪一下又没了** → 引擎是"点发送即落库+推送"，所以实时行一开始只在前端模型里。
`loadGreetRecords` 拿到服务端列表后整份赋值，而页面加载/20 秒轮询的那次 `fetch` 往往在落库**之前**
就发出了，落地时正好把刚推上来的那行替换掉，最长要等下一轮轮询才回来。现在推送行进
`greetPendingRows`：服务端快照里没有它时留在表头，确认回来的那次自动并入（`_key` 相同不会变两行），
只在当前数据范围内留（切号不能把别的号的推送挂过来），超过 3 分钟还没被确认就放手，手动清空时直接清。
同一次修复还堵了姊妹问题：`loadGreetRecords` 现在带请求序号，切范围时**上一个范围的慢响应后落地不再把表
盖回去**（看板实测复现过：点「账号2」后仍是全部账号的 393 行，看着就是"切换没反应"）。

**登录态被误判后 Cookie 文件没了** → 2026-09-29 用演练模式（`dry_run`，四个发送点全拦）真并发跑两个号，
把这条链跑出来了：主账号启动时被判"Cookie 已过期"→ 旧代码当场 `unlink()` 掉 `zhipin_cookies.json` →
`_wait_for_login` 只等 2 秒看 URL 又误报"检测到登录成功" → 于是把**登录页那份 cookie 存成文件**顶掉原会话 →
回复侧从 9 秒后开始每 30 秒打一次"登录态失效"并再删一次，一路刷到我手动停。三个洞各自的修法：

| 洞 | 修法 | 位置 |
|----|------|------|
| 拿一眼 URL 当结论（BOSS 是 SPA，`/web/geek/chat` 未登录时先渲染再跳 `/web/user`） | `_settle_url()` 连读两次一致才定罪；`login_state_of(url, has_auth_cookie)` 要页面与浏览器内登录项**两路一致**才给 `logged_in`/`login_wall`，矛盾一律 `uncertain` | `boss_bot/main_loop.py` |
| 判"过期"就删用户的会话文件（不可逆，且丢了排查依据） | 改归档：`archive_cookie_file()` 把文件挪到 `data/stale_cookies/主账号_<时间戳>.json`（原路径为空，下一轮照样走完整登录，内容还在）；`uncertain` 时一个字节都不动 | 同上，开关仍是 `login.clear_cookies_on_failure` |
| 假登录后无条件 `save_cookies`，用登录页那份顶掉好会话 | `_save_cookies_if_logged_in()`：浏览器里没有未过期的 `wt2/zp_at/bst/wbg` 就不落盘，并打日志说明 | 同上 |

两侧健康检查报"要登录"时都不再凭第一眼定罪：`_recheck_before_archive()` 用同一套两路证据**只读复核**
当前标签页（不导航——把另一个线程正在用的页面拽走比误判更糟），复核不是 `login_wall` 就不动 Cookie 文件、
不计失败，本轮跳过。只有连续复核到登录墙才归档，且只在第一次归档（后面几次不再反复搬文件）；
连续 3 次确认才停这个号的回复轮并置 `needs_login`，中间恢复过一次计数清零。
等登录时先看 Cookie 再决定要不要访问页面——还没扫完就把页面刷走，等于把人家的二维码弄没。
`_handle_login` 同理：两路矛盾先等 4 秒复核一次，仍然矛盾才按"需要人工登录"处理，
并且**只有确认 `cookie_expired` 才导航到登录页**——本来就登录着时跳过去会把会话页弄脏，
回复侧随后读到"登录墙"，自己把自己绊停（23:01 实测主号白等 80 秒就是这么来的）。
`_login_reason` 也不会被笼统的 `login_timeout` 盖掉：界面那一列靠 `cookie_expired` / `login_uncertain`
定位问题。

Cookie 文件从此**不可能静默消失**，四层各留一手：`uncertain` 一个字节都不动；确认失效只挪进
`data/stale_cookies/`；`save_cookies` 覆盖写之前先把旧文件复制进 `data/cookie_backups/`（保留最近
`COOKIE_BACKUP_KEEP=5` 份，`browser_launcher.backup_cookie_file`）；界面「删除 Cookie」按钮也只归档。
两个目录都在 `.gitignore` 的 `data/` 里，不会跟着提交走。

`tools/two_account_login_check.py` 同批修正：它原先只排除 `login`/`passport`，把 `/web/user/`（BOSS 的登录页）
报成"已登录"，正好盖住了这次最要紧的坏消息；现在 `logged_in_from_url()` 明确排除 `/web/user`，并加
`--salvage`——Cookie 文件丢了但浏览器里还有登录项时回存一份（只补"文件不存在"，不覆盖已有文件）。

**改了配置没生效** → 先查 [配置生效范围](#配置生效范围重要)；仍不生效就是 bug。三端检测脚本能复现：`python tools/verify_three_way.py`。

**用 `BOSS_BOT_DRY_RUN=1` 起过面板之后** → 环境变量是最高优先级，但它会跟着 `to_dict()` 被算进"当前配置"：
在这种面板上点任何一次保存，`dry_run: true` 就被写进 `bot_config.json`，之后即使不带环境变量也会一直演练。
实测踩过（2026-09-30 跑判分复盘真机测试时）：验完记得 `python -c` 看一眼 `dry_run` 是不是你要的值。

**两个号串数据** → 检查各自 `cookie_file` 是否独立、`browser_data/account_N` 是否分开；`data/chatted_jobs.json` 是有意共享的。

**账号标签上是红点** → 红点只代表"这个账号的 Cookie 文件判为失效"，它现在随状态轮询自动重测（也可点一下强制重测）。要区分的是：左侧运行点为**黄**=在等你登录，**灰**=没启动，**蓝闪**=初始化中；右侧 Cookie 点为**红**才是要重新登录。若红点长期不灭，看 `logs/boss_bot.log` 里该账号有没有 `Cookie 已过期` / `登录态失效`。

**觉得多账号没在跑** → 日志按 `[主账号]` / `[账号2]` 前缀分别打印两侧轮次；界面上把数据范围切到该账号，看小计数（`投N·回M`）和记录是否变化；`curl http://localhost:5000/api/status` 会给出每个账号的 `phase`、`login_reason` 和各自 `stats`。

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
│   ├── greet_engine.py          # 打招呼：搜索/详情/AI 容灾链（判分预算、截断识别、硬否决）/发送
│   ├── reply_engine.py          # 回复：四级决策链 + AI 容灾尝试上限
│   ├── page_handler.py          # BOSS 页面操作与选择器
│   ├── metrics.py               # 漏斗指标持久化（累计/当日/按账号 + 回填）
│   ├── ai_health.py             # AI 接口体检（真实探测、错误分类、落盘合并、真实超时/截断回写）
│   ├── reply_record.py          # 投递/回复记录存储与 Excel 导出（按账号筛选/删除、AI 判分质量）
│   ├── message_store.py         # 聊天记录（会话身份=姓名+公司、data-mid 排序与去重、按账号前缀分文件）
│   ├── state_store.py / stats.py / notify.py / intent.py / rules.py
│   ├── prompts.py               # 回复提示词（每次现读规则/模板/画像，改了立刻生效）
│   └── self_evolve.py           # 回复效果记录与评估
├── flask-version/
│   ├── app.py                   # Flask + SocketIO（REST API、实时推送、体检后台线程）
│   └── templates/index.html     # 单页界面（毛玻璃、双主题、toast、账号数据范围）
├── tests/
│   ├── conftest.py              # 会话级隔离：测试不写生产数据
│   ├── test_unified.py          # 单元/集成测试（配置、引擎、闸门、结构检查）
│   ├── test_ai_failover.py      # AI 判分链路：不可用输出的容灾切换、截断、预算、硬否决、提示词热生效
│   ├── test_ai_health_runtime.py# 真实超时/截断回写体检 + 容灾链跳过
│   ├── test_ai_proxy_attribution.py # 代理 vs 直连归因：单次抽样不能定代理的罪
│   ├── test_message_sync.py     # 会话身份（姓名+公司）、data-mid 顺序与去重、卡片/动作分型
│   └── test_multi_account.py    # 多账号隔离与数据范围
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
