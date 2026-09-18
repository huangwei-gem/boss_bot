# BOSS 直聘智能投递助手

> 自动打招呼 + 自动回复机器人，整合 auto_boss 与 BOSS-auto-reply-bot 两大开源项目，提供多账号管理、AI 智能解析、双标签页并行架构和 Flask Web 管理界面。

## 功能特性

### 核心能力

- **多账号同步管理** — 每个账号独立浏览器实例，互不干扰
- **自动打招呼** — 搜索岗位、自动发送打招呼消息、投递简历
- **自动回复** — 监控未读消息，四级决策链（关键词规则 → 意图识别 → AI 生成 → 兜底）
- **AI 智能解析** — 多 provider 容灾链、岗位匹配度评分分析
- **双标签页并行架构** — 打招呼与回复在同一浏览器中并行运行
- **Flask Web 管理界面** — 毛玻璃风格 UI，暗色/亮色双主题切换

### 回复引擎四级决策

| 优先级 | 决策层 | 说明 |
|--------|--------|------|
| 1 | 关键词规则 | `REPLY_RULES` 直接匹配，疑问/否定上下文过滤 |
| 2 | 意图识别 | 正则模式识别对方意图（邀约面试 > 询问薪资 > 要简历 > …） |
| 3 | AI 生成 | 多轮对话历史 + 个人画像动态提示词，OpenAI 兼容 API |
| 4 | 兜底策略 | AI 失败时可配置 skip / default 回复 |

### 反爬与容错

- 随机消息间隔、User-Agent 轮换
- 指数退避重试装饰器
- 验证码检测暂停 + Webhook 通知
- 浏览器断连自动重连、登录失效等待重新登录
- 去重管理（持久化已投递记录）

## 截图 / 演示

<!-- TODO: 添加 Web 界面截图 -->

![Web 管理界面](docs/screenshot-dashboard.png)

## 安装

### 环境要求

- Python 3.10+
- Chrome 或 Edge 浏览器（Windows / macOS / Linux）

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/huangwei-gem/boss_bot.git
cd boss_bot

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 bot_config.json（首次使用前必须修改）
#    - 填写打招呼话术、城市、搜索关键词
#    - 如需 AI 回复，配置 ai.providers
cp bot_config.json bot_config.json.bak  # 备份默认配置
```

## 使用说明

### 方式一：Flask Web 界面（推荐）

```bash
python flask-version/app.py
# 访问 http://localhost:5000
```

Web 界面提供：
- 启动/停止机器人、暂停/恢复打招呼和回复
- 实时日志推送（SocketIO）
- 配置编辑（浏览器、账号、话术、AI、规则）
- Cookie 上传、图片上传
- 统计数据查看、消息列表浏览
- 登录状态检测与确认

### 方式二：命令行模式

```bash
# 统一主循环（打招呼 + 回复，双标签页并行）
python -m boss_bot

# 仅打招呼引擎
python -m boss_bot --greet

# 仅回复引擎
python -m boss_bot --reply

# 启动 Flask Web 界面
python -m boss_bot --web
```

### 方式三：真机实测脚本

```bash
python login_and_test.py
```

流程：启动浏览器 → 手动登录 → 保存 Cookie → 创建双标签页 → 开始运行。

## 配置说明

### `bot_config.json`

| 字段 | 说明 |
|------|------|
| `browser` | 浏览器配置（headless、视口、超时、代理、浏览器类型） |
| `login` | 登录配置（等待超时、Cookie 文件路径） |
| `rate_limit` | 频率限制（每小时/每天最大操作数） |
| `retry` | 重试配置（最大次数、基础延迟、退避因子） |
| `ai` | AI 配置（enabled、api_key、model、providers 列表、匹配阈值） |
| `resume` | 简历信息（学校、专业、学位、技能、经验、目标岗位、自我介绍） |
| `accounts` | 多账号列表（每个账号独立配置：城市、搜索关键词、打招呼话术、图片） |
| `reply` | 回复配置（检查间隔、上下文消息数、每小时最大回复数、延迟范围） |
| `notify` | 通知配置（Webhook URL，支持企业微信/飞书/钉钉） |
| `log` | 日志配置（级别、保留天数、事件日志开关） |

### `user_profile.json`

个人画像配置，用于话术模板占位符替换和 AI 提示词生成：

```json
{
  "name": "张三",
  "position": "数据分析师",
  "salary_expectation": "15-20K",
  "skills": ["Python", "SQL", "Excel"],
  "experience": "3年数据分析经验",
  "available_interview_time": "随时可面试",
  "contact": "微信: xxx"
}
```

### AI Provider 配置

支持多 provider 容灾链，环境变量格式：

```bash
AI_PROVIDERS_1=sk-xxx|agnes-2.5-flash|https://apihub.agnes-ai.com/v1
AI_PROVIDERS_2=sk-yyy|deepseek-v4-flash|https://token.sensenova.cn/v1
```

或在 `bot_config.json` 的 `ai.providers` 中配置。

## 项目结构

```
boss_bot/
├── boss_bot/                    # 核心 Python 包
│   ├── __init__.py              # 包初始化（版本号）
│   ├── __main__.py              # 统一入口（--greet/--reply/--web）
│   ├── unified_config.py        # 统一配置管理（JSON + 环境变量 + 默认值）
│   ├── config.py                # 配置兼容层（模块级常量快捷访问）
│   ├── browser_launcher.py      # 跨平台浏览器管理器（DrissionPage 封装）
│   ├── greet_engine.py          # 自动打招呼/投递引擎
│   ├── reply_engine.py          # 自动回复引擎（四级决策链）
│   ├── main_loop.py             # 统一主循环（双标签页并行架构）
│   ├── page_handler.py          # 页面操作封装（CSS 选择器、聊天页面交互）
│   ├── intent.py                # 意图分类模块（正则模式识别）
│   ├── rules.py                 # 关键词规则引擎
│   ├── prompts.py               # AI 提示词模板（动态个人画像）
│   ├── message_store.py         # 消息存储（JSON 持久化 + 内存缓存）
│   ├── state_store.py           # 会话状态持久化（防重复回复/发简历）
│   ├── stats.py                 # 统计数据持久化（按天记录）
│   └── notify.py                # 通知模块（Webhook 推送）
├── flask-version/               # Flask Web 管理界面
│   ├── app.py                   # Flask + SocketIO 应用
│   └── templates/
│       └── index.html           # 单页应用（毛玻璃风格，双主题）
├── static/
│   └── dashboard/               # 上传的图片资源
├── tests/
│   ├── __init__.py
│   └── test_unified.py          # 单元测试（覆盖全部核心模块）
├── data/                        # 运行时数据（Cookie、统计、状态）
├── logs/                        # 日志文件
├── messages/                    # 聊天消息记录
├── bot_config.json              # 主配置文件
├── user_profile.json            # 个人画像配置
├── login_and_test.py            # 真机实测启动脚本
├── requirements.txt             # Python 依赖
├── pytest.ini                   # 测试配置
└── README.md
```

## 开发指南

### 运行测试

```bash
pytest
```

测试覆盖配置系统、规则引擎、意图分类、状态存储、统计、消息存储、通知、回复引擎、打招呼引擎、主循环和 Flask 应用，通过 mock/patch 隔离外部依赖。

### 代码风格

- 遵循 PEP 8
- 模块级 docstring 说明模块职责和核心设计
- 函数/类 docstring 使用 Google 风格
- 类型注解（typing）用于公共接口

### 添加新功能

1. 在 `boss_bot/` 包中实现核心逻辑
2. 在 `tests/test_unified.py` 中添加单元测试
3. 如需 Web 界面支持，在 `flask-version/app.py` 中添加 API 端点
4. 在 `flask-version/templates/index.html` 中添加前端交互

## 许可证

[MIT License](LICENSE) © huangwei-gem

## 致谢

本项目整合了以下两个开源项目的核心能力：

- **auto_boss** — 自动打招呼/投递引擎
- **BOSS-auto-reply-bot** — 自动回复消息机器人