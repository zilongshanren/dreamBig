# DreamBig 开发指南

## 先决条件 / Prerequisites

- **Docker Desktop** ≥ 24（或独立 Postgres 16 + Redis 7）
- **Node.js** ≥ 20，**pnpm** 或 **npm**
- **Python** ≥ 3.11 + **uv** 或 **pip**
- **Poe API key**（https://poe.com/api_key）
- 部分区域（Google Play / App Store 非本区榜单）需要代理：配置 `SCRAPE_PROXY_URL`

## 本地开发（Docker 全家桶）

```bash
cp .env.example .env
# 填入 POE_API_KEY、FEISHU_WEBHOOK_URL
docker-compose up -d db redis web scraper scheduler
cd web && npx prisma db push      # 首次建表
```

## 本地开发（不用 Docker）

```bash
# 1. 启动 Postgres + Redis（本地安装）
brew install postgresql@16 redis pgvector
brew services start postgresql@16 redis
createdb dreambig
psql dreambig -f scripts/init-db.sql

# 2. web
cd web && npm install
npx prisma generate && npx prisma db push
npm run dev

# 3. workers
cd workers && python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m src.scheduler          # 调度器
rq worker --url redis://localhost:6379/0 --path .   # 另开一个终端
```

## 手动触发单个 scraper

```bash
cd workers
python -m src.worker <scraper_name> [args...]

# 示例
python -m src.worker app_store top_free US
python -m src.worker google_play top_free CN
python -m src.worker scrape_reviews          # 扫描全部高潜力游戏
python -m src.worker generate_reports 1      # 只跑 top-1，快速调试 LLM
```

## 新增一个 Scraper

1. 在 `workers/src/scrapers/<platform>/` 新建 `my_scraper.py`
2. 继承 `BaseScraper`，实现 `fetch()` 返回 `list[RankingItem]`
3. 在 `workers/src/worker.py` 注册 CLI 入口
4. 在 `workers/src/scheduler.py` 注册 cron（如需要）

## 新增一种 Alert 类型

1. 编辑 `workers/src/processors/alerting.py`，添加新的 evaluator 函数
2. 在 `alerts` 表 INSERT 一条规则（`alert_type = 'my_new_type'`, 设置 `severity` 和 `conditions`）
3. Alert 引擎会在 `evaluate_alerts` 任务中自动挑选未处理的规则

## 调整潜力评分权重

修改 `shared/scoring_weights.json` 的 `weights` 节（7 个维度加起来必须 = 1.0），改完重启 scraper 容器即可。阈值（`thresholds.high_potential` 等）也在同文件。

## 新增 LLM Prompt

1. 在 `workers/src/llm/prompts/` 新建 `my_prompt.py`
2. 暴露 `SYSTEM_PROMPT` 和 `build_user_prompt(...)`，以及 `PROMPT_VERSION` 常量
3. 在 processor 里通过 `PoeClient` 调用（模型在 prompt 模块里选）

## Schema 变更

```bash
# 开发期：直接 push，不生成迁移文件
cd web && npx prisma db push

# 生产期：生成迁移 SQL，review 后 apply
cd web && npx prisma migrate dev --name my_change
npx prisma migrate deploy
```

手写 SQL 迁移见 `web/prisma/migrations/*.sql`（如 `20260405_phase1_iteration.sql`）。

## 调试 / Debugging

- **scrape_jobs 表**：每次调度任务的状态 / 错误信息
- **rq dashboard**：`pip install rq-dashboard && rq-dashboard -u redis://localhost:6379/0`
- **scheduler 日志**：`docker-compose logs -f scheduler`
- **worker 日志**：`docker-compose logs -f scraper`
- **单独调 LLM**：`python -m src.worker generate_reports 1` 只跑 1 个游戏，看完整 prompt / 返回

## 常见问题

- `pgvector` 扩展未加载：手动执行 `CREATE EXTENSION vector;`
- `POE_API_KEY` 无效：检查 https://poe.com/api_key 页面是否重置
- Google Play scraper 返回 403：配置 `SCRAPE_PROXY_URL` 走住宅代理
- Prisma `Unsupported("vector(1536)")` 报错：确认 `vector` 扩展已安装

## 代码风格

- **Python**：ruff（pyproject.toml 已配置 E/F/I/N/W），行宽 100
- **TypeScript**：eslint + Next.js 默认
- **提交信息**：`feat: ...` / `fix: ...` / `docs: ...`（conventional commits）
