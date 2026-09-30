# BOSS 直聘页面现场（本仓库真机核过，2026-09-29/30）

本文件两种写法，含义不一样：

- `- 选择器 \`xxx\`` 开头的行 = xxx 在本仓库生产代码（`boss_bot/greet_engine.py`、
  `boss_bot/page_handler.py`）里逐字出现过，有测试逐条比对；生产改了这里没跟着改，测试会红。
- 写在正文里的选择器 = 只有真机观察，生产代码并不依赖它（靠按钮文本兜底），**不参与比对**。

别把正文里的选择器擅自升级成锁，也别为了让测试过去把锁降成正文。

## 岗位列表页（/web/geek/job-recommend 与搜索结果页）

- 选择器 `.job-card-wrap`、`.job-card-wrapper` — 岗位卡片容器，新列表页前者、老版后者
- 卡片里的岗位名/公司/薪资/地点是平铺文本，岗位链接在 `a[href*="job_detail"]`
  （生产代码不拿它当选择器，用的是 `"job_detail" in url` 这类判断）
- 新列表页**卡片上没有**沟通按钮：「立即沟通」在右侧详情栏（观察到的类名
  `A.op-btn.op-btn-chat`），必须先点卡片把详情打开再找按钮；生产代码找它靠的是
  `.btn btn-startchat` 加文本兜底，所以这条不锁
- 老式详情页 `/job_detail/*.html` 仍直接暴露沟通按钮，类名里有
  `btn-startchat`（CSS 等价写法 `a.btn.btn-startchat`）

## 打招呼的两套机制

1. **抽屉式**：点「立即沟通」→ 页面内出现输入框 → 填文案 → 点发送
   - 选择器 `#chat-input`、`.chat-input`、`.input-area`
   - 选择器 `textarea[placeholder*="回复"]`、`textarea[placeholder*="输入"]`
   - 选择器 `[contenteditable=true]`
   - 选择器 `.btn-send`、`.btn-v2.btn-sure-v2.btn-send`、`.send-message`、`.chat-send`
2. **平台自动发**：点「立即沟通」后弹出「已向BOSS发送消息 / 留在此页 / 继续沟通」，
   页面里没有可输入的抽屉
   - 该点的是「继续沟通」；**「留在此页」是什么都不做，不能点**
   - 点完会话可能开在当前页抽屉里，也可能新开一个标签页
   - 选择器 `.message-item.item-myself`、`.text-content` — 核对是否真发出去只认这两个：
     我方气泡里的这段文本才是"我发出去的"，对侧气泡是 item-friend，不算
   - 若气泡文本与本账号文案不一致，说明平台发的是它自己的预设 → 需要补发本账号文案
   - 读不到消息列表时**不许盲发**（宁可少发也不能重复骚扰）

## 会话页 / 已投递判定

- 选择器 `.friend-content` — 会话条目（左侧列表 40 条一屏）
- 按钮文案出现「继续沟通」= 该岗位之前已经投递过，要跳过
- SPA 换页后 `@eN` 引用全部失效：每次导航/开弹窗之后必须重新 observe/snapshot

## 页面会骗人，URL 也会

- 未登录时 `/web/geek/chat` 会**先渲染再跳** `/web/user`：看一眼 URL 就下结论必错，
  要等 URL 稳定（连读两次一致）
- 落在登录页但浏览器里仍有未过期登录项 = 两路证据矛盾，判"看不准"，不能判"已过期"
