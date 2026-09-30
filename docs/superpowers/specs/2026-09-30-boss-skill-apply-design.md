# boss-apply：基于 BrowserSkills 的可分发投递 Skill（子项目① 投递闭环）

日期：2026-09-30　状态：设计六节已向用户呈现，用户重复确认原始需求（含五条约束）视为通过
需求文档：本文件；后续实现计划由 writing-plans 产出

## 需求原文（用户 2026-09-30 第三次确认）

> 基于 BrowserSkills 二次开发一款独立 Skill，供多方用户复用。
> 前置条件：使用者本地已安装 BrowserSkills，且浏览器中保持 Boss 直聘 登录状态。
> 能力：加载本 Skill 后，自动在 Boss 直聘执行简历投递流程。
> 目标：打包成可分发的 Skill，其他人导入后，满足前置条件即可直接使用。

约束五条：① 复用 BrowserSkills 的浏览器控制能力，不重复实现浏览器驱动；② 仅自动操作页面投递按钮，不做账号注册、登录；③ 可配置投递筛选规则，支持多人独立使用；④ 操作日志输出，方便排查投递失败原因；⑤ 遵循页面交互逻辑，控制操作频率，降低风控风险。

## 已定口径（2026-09-29 逐条提问定的，别在没有新证据时翻案）

| 决策 | 选定 | 被否掉的替代 |
|------|------|--------------|
| 依赖形态 | **纯 skill、零安装依赖**：不 pip、不带 cloakbrowser | 让 skill 当现有 Flask 面板的远程前端（要求对方先装 Python + 依赖，违背"导入即可用"） |
| 谁来判分 | **跑 skill 的 agent 自己的模型**，不配外部 AI key | 复用 boss_bot 的 AI 容灾链（skill 形态里根本不该有 key 配置、429、假成功这一整类坑） |
| 第一版范围 | 对齐现有全部能力，**拆 3 个子项目**：① 投递闭环 ② 会话回复闭环 ③ 记录与界面。本 spec 只做 ① | 一次做完三件（一个 spec 装不下，且 ② 依赖 ① 的记录形状） |
| 运行形态 | **手动一轮一轮跑**（"帮我投 30 个" → 跑完汇报退出） | 挂 Qoder 定时任务 / 会话内长循环（回复侧因此是"下次喊我才看"，不按常驻轮询设计） |
| 分发边界 | 小范围分享 + **skill 内置四条红线** | 公开市场分发（风险由平台与用户共担，当前不需要） |

## 为什么必须贴 bsk 的真实接口

`~/.agents/skills/browser-skill/SKILL.md` 里三条是硬约束，设计绕不过去：

1. **强制会话生命周期**：`bsk session start` 拿到 4 位 session id → 每条命令带 `--session <id>` → 用完必须 `bsk session stop <id>`，官方明确"错误路径也要 stop"，且不要依赖 5 分钟空闲超时。
2. **写操作只作用于 Agent Window**：`bsk navigate/click/fill` 默认只影响 agent 标签页；用户自己的标签页只读，要 `bsk tab borrow` 才能动。→ 本 skill **一律在 Agent Window 里操作**，不去借用户的登录窗口，这一条同时是安全收益（不会误点用户正在看的页面）。
3. **引用会失效**：`@eN` ref 在导航后失效，必须重新 `bsk observe`/`snapshot`。BOSS 是 SPA，跳详情页/开弹窗都会换 DOM，所以"点一次就猜下一个 ref"必炸。

另外官方要求：页面需要人工输入（登录/验证码/OTP）或同一动作失败两次时，调 `bsk request-help` 而不是重试。这正好是红线②和风控处理的落点。

## 包结构

skill 源码放在**本仓库根目录 `boss-apply/`**（和 `boss_bot/`、`flask-version/` 平级），这样 DOM 事实与生产实现改动能在同一份 diff 里对照；对外只分发 `python -m scripts.package_skill boss-apply` 产出的 `.skill`，仓库里的 `boss_bot/` 不进包。

```
boss-apply/
├── SKILL.md                 # <500 行，只放：何时用 / 前置自检 / 一轮的生命周期 / 红线 / 失败归因入口
├── references/
│   ├── boss-dom.md          # 真机核过的选择器、两套打招呼机制的页面现场、SPA 换页时机
│   ├── failure-codes.md     # 失败归因判据表（每个 code 一个可观测判据 + 建议动作）
│   └── rules-format.md      # rules.json 字段说明与三份示例（校招/社招/换城市）
├── scripts/
│   ├── check_env.py         # 前置自检，纯标准库；跑不了就 agent 自己按清单手查
│   └── state.py             # 记录 / 去重 / 计数，纯标准库 JSON；agent 也可直接读写同一份文件
└── assets/
    ├── rules.example.json
    └── greeting.example.txt
```

frontmatter 只用白名单键 `{name, description, license, allowed-tools, metadata, compatibility}`——多余键会被 `skill-creator` 的 `quick_validate.py` 判失败。打包：`python -m scripts.package_skill boss-apply` 出 `.skill`（zip）。

### 两个脚本的接口（实现前定死，避免 agent 猜）

`scripts/check_env.py`——只做自检，绝不 launch 浏览器：

```
python scripts/check_env.py [--home ~/.boss-apply]     # 退出码 0=可以开跑，非 0=缺东西
  检查项：bsk 在不在 PATH → bsk doctor 的结论摘要 → BOSS_APPLY_HOME 可写 →
          rules.json 存不存在（不存在就打印"从 assets/rules.example.json 复制一份"）
  输出：每行一个 "OK|MISS|FAIL <检查名>：<结论 + 下一步动作>"，最后 JSON 汇总（--json 时）
```

`scripts/state.py`——记录、去重、计数，纯标准库，写 `~/.boss-apply/state/`：

```
python scripts/state.py seen <url> [--account A]              # 投过就 exit 0
python scripts/state.py record --json '<一行记录>'            # 发送即落库，回显 id
python scripts/state.py summary [--round <id>]                # 按 failure_code 分组的一轮汇总
python scripts/state.py count [--round <id>]                  # 本轮已打招呼条数（用来卡上限）
```

上限判定读的是 `state.py count`，不是 agent 的记忆——一轮里 agent 被压缩过上下文后"以为只投了 3 个"是真实风险，而多投的代价是骚扰 HR。


### 四层职责（每层一个接口，互不越界）

| 层 | 负责 | 明确不负责 |
|----|------|------------|
| 浏览器控制 | `bsk` 命令，唯一出口 | 自己起 CDP、Selenium、DrissionPage、requests 打接口 |
| 判分 | 跑 skill 的 agent 读 JD 后自己打分 | 外部 AI key、容灾链、重试 |
| 状态 | `scripts/state.py` 读写 `~/.boss-apply/state/` 下的 JSON | 存在 skill 目录里 |
| 编排 | `SKILL.md` 里的步骤文本 + 数量/频率/上限参数 | 常驻循环、定时任务 |

`SKILL.md` 第一步是**探测而不是假设**：`bsk` 在不在 PATH、`bsk doctor` 通不通。不通就停手，把要装什么讲清楚（`browser-skill` 扩展 + `bsk` CLI），**绝不自己降级去起浏览器**——那正是约束①禁止的事。

## 一轮的生命周期

```
1. 自检           bsk 可用 + Agent Window 能起 + BOSS 已登录（未登录 → request-help 交人工，永不代填）
2. 读配置         ~/.boss-apply/rules.json（城市、关键词、阈值、招呼语、每轮上限）
3. 开一轮         bsk session start → 记下 session id
4. 逐个关键词     navigate 搜索页 → observe → 取岗位卡片
5. 读 JD          进详情读任职要求（BOSS 新列表页 立即沟通 在右侧详情栏，不在卡片上）
6. agent 判分     分数 + 理由 + 不匹配时按边界带追问（阈值-15..阈值）
7. 打招呼         只对过阈值的岗位；两套机制都处理（点立即沟通 / 出现"已向BOSS发送消息·继续沟通"弹窗）
8. 落记录         每个岗位一条 JSON（含 stage + failure_code + 耗时），发送即落库
9. 收尾           bsk session stop <id> → 汇报"本轮投 N / 卡在 X 几件 / 失败原因分布"
```

频率与量的红线（对应约束⑤）：打招呼之间随机间隔（默认 6~14 秒，从 `rules.json` 读）；**每轮默认上限 20、硬上限 50**（超过 50 直接拒绝并说明为什么——BOSS 自己给单账号的上限是 150/天，skill 不该把人往那个数上推）；每轮开头打印一句风险声明：投递与打招呼的账号是使用者本人的，风控/封号风险由使用者自己承担。

## 配置与记录（对应约束③）

使用者的东西全在他自己目录，skill 目录只带 `.example`：

```
~/.boss-apply/
├── rules.json                    # 城市/关键词/阈值/招呼语/间隔/每轮上限
└── state/
    ├── rounds-YYYY-MM.json       # 每轮汇总（投了几个、失败分布）
    └── chatted.json              # 已投递 URL 去重表，跨轮生效
```

多人各用各的号：同一台机器上两个使用者用不同 `BOSS_APPLY_HOME` 就完全隔离；同一个人换号则是同一份 `chatted.json` 里带 `account` 字段区分。为什么记录必须在 skill 目录外：更新 skill 会覆盖目录内容，用户数据和 skill 版本混在一起迟早丢。

## 操作日志与失败归因（对应约束④）

每条记录带 `failure_code`，`state.py` 汇总时按 code 分组，一轮结束打印成人能看懂的一行：

| code | 判据（可观测） | 建议动作 |
|------|----------------|----------|
| `not_logged_in` | 页面落在 `/web/user` 或 `passport.`，且浏览器里没有未过期登录项 | 交人工登录，skill 永不代填手机号/验证码 |
| `wind_control` | 页面出现验证码/滑块/"访问频繁" | 立刻停本轮，`bsk request-help`，不重试 |
| `no_chat_button` | 详情页找不到 `A.op-btn.op-btn-chat` 且右侧栏无"立即沟通" | 落记录并跳过，按落点分档（不是同一原因） |
| `already_chatted` | URL 命中 `chatted.json` | 跳过（避免同一 HR 收到两条一样招呼） |
| `auto_greet_dialog` | 出现"已向BOSS发送消息/留在此页/继续沟通" | 按 `rules.json` 里该账号的招呼语文案处理，不盲发 |
| `jd_unreadable` | 详情页正文取不到 | 不判分、不打招呼（宁可不投也不能盲投） |
| `sent` | 招呼语出现在自己的消息气泡里（`.message-item.item-myself`） | 记为成功 |

判据表全部来自本仓库已经真机核过的结论（`references/boss-dom.md` 会注明来源与核过的日期），不是猜的选择器。

## 验证方式与一条必须挑明的冲突

用户的红线是"浏览器活必须走项目内 cloakbrowser"，而这个 skill 按"零依赖"跑在**使用者自己的真实浏览器**（bsk 扩展）里。两者不能同时满足：

- 我**能**验的：`check_env.py` / `state.py` 的纯函数行为（pytest，离线）；`SKILL.md` 的 frontmatter 过 `quick_validate`；`references/boss-dom.md` 里每条 DOM 事实与本仓库既有真机核过的代码逐条比对；打包出的 `.skill` 目录结构。
- 我**不能**验的：别人机器上"导入即用"的第一次真机跑通。那一遍需要装了 `bsk` + browser-skill 扩展的人来跑，我提供自检脚本和 5 分钟上手清单。
- 代价说清楚：不能替你保证首次上手零摩擦；能保證的是摩擦点会被 `failure_code` 归因出来，而不是静默失败。

## 三个子项目的顺序

1. **① 投递闭环**（本 spec）：搜岗位 → 读 JD → 判分 → 两套机制打招呼 → 落去重记录。**必须自带最小记录**，否则下一轮不知道哪些投过。
2. ② 会话回复闭环：读未读会话 → 判意图 → 回消息/发简历。依赖 ① 的记录形状定稿。
3. ③ 记录与界面：一轮一份静态 HTML 报告（替代面板）。依赖 ①② 的数据。

## 明确不做

- 不写任何浏览器驱动、不碰 CDP/Selenium/DrissionPage（约束①）。
- 不做注册、登录、代填手机号或验证码（约束② + 红线）。
- 不带外部 AI key、不做容灾链重试（口径 2）。
- 不挂定时任务、不在会话里长循环（口径 4）。
- 不在 skill 目录里写用户数据或记录。
- 不碰真机时点任何"发送/打招呼/发简历"按钮——本仓库内的所有验证沿用既有红线。
