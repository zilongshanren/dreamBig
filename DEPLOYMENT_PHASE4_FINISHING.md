 # Phase 4 Finishing 部署手册

> 本次部署涵盖：多团队 workspace（P3-4）+ Trailer 自动拆解（P4-2）+ 运营 admin 页 + ranking_snapshots 月分区 + 测试套件 + PRD 评分公式对齐。
>
> **适用场景：增量部署到已有的 HK VPS（docker-compose）。**
>
> 预估停机时间：5–10 分钟（DB 迁移期间）。

---

## 0. 部署前必读

### 0.1 改动汇总

| 类别 | 改动 | 影响 |
|---|---|---|
| **DB schema** | 2 个新迁移文件：workspace + ranking_snapshots 分区 | 必须先于 web 容器启动 |
| **Prisma schema** | 新增 `Workspace` / `WorkspaceMember`，5 张表加 `workspaceId` FK | 触发 web 镜像重建（`prisma generate`）|
| **workers 依赖** | `pyproject.toml` 加 `yt-dlp>=2024.0` | 触发 workers/scheduler 镜像重建 |
| **workers 系统依赖** | `Dockerfile` 加 `ffmpeg` | 触发 workers/scheduler 镜像重建 |
| **workers 代码** | 新增 `trailer_analysis.py` + `prompts/trailer_analysis.py`；修改 `alerting.py`、`vision_client.py`、`worker.py`、`scheduler.py` | 容器重启生效 |
| **scheduler cron** | 新增周三 03:00 HKT 的 `trailer_analysis` 任务 | scheduler 容器重启生效 |
| **web 路由** | 3 个 admin 子页 + 4 个 admin API 路由 + workspace switcher 嵌入 sidebar | 容器重启生效 |
| **环境变量** | **无新增** | 不需要改 `.env` |

### 0.2 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| workspace 迁移把现有数据搬到 default workspace 失败 | 中 | 迁移前 pg_dump 全库 |
| ranking_snapshots 分区改造期间数据被锁 | 中 | 迁移前停所有 worker（不让新数据写入），迁移完再起 |
| Prisma client 在 web 容器内未刷新导致运行时报 unknown field | 高 | 重建 web 镜像（`docker compose build web --no-cache`） |
| ffmpeg 缺失导致 trailer 分析 crash | 低 | 已在 Dockerfile 加 ffmpeg；优雅降级也已实现 |
| 老旧 alert 没有 workspace_id 触发 NOT NULL 约束 | 低 | 迁移内置 backfill `UPDATE ... SET workspace_id='default'` |

### 0.3 前置条件

- VPS 上 PostgreSQL **必须 ≥ 11**（分区表 + FK 支持）。可在 db 容器里跑 `psql -U dreambig -c "SELECT version();"` 确认。
- VPS 上有足够磁盘：分区迁移会临时复制 `ranking_snapshots` 表（占用 ≈ 等量空间），完成后释放。
- 你能 SSH 到 VPS 并有 docker 权限。

---

## 1. 备份（必做）

```bash
# 在 VPS 上
cd /path/to/dreamBig

# 全库 dump，落地到带时间戳的文件
docker compose exec -T db pg_dump -U dreambig dreambig \
  > backup_pre_phase4_finishing_$(date +%Y%m%d_%H%M%S).sql

# 验证 dump 不为空
ls -lh backup_pre_phase4_finishing_*.sql
```

> ⚠️ 不要跳过这一步。两个迁移都改 schema 结构，万一回滚需要这份 dump。

---

## 2. 拉取新代码

```bash
git fetch origin
git status                       # 确认 working tree 干净
git log --oneline -5              # 记录当前 HEAD 用于回滚
git pull origin main
```

---

## 3. 暂停可能写入受影响表的服务

为避免分区迁移期间 `ranking_snapshots` 被写入导致竞态：

```bash
docker compose stop scraper scheduler
```

> web 容器先**保留运行**，让用户继续读旧数据。等迁移完再重启。

---

## 4. 应用数据库迁移

### 4.1 Workspace 迁移

```bash
docker compose exec -T db psql -U dreambig -d dreambig \
  < web/prisma/migrations/20260408_workspace.sql
```

预期输出（关键行）：
```
CREATE TABLE
INSERT 0 1                       # default workspace 创建
INSERT 0 N                       # 现有 N 个用户加入 default workspace
ALTER TABLE                      # alerts 加 workspace_id
UPDATE M                         # backfill 现有 M 行 alerts
ALTER TABLE                      # 把 workspace_id 改为 NOT NULL
... (subscriptions / experiments / game_tags / audit_logs 同样的 5 段)
```

**验证**：
```bash
docker compose exec -T db psql -U dreambig -d dreambig -c "
  SELECT 'workspaces' AS t, COUNT(*) FROM workspaces
  UNION ALL SELECT 'workspace_members', COUNT(*) FROM workspace_members
  UNION ALL SELECT 'alerts_with_ws', COUNT(*) FROM alerts WHERE workspace_id IS NOT NULL
  UNION ALL SELECT 'subs_with_ws', COUNT(*) FROM subscriptions WHERE workspace_id IS NOT NULL;
"
```

应该看到 `workspaces ≥ 1`，且其它行的计数等于该表的总行数。

### 4.2 Ranking snapshots 分区迁移

```bash
docker compose exec -T db psql -U dreambig -d dreambig \
  < web/prisma/migrations/20260409_ranking_snapshots_partition.sql
```

> 这一步对大表（千万级行）可能耗时 30 秒到 5 分钟。期间会：
> 1. 重命名旧表 → `ranking_snapshots_old`
> 2. 建分区父表 + 15 个月度分区（过去 12 月 + 未来 3 月）
> 3. 全量复制数据
> 4. drop 旧表
> 5. 重建索引

**验证**：
```bash
docker compose exec -T db psql -U dreambig -d dreambig -c "
  SELECT
    pt.partrelid::regclass AS parent,
    COUNT(*) AS partition_count,
    SUM(pg_total_relation_size(c.oid)) AS total_bytes
  FROM pg_partitioned_table pt
  JOIN pg_inherits i ON pt.partrelid = i.inhparent
  JOIN pg_class c ON i.inhrelid = c.oid
  WHERE pt.partrelid::regclass::text = 'ranking_snapshots'
  GROUP BY pt.partrelid;
"
```

应该看到 `parent=ranking_snapshots`，`partition_count` ≥ 15。

```bash
# 抽样校验数据没丢
docker compose exec -T db psql -U dreambig -d dreambig -c "
  SELECT COUNT(*) FROM ranking_snapshots;
"
```

把这个数字和迁移**前**的数字对比（迁移前可以提前查一次）。两者应该相等。

> ⚠️ 如果分区迁移失败，**不要急着回滚**，先看错误信息。如果是 PG 版本太老（< 11），需要先升级 db；如果是磁盘满了，先清空间。已经成功的 workspace 迁移不需要回滚。

---

## 5. 重建并启动新镜像

### 5.1 重建 web（拉取新 schema + prisma generate）

```bash
docker compose build web --no-cache
```

> `--no-cache` 是必须的：保证 `prisma generate` 在新 schema 上跑，而不是缓存里的老版本。

### 5.2 重建 workers 和 scheduler（含 ffmpeg + yt-dlp）

```bash
docker compose build scraper scheduler --no-cache
```

> 同样用 `--no-cache`，因为 Dockerfile 改了（加了 `ffmpeg`），还有 `pyproject.toml` 加了 `yt-dlp`。

镜像构建成功后，scheduler 镜像里应该有 ffmpeg：

```bash
docker compose run --rm scheduler ffmpeg -version | head -1
docker compose run --rm scheduler yt-dlp --version
```

两个命令都应该有版本号输出。

### 5.3 滚动重启

```bash
# web 先重启（新 prisma client + workspace 切换器）
docker compose up -d web

# scheduler 重启（新增的 trailer cron 生效）
docker compose up -d scheduler

# 最后起 scraper（2 个 replica）
docker compose up -d scraper
```

---

## 6. 部署后验证

### 6.1 容器健康

```bash
docker compose ps
```

所有服务都应该 `running (healthy)` 或 `running`，没有 `restarting`。

```bash
# 查最近 100 行日志确认没有崩溃循环
docker compose logs --tail=100 web
docker compose logs --tail=100 scheduler
docker compose logs --tail=100 scraper
```

### 6.2 Web 功能

打开浏览器或用 curl：

```bash
# 健康检查（已登录用户）
curl -I https://your-domain/

# Workspace API
curl https://your-domain/api/workspaces \
  -H "Cookie: <your-session-cookie>"
```

手动浏览验证：
- [ ] sidebar 顶部出现 "工作区" 切换器，显示 "默认工作区"
- [ ] `/admin` → 旧的管理总览还在
- [ ] `/admin/jobs` → 显示 scrape_jobs 列表（新页）
- [ ] `/admin/games` → 游戏主档列表 + 编辑入口（新页）
- [ ] `/admin/duplicates` → pg_trgm 候选列表（可能为空，正常）
- [ ] `/alerts` → 现有告警仍可见（属于 default workspace）
- [ ] `/subscriptions` → 现有订阅仍可见

### 6.3 Scheduler cron 注册

```bash
docker compose logs scheduler | grep -E "trailer_analysis|registered"
```

应该看到一行 `trailer_analysis: cron[day_of_week='wed', hour='3', minute='0']` 之类的输出。

### 6.4 Workers 测试

如果你想在 VPS 上验证测试套件：

```bash
docker compose run --rm scraper python -m pytest tests/ -x --tb=short
```

应该输出 `59 passed`。

### 6.5 手工触发 trailer 分析（可选烟囱测试）

```bash
docker compose run --rm scraper python -m src.worker trailer_analysis 1
```

预期日志包含：
- `Starting trailer hook analysis`
- 找到一个 trailer URL
- yt-dlp 下载完成
- ffmpeg 抽帧完成
- vision 分析完成 + 写入 game_asset_analysis

如果系统里没有任何 youtube/bilibili 类型的 social_content_samples，会跳过并打印 "no trailer found"，这也算正常。

---

## 7. 回滚方案

> 仅当部署后出现严重问题且短期无法修复时执行。

### 7.1 代码回滚

```bash
git reset --hard <PRE_DEPLOY_COMMIT>
docker compose build web scraper scheduler --no-cache
docker compose up -d
```

### 7.2 DB 回滚

#### 7.2.1 回滚分区迁移

```bash
docker compose exec -T db psql -U dreambig -d dreambig -c "
  -- 重建非分区版本
  CREATE TABLE ranking_snapshots_flat (LIKE ranking_snapshots INCLUDING ALL);
  INSERT INTO ranking_snapshots_flat SELECT * FROM ranking_snapshots;
  DROP TABLE ranking_snapshots CASCADE;
  ALTER TABLE ranking_snapshots_flat RENAME TO ranking_snapshots;
  -- 重建索引
  CREATE INDEX ranking_snapshots_date_idx ON ranking_snapshots (snapshot_date DESC);
  CREATE INDEX ranking_snapshots_listing_date_idx
    ON ranking_snapshots (platform_listing_id, snapshot_date DESC);
"
```

#### 7.2.2 回滚 workspace 迁移

```bash
docker compose exec -T db psql -U dreambig -d dreambig -c "
  ALTER TABLE alerts DROP COLUMN IF EXISTS workspace_id;
  ALTER TABLE subscriptions DROP COLUMN IF EXISTS workspace_id;
  ALTER TABLE experiments DROP COLUMN IF EXISTS workspace_id;
  ALTER TABLE audit_logs DROP COLUMN IF EXISTS workspace_id;
  ALTER TABLE users DROP COLUMN IF EXISTS last_workspace_id;
  -- game_tags 主键改回二元
  ALTER TABLE game_tags DROP CONSTRAINT game_tags_pkey;
  ALTER TABLE game_tags DROP COLUMN workspace_id;
  ALTER TABLE game_tags ADD PRIMARY KEY (game_id, tag);
  DROP TABLE workspace_members;
  DROP TABLE workspaces;
"
```

#### 7.2.3 完全回滚（最后手段）

```bash
docker compose down
docker compose up -d db
sleep 5
docker compose exec -T db psql -U dreambig -d dreambig \
  < backup_pre_phase4_finishing_<timestamp>.sql
docker compose up -d
```

---

## 8. 已知 caveats

### 8.1 ranking_snapshots unique 约束变弱

新分区表的 unique 约束包含 `id`：
```sql
UNIQUE (platform_listing_id, chart_type, region, snapshot_date, id)
```

而原约束是 `UNIQUE (platform_listing_id, chart_type, region, snapshot_date)`。

**含义**：DB 层不再硬阻止"同一个 listing + chart_type + region + 日期"出现重复行。你的 scrape upsert 逻辑（在 `dedup.py`）依然会用业务键去重，所以正常情况下不会出问题。

**何时会出问题**：如果你绕过 dedup engine 直接 raw insert 到 ranking_snapshots，可能会造重复行。**只在直接 SQL 操作时需要小心**。

### 8.2 Prisma model 与 DB PK 不完全一致

`web/prisma/schema.prisma` 里 `RankingSnapshot.id` 仍然是单字段 `@id`，但 DB 实际是复合 PK `(id, snapshot_date)`。这是分区表的硬性要求。

**含义**：Prisma 的 `findUnique({ id })` 仍然能用，因为 `id` 来自全表共享的 sequence，跨分区也唯一。但**永远不要跑 `prisma db pull`** —— 它会发现不一致并提议改 schema，会破坏 ORM 行为。

### 8.3 系统级 alerts 都属于 default workspace

`workers/src/processors/alerting.py` 创建的 `__system__*` alerts 全部写到 `workspace_id='default'`。其它 workspace 的用户**不会**看到这些规则触发的 alert events，因为 `/alerts` 页按 workspace 过滤。

**临时解决**：如果你创建了第二个 workspace 并希望它也接收系统告警，需要：
1. 把那个 workspace 的用户也加到 default workspace 当 viewer
2. 或者在 `/subscriptions` 里订阅相关游戏（subscriptions 是按 user 而不是 workspace 隔离投递的）

**长期解决（未做）**：把 system alert 改成"全局规则、按 workspace 派发"模式。这是 P5 的事。

### 8.4 第一次启动的 workspace 后台

如果你想让某个用户能看到非 default 的 workspace，**目前必须用 super_admin 角色**调 API 创建：

```bash
# 假设你的 super_admin 用户已存在
curl -X POST https://your-domain/api/workspaces \
  -H "Content-Type: application/json" \
  -H "Cookie: <super-admin-session>" \
  -d '{"name":"产品 A 工作区","slug":"product-a","description":"..."}'
```

创建后调 `/api/workspaces/<id>/members` 加成员。Sidebar 切换器随后会显示。

### 8.5 yt-dlp 受平台 rate-limit 影响

trailer 分析在 production 跑时，YouTube 偶尔会返回 429。yt-dlp 内置重试，但如果遇到大量失败，考虑：
- 在 `docker-compose.yml` scraper 服务的 environment 加 `HTTP_PROXY` 走代理
- 或调低 trailer 分析的 limit（在 scheduler 里默认 10/周，可以改小）

---

## 9. 部署完成后的 commit 建议

部署稳定 24 小时后，建议 commit ROADMAP 更新和这次部署文档：

```bash
git add ROADMAP.md DEPLOYMENT_PHASE4_FINISHING.md prd.txt
git add web/prisma workers/src workers/tests workers/Dockerfile workers/pyproject.toml
git add web/lib/workspace.ts web/components/workspace-switcher.tsx
git add web/app/api/workspaces web/app/admin/jobs web/app/admin/games web/app/admin/duplicates
git commit -m "feat: phase4-finishing - workspace, trailer, admin pages, partitioning, tests"
```

---

## 10. 速查命令

```bash
# 看所有 cron 任务
docker compose logs scheduler | grep "cron\["

# 看最近的 trailer 分析结果
docker compose exec -T db psql -U dreambig -d dreambig -c "
  SELECT game_id, asset_url, analyzed_at, model_used
  FROM game_asset_analysis
  WHERE asset_type = 'trailer'
  ORDER BY analyzed_at DESC LIMIT 10;
"

# 看每个 workspace 的业务实体计数
docker compose exec -T db psql -U dreambig -d dreambig -c "
  SELECT
    w.id, w.name,
    (SELECT COUNT(*) FROM alerts WHERE workspace_id = w.id) AS alerts,
    (SELECT COUNT(*) FROM subscriptions WHERE workspace_id = w.id) AS subs,
    (SELECT COUNT(*) FROM experiments WHERE workspace_id = w.id) AS exps,
    (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = w.id) AS members
  FROM workspaces w
  ORDER BY w.created_at;
"

# 重新跑 worker 测试
docker compose run --rm scraper python -m pytest tests/ -v

# 强制触发 trailer 分析（不等周三）
docker compose run --rm scraper python -m src.worker trailer_analysis 5
```

---

## 完成清单

部署结束后逐项打钩：

- [ ] DB 备份完成且文件 > 0 字节
- [ ] 代码已 `git pull` 到 latest main
- [ ] scraper / scheduler 已暂停
- [ ] workspace 迁移成功，验证 SQL 通过
- [ ] 分区迁移成功，分区数 ≥ 15，行数与迁移前一致
- [ ] web 镜像 `--no-cache` 重建完成
- [ ] scraper / scheduler 镜像 `--no-cache` 重建完成
- [ ] 三个服务全部 running healthy
- [ ] sidebar 显示 workspace 切换器
- [ ] `/admin/jobs` `/admin/games` `/admin/duplicates` 三个新页可访问
- [ ] scheduler 日志里看到 `trailer_analysis` cron 注册
- [ ] (可选) `python -m pytest tests/` 在容器里 59 passed
- [ ] 24 小时观察期：无异常重启、无 5xx 日志暴涨

完成。
