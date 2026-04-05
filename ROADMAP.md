# GamePulse AI 迭代路线图

> 本文档跟踪 PRD v1.0 中尚未实现的能力。Phase 1 已完成（见 commit `7f7a7c3`）。
>
> 维护约定：
> - 每个模块标注所属 PRD 章节 + 当前状态
> - 完成后移到 `## 已完成` 段落并标注 commit
> - 新增需求先登记到"候补"区，评审后进入 Phase

---

## 总览

| Phase | 范围 | 状态 |
|---|---|---|
| **Phase 1** | 核心闭环（数据 + 评分 + 告警 + LLM 战报 + IAA 顾问） | ✅ 已完成 `7f7a7c3` |
| **Phase 2** | 扩展能力（订阅中心 + 赛道分析 + 社媒深化 + 认证） | 📋 规划中 |
| **Phase 3** | 决策智能（自动立项建议 + 商业化实验引擎 + 多团队） | 📋 规划中 |
| **Phase 4** | 运营工具（OCR + 截图素材分析 + Trailer 拆解） | 🔬 研究中 |

---

## Phase 2 — 扩展能力

### P2-1 订阅中心与日报 `PRD §9.6`

**价值**：用户能按自己关心的维度订阅，每天收到定制日报。

**差距**：当前告警是单一规则触发，缺"按品类/地区/关键词/具体游戏"的订阅维度，缺日报模板。

**交付物**：
- 新增 `Subscription` 表：`userId, dimension (platform/genre/region/keyword/game), value, channel (feishu/wecom/email), schedule (cron), isActive`
- 日报生成器 `workers/src/processors/daily_digest.py`：每天固定时间跑，为每个订阅者生成定制内容
- 飞书卡片日报模板：昨日新增高潜力 Top5 + 榜单异动 Top10 + 社交爆发 Top10 + IAA 候选 Top5
- 企业微信 + 邮件通道（飞书已接）
- Web UI：`/subscriptions` 页面管理订阅

**预估工作量**：3 agents（订阅模型、日报生成器、订阅 UI），约 2-3 天

**依赖**：需要先做 P2-4 用户系统（订阅要绑定到 User）

---

### P2-2 赛道/竞品分析 `PRD §9.5`

**价值**：从单游戏分析上升到赛道视角，回答"哪个品类最近 7 天最热"、"这游戏的相似竞品是谁"。

**差距**：完全没有赛道聚合视图和相似推荐。

**交付物**：
- 新增 `Genre` 聚合表（冗余字段，每日刷新）：`key, label, hotGamesCount, momentum, iaaBaseline`
- `GameEmbedding` 生成任务（schema 已建好）：
  - 用 OpenAI `text-embedding-3-small` 或 Voyage 做 embedding
  - 输入：`游戏名 + 品类 + 描述 + Top 5 评论主题`
  - 每周全量或增量刷新
- 相似游戏推荐 API：`/api/games/[id]/similar` 使用 `pgvector` 余弦相似
- 新增页面 `/genres`（赛道榜单）+ `/genres/[key]`（单赛道详情）
- 游戏详情页新增"相似游戏"卡片

**预估工作量**：2-3 agents（embedding 管线、赛道聚合、相似页面），约 3 天

**依赖**：无

**注意**：
- Poe 主要是 chat completion，**embedding 需要单独通道**（OpenAI 或 Voyage）
- 或者评估 BGE-M3 本地模型（开源，中英双语，维度 1024）—— 若团队有 GPU，这是更省钱的选择

---

### P2-3 社媒舆情深化 `PRD §10.4`

**价值**：从"数值"升级到"内容"——不只知道多少播放量，还要知道传播点是什么。

**差距**：当前 `social_signals` 只有 video_count/view_count/like_count 汇总。

**交付物**：
- 新增 `SocialContentSample` 表：`gameId, platform, contentType (video/post), title, hashtags[], viewCount, postedAt, hookPhrase`
- 扩展社媒抓取器：TikHub API（抖音/TikTok 已在 env）+ YouTube Data API + Bilibili API 实际调用
- 内容抽取任务：对抓到的视频标题/描述用 Haiku 抽取"吸睛点"（hook phrase）
- 游戏详情页新增"传播内容样本"卡片（展示 top 5 视频标题 + 热词云）

**预估工作量**：2 agents（社媒深度抓取、hook 抽取 + UI），约 2 天

**依赖**：无

---

### P2-4 用户认证与 RBAC `PRD §13`

**价值**：多人使用，区分权限（谁能改阈值、谁能导出战报）。

**差距**：完全无登录，无权限。

**交付物**：
- 新增 `User / Session / Account` 表（NextAuth adapter 管）
- NextAuth.js 集成，支持邮箱 + Google OAuth
- 5 种角色（PRD §13.1）：super_admin / analyst / publisher / monetization / viewer
- 权限中间件覆盖 PRD §13.2 五类能力：
  - 新增监控对象
  - 修改预警阈值
  - 导出战报
  - 配置推送
  - 人工修正标签
- 登录页 `/login` + 用户设置 `/account`
- 审计日志：`AuditLog` 表记录敏感操作

**预估工作量**：2 agents（auth 后端、权限 UI），约 2-3 天

**依赖**：无（但订阅中心 P2-1 依赖此）

---

### P2-5 评论抓取器多语言 + 更多平台

**价值**：当前 4 个评论抓取器覆盖西方 + 中国主流，但还有盲区。

**差距**：
- TapTap 抓取器有 Playwright 兜底但 selectors 需要实际验证
- 4399 / 微信小游戏评论未抓
- iOS App Store 评论 RSS 仅限美区英语，其他区域需额外抓取

**交付物**：
- TapTap 评论抓取器 selectors 验证 + 修复
- 4399 评论抓取器
- App Store 多区评论抓取（CN/JP/KR 等）
- 评论语言自动检测（用 `langdetect` 或 Claude Haiku 一次调用）

**预估工作量**：1 agent，约 1 天

**依赖**：无

---

## Phase 3 — 决策智能

### P3-1 赛道增长信号自动报告

**价值**：每周自动生成"本周哪个品类最热"、"哪个品类口碑上升最快"报告。

**差距**：无自动化赛道趋势报告。

**交付物**：
- 新增 `workers/src/processors/genre_trends.py`
- 每周一早上跑，对每个品类聚合：`新增高热游戏数 / 口碑增速 / IAA 适配度均值 / 广告活跃度`
- Poe Sonnet 生成叙述性周报
- 通过订阅中心推送

**依赖**：P2-1 订阅中心

---

### P3-2 自动立项建议引擎

**价值**：对 Top 30 潜力游戏，自动生成"要不要立项"简报。

**差距**：当前战报有玩法拆解和 IAA 建议，但无明确的"建议立项/观望/放弃"结论。

**交付物**：
- 扩展 `GameReport.payload` 增加 `project_advice` 字段：
  ```json
  {
    "recommendation": "pursue" | "monitor" | "pass",
    "reasoning": "...",
    "similar_shipped_projects": ["..."],
    "resource_estimate_weeks": 8,
    "risk_factors": ["..."],
    "confidence": 0.75
  }
  ```
- Opus 级别的综合分析 prompt
- 新增 Web 页面 `/projects/candidates` 管理立项候选池

**依赖**：P2-2 相似推荐（知道"类似已上线项目"需要向量检索）

---

### P3-3 商业化实验建议引擎

**价值**：输出具体的 A/B 实验参数包（比 IAA advice 里的 ab_test_order 更详细）。

**差距**：当前 ab_test_order 只有顺序，无具体参数和成功标准。

**交付物**：
- 新增 `Experiment` 表：`gameId, hypothesis, variant_a, variant_b, success_metric, sample_size, status`
- 实验模板库（Rewarded revive / double reward / session-end interstitial 等）
- Web UI：`/experiments` 管理实验包

**依赖**：无

---

### P3-4 多团队协作与权限

**价值**：一个部署服务多个业务线，游戏池隔离。

**差距**：无 workspace 概念，所有数据都共享。

**交付物**：
- `Workspace / WorkspaceMember` 表
- 大多数查询加 `workspace_id` 过滤
- Workspace 切换 UI

**依赖**：P2-4 用户系统

---

## Phase 4 — 运营工具

### P4-1 截图 OCR + 素材拆解

**价值**：从游戏截图/广告素材中自动识别关键元素（UI 布局、色调、卖点文字）。

**差距**：当前只存截图 URL，无内容分析。

**交付物**：
- Playwright 截屏能力扩展（已有基础）
- OCR：Tesseract 或 PaddleOCR 提取截图中的文字
- GPT-4V / Claude-Vision 做素材语义分析
- 新增 `GameAssetAnalysis` 表存储结果

**依赖**：Poe 是否支持多模态，或走 OpenAI 直连

---

### P4-2 Trailer 自动拆解

**价值**：从游戏预告片中自动提取"前 3 秒钩子"、"节奏分布"、"情绪曲线"。

**差距**：无视频内容分析。

**交付物**：
- 视频下载 + 帧抽取
- Whisper 做配音转写
- Claude/GPT 分析钩子模式
- 帮买量团队设计素材

**依赖**：GPU 资源或云服务

---

### P4-3 飞书机器人双向交互

**价值**：用户可以在飞书里直接问"帮我分析 XXX 游戏"，不用切回 web。

**差距**：当前只是单向推送。

**交付物**：
- 飞书机器人 bot 接入
- 自然语言 → 内部 API 调用
- 核心命令：`/analyze <game>`, `/iaa <game>`, `/similar <game>`, `/trending <genre>`

**依赖**：P2-4 用户系统（权限校验）

---

## 技术债与小改进（滚动清单）

这些不属于主迭代线，但值得持续改进：

### 数据质量
- [ ] **冗余指标回填**：`Genre.hotGamesCount / momentum` 每日刷新任务（依赖 P2-2）
- [ ] **评分公式版本化**：`PotentialScore.algorithmVersion` 已有字段，但无历史版本对比 UI
- [ ] **IAA 适配分解耦**：`iaa_adapted` 标签当前靠人工打，需要加 UI 触发（`web/app/admin/` 扩展）
- [ ] **人工去重纠错 UI**：当前去重靠 pg_trgm 0.85 阈值，缺"合并/拆分"UI（PRD §10.1）

### 性能
- [ ] **`ranking_snapshots` 分区**：表增长最快，按月 `PARTITION BY RANGE (snapshot_date)`，保留近 12 月
- [ ] **Dashboard 查询缓存**：首屏 4 榜单每次全量查询，加 Redis 缓存 5 分钟
- [ ] **评论批量 embedding**：P2-2 实现后，考虑评论也做 embedding 存储

### 稳定性
- [ ] **Playwright 池化**：当前每次抓取都新建浏览器，考虑引入 `playwright-pool`
- [ ] **代理池接入**：`SCRAPE_PROXY_URL` 已留位但未接动态代理
- [ ] **成本监控告警**：Poe API 每日花费 > 阈值时自动告警到飞书

### 可观测性
- [ ] **任务监控页**（PRD §17）：`scrape_jobs` 表有数据但无 Web UI
- [ ] **主档管理页**（PRD §17）：`Game` 表的人工编辑 UI
- [ ] **LLM 质量抽查**：随机抽 5% 的 GameReport 输出做人工评审

### 测试
- [ ] **集成测试**：`workers/tests/` 目录空，需要补基础测试
- [ ] **Prompt 回归测试**：记录固定输入的 LLM 输出，版本升级时对比

---

## 候补需求池

> 未评审的新想法放这里。进入 Phase 前需要评估价值/成本。

- [ ] **AppAnnie / data.ai 数据接入**（付费数据源，补齐下载量预估）
- [ ] **海外运营商/发行商数据库**（收集各地区主要买量商的案例）
- [ ] **用户评分游戏机制**（让运营用户自己给游戏打"爽点标签"，众包校准 LLM 输出）
- [ ] **CI/CD 流水线**（GitHub Actions 自动部署）

---

## 迭代节奏建议

- **Phase 2 推荐顺序**：P2-4 认证 → P2-1 订阅 → P2-2 赛道 → P2-3 社媒 → P2-5 评论扩展
- **Phase 3 触发条件**：Phase 2 稳定运行 1 个月后再启动
- **Phase 4**：视资源和业务需求独立评估

每个 Phase 结束后更新本文档，把完成项移到"已完成"段落。
