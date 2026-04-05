# DreamBig / GamePulse AI

爆款游戏监控与 IAA 改造分析平台。7×24 小时自动追踪 Steam / Google Play / App Store / TapTap 等平台的游戏榜单、评论、社交反馈，自动输出爆款预警、玩法拆解、IAA 改造建议。

## 架构

- **web/** — Next.js 16 + Prisma 6 + Tailwind 4 前端（Dashboard / 游戏库 / 告警 / IAA 顾问）
- **workers/** — Python 3.11+ 采集 + 分析后端（scrapers + processors + LLM）
- **shared/** — 跨层共享的 JSON 配置（品类表、评分权重）
- **PostgreSQL 16** + pg_trgm + pgvector
- **Redis 7** + rq 队列 + APScheduler 调度
- **Caddy** 反向代理（自动 HTTPS）

## 快速开始

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 POE_API_KEY 和 FEISHU_WEBHOOK_URL

# 2. 启动所有服务
docker-compose up -d

# 3. 初始化数据库
cd web && npx prisma migrate deploy   # 或 npx prisma db push

# 4. 查看前端
open http://localhost:3000
```

## 主要功能（Phase 1）

- 多平台游戏榜单抓取（10+ 平台）
- 评论抓取 + 情绪分析 + 主题聚类（Poe API Haiku / Sonnet）
- LLM 战报生成（玩法机制拆解 + IAA 改造建议）— Poe API Opus
- 7 维度潜力评分
- 5 类预警（P1 / P2 / P3 分级）+ 飞书推送
- IAA 顾问独立页

## CLI 工具

```bash
cd workers

# 抓取评论
python -m src.worker scrape_reviews

# NLP 流水线
python -m src.worker classify_sentiment
python -m src.worker extract_topics
python -m src.worker cluster_topics

# 生成战报（前 20 个高潜力游戏）
python -m src.worker generate_reports 20

# 单独跑某平台榜单抓取
python -m src.worker app_store top_free US
```

## 调度时间表

（调度以 HKT 为准，所有时间为北京 / 香港时间）

| 时间 | 任务 |
|---|---|
| 06:00 | 采集各平台榜单（Google Play / App Store / TapTap / Steam / Poki / CrazyGames） |
| 06:45 | 补抓游戏截图 / 详情 |
| 07:00 | 采集社交媒体信号 |
| 07:30 | 采集广告情报 |
| 08:00 | 重新计算潜力评分 |
| 08:30 | 评估预警规则 + 飞书推送 |
| 09:00 | 抓取高潜力游戏的评论 |
| 10:00 | 评论情绪分类（Haiku 批处理） |
| 10:30 | 评论主题抽取（Haiku） |
| 11:00 | 主题聚类（Sonnet） |
| 11:30 | 生成游戏战报（Opus，top-20） |
| 14:00 | 二次拉取主要平台榜单 |
| 每 5 分钟 | 轮询 web 手动触发的任务 |

## 模型路由

| 任务 | 模型 | 平均成本 |
|---|---|---|
| 情绪分类 | Claude-Haiku-4.5 | 极低 |
| 主题抽取 | Claude-Haiku-4.5 | 极低 |
| 主题聚类 | Claude-Sonnet-4.6 | 中 |
| 战报生成（玩法 + IAA） | Claude-Opus-4.6 | 高 |

所有 LLM 调用走 Poe API（https://api.poe.com/v1，OpenAI 兼容）。

## 文档

- [DEVELOPMENT.md](./DEVELOPMENT.md) — 本地开发 / 扩展指南
- [prd.txt](./prd.txt) — 产品需求文档
