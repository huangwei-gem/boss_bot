# 贡献指南

感谢你对 BOSS 直聘智能投递助手项目的关注！欢迎提交 Issue 和 Pull Request。

## 提交 Issue

- 请先搜索已有 Issue，避免重复提交
- 描述清楚问题现象、复现步骤、环境信息（OS、Python 版本、浏览器）
- 如果是功能建议，请说明使用场景和期望效果

## 提交 Pull Request

### 流程

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 提交代码：`git commit -m "feat: 添加 xxx 功能"`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

### 代码规范

- 遵循 PEP 8 风格
- 新功能必须包含单元测试（`tests/test_unified.py`）
- 所有测试通过：`pytest`
- 不要提交敏感信息（API Key、Cookie、密码等）
- 不要提交运行时生成的文件（`data/`、`logs/`、`messages/`、`*.xlsx`）

### Commit 消息格式

```
<type>: <description>

[type] 可选值：
  feat     新功能
  fix      修复 Bug
  refactor 重构
  test     测试相关
  docs     文档
  chore    构建/工具
```

### 开发注意事项

- 回复引擎的四级决策链（规则 → 意图 → AI → 兜底）是核心架构，修改时注意保持优先级顺序
- `browser_launcher.py` 需要同时兼容 Windows / macOS / Linux
- Flask Web 界面使用 SocketIO 实时推送，新增状态字段时同步更新前端
- 配置变更需同时更新 `unified_config.py`（数据类）和 `config.py`（兼容层常量）