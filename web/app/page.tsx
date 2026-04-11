import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { getCurrentWorkspaceId } from "@/lib/workspace";
import { formatNumber } from "@/lib/utils";

export const dynamic = "force-dynamic";

// ============================================================
// Types + constants
// ============================================================
type RankRow = {
  rank_position: number;
  rank_change: number | null;
  game_id: number;
  name: string;
  name_zh: string | null;
  developer: string | null;
  icon: string | null;
};

// All 10 WeChat charts rendered on the dashboard — each as a full ChartCard
// (top 10 preview + "查看全部 →" link to /charts/<key> with pagination).
const ALL_CHARTS: Array<{ key: string; title: string; subtitle: string }> = [
  { key: "hot", title: "热门榜", subtitle: "Tencent YYB 热度排行 Top 400" },
  { key: "top_grossing", title: "畅销榜", subtitle: "广告 / 付费收入领先 Top 400" },
  { key: "new", title: "新游榜", subtitle: "新入榜，机会窗口 Top 400" },
  { key: "featured", title: "精选榜", subtitle: "腾讯官方编辑精选" },
  { key: "tag_puzzle", title: "休闲益智榜", subtitle: "休闲益智类微信游戏" },
  { key: "tag_rpg", title: "角色扮演榜", subtitle: "角色扮演类微信游戏" },
  { key: "tag_board", title: "棋牌榜", subtitle: "棋牌类微信游戏" },
  { key: "tag_strategy", title: "策略榜", subtitle: "策略类微信游戏" },
  { key: "tag_adventure", title: "动作冒险榜", subtitle: "动作冒险类微信游戏" },
  { key: "tag_singleplayer", title: "单机榜", subtitle: "单机类微信游戏" },
];

const GRADE_COLORS: Record<string, string> = {
  S: "bg-green-500 text-white",
  A: "bg-lime-500 text-white",
  B: "bg-yellow-500 text-white",
  C: "bg-orange-500 text-white",
  D: "bg-red-500 text-white",
};

function todayDate(): Date {
  return new Date(new Date().toISOString().split("T")[0]);
}

// ============================================================
// Queries
// ============================================================

async function getStatCards() {
  try {
    const today = todayDate();
    const [wechatGameCount, todayChartsCount, newEntriesToday, highPotential] =
      await Promise.all([
        // Total unique WeChat games tracked
        prisma.game.count({
          where: {
            platformListings: { some: { platform: "wechat_mini" } },
          },
        }),
        // Total rank rows today (indication of scrape health)
        prisma.rankingSnapshot.count({
          where: {
            snapshotDate: today,
            platformListing: { platform: "wechat_mini" },
          },
        }),
        // Today's entries on 新游榜
        prisma.rankingSnapshot.count({
          where: {
            snapshotDate: today,
            chartType: "new",
            platformListing: { platform: "wechat_mini" },
          },
        }),
        // High-potential WeChat games (overall_score >= 60 today)
        prisma.potentialScore.count({
          where: {
            scoredAt: today,
            overallScore: { gte: 60 },
            game: {
              platformListings: { some: { platform: "wechat_mini" } },
            },
          },
        }),
      ]);
    return {
      wechatGameCount,
      todayChartsCount,
      newEntriesToday,
      highPotential,
    };
  } catch {
    return {
      wechatGameCount: 0,
      todayChartsCount: 0,
      newEntriesToday: 0,
      highPotential: 0,
    };
  }
}

async function getChartTop10(chartKey: string): Promise<RankRow[]> {
  try {
    const today = todayDate();
    const rows = await prisma.rankingSnapshot.findMany({
      where: {
        chartType: chartKey,
        snapshotDate: today,
        platformListing: { platform: "wechat_mini" },
      },
      orderBy: { rankPosition: "asc" },
      take: 10,
      include: {
        platformListing: {
          include: { game: true },
        },
      },
    });
    return rows.map((r) => ({
      rank_position: r.rankPosition,
      rank_change: r.rankChange,
      game_id: r.platformListing.gameId,
      name: r.platformListing.name,
      name_zh: r.platformListing.game.nameZh,
      developer: r.platformListing.game.developer,
      icon: r.platformListing.game.thumbnailUrl,
    }));
  } catch {
    return [];
  }
}

async function getIAACandidates() {
  try {
    const today = todayDate();
    const rows = await prisma.potentialScore.findMany({
      where: {
        scoredAt: today,
        game: {
          platformListings: { some: { platform: "wechat_mini" } },
        },
      },
      orderBy: [{ overallScore: "desc" }],
      take: 15,
      include: { game: true },
    });
    return rows.map((r) => ({
      game_id: r.gameId,
      name_zh: r.game.nameZh,
      name_en: r.game.nameEn,
      developer: r.game.developer,
      iaa_grade: r.game.iaaGrade,
      iaa_suitability: r.game.iaaSuitability,
      overall_score: r.overallScore,
      genre: r.game.genre,
    }));
  } catch {
    return [];
  }
}

async function getLatestReviews() {
  try {
    // Order by helpfulCount (NULLs last) then most-recently scraped.
    // Explicit nulls positioning because Bilibili comments can have
    // like=0 (treated as 0 not null) but we still want the top-liked
    // content first regardless.
    const rows = await prisma.review.findMany({
      where: {
        platformListing: { platform: "wechat_mini" },
      },
      orderBy: [
        { helpfulCount: { sort: "desc", nulls: "last" } },
        { scrapedAt: "desc" },
      ],
      take: 20,
      include: {
        platformListing: { include: { game: true } },
      },
    });
    return rows.map((r) => ({
      id: r.id,
      game_id: r.platformListing.gameId,
      game_name:
        r.platformListing.game.nameZh || r.platformListing.game.nameEn,
      content: r.content,
      author: r.authorName,
      helpful: r.helpfulCount,
      posted_at: r.postedAt,
      sentiment: r.sentiment,
      topics: r.topics,
    }));
  } catch (e) {
    // Log loud so we stop silently showing "no reviews" when the query
    // itself is the thing that's broken.
    console.error("[dashboard] getLatestReviews failed:", e);
    return [];
  }
}

async function getSocialBuzz() {
  try {
    const since = new Date();
    since.setDate(since.getDate() - 7);
    const rows = await prisma.socialSignal.findMany({
      where: {
        signalDate: { gte: since },
        game: {
          platformListings: { some: { platform: "wechat_mini" } },
        },
      },
      include: { game: true },
    });
    // Aggregate per game
    const ZERO = BigInt(0);
    const byGame = new Map<
      number,
      {
        game_id: number;
        name_zh: string | null;
        name_en: string | null;
        total_views: bigint;
        total_videos: number;
      }
    >();
    for (const r of rows) {
      let agg = byGame.get(r.gameId);
      if (!agg) {
        agg = {
          game_id: r.gameId,
          name_zh: r.game.nameZh,
          name_en: r.game.nameEn,
          total_views: ZERO,
          total_videos: 0,
        };
        byGame.set(r.gameId, agg);
      }
      agg.total_views += r.viewCount;
      agg.total_videos += r.videoCount;
    }
    const arr = Array.from(byGame.values());
    arr.sort((a, b) => (b.total_views > a.total_views ? 1 : -1));
    return arr.slice(0, 10).map((x) => ({
      ...x,
      total_views: Number(x.total_views),
    }));
  } catch {
    return [];
  }
}

async function getGenreWeeklyReports() {
  try {
    const rows = await prisma.generatedReport.findMany({
      where: { reportType: "genre_weekly" },
      orderBy: { generatedAt: "desc" },
      take: 3,
    });
    return rows;
  } catch {
    return [];
  }
}

type WechatIntelReport = {
  id: number;
  generatedAt: Date;
  title: string | null;
  summary: string | null;
  payload: unknown;
};

async function getLatestWechatIntel(): Promise<WechatIntelReport | null> {
  try {
    const row = await prisma.generatedReport.findFirst({
      where: { reportType: "wechat_intelligence" },
      orderBy: { generatedAt: "desc" },
    });
    return row;
  } catch (e) {
    console.error("[dashboard] getLatestWechatIntel failed:", e);
    return null;
  }
}

type IntelPayload = {
  headline?: string;
  market_pulse?: string;
  market_snapshot?: string;
  overall_confidence?: number;
  top_signal_games?: Array<{
    game_id: number;
    name: string;
    signal_strength: string;
    iaa_angle: string;
    evidence_refs?: string[];
  }>;
  market_opportunities?: Array<{
    opportunity: string;
    reasoning: string;
    why_now: string;
    risk_factors?: string[];
    confidence: number;
  }>;
  red_flags?: Array<{
    pattern: string;
    affected_games?: number[];
    implication: string;
  }>;
  project_recommendations?: Array<{
    title: string;
    genre: string;
    core_mechanic: string;
    inspirations: number[];
    iaa_placement_hint: string;
    rationale: string;
    target_audience: string;
    estimated_dev_weeks: number;
    confidence: number;
  }>;
};

const PULSE_LABELS: Record<string, { label: string; color: string }> = {
  hot: { label: "🔥 过热", color: "bg-red-100 text-red-700" },
  warming: { label: "⬆️ 升温", color: "bg-orange-100 text-orange-700" },
  stable: { label: "⏸ 稳态", color: "bg-blue-100 text-blue-700" },
  cooling: { label: "⬇️ 降温", color: "bg-cyan-100 text-cyan-700" },
  cold: { label: "🧊 清冷", color: "bg-gray-100 text-gray-600" },
};

async function getRecentAlerts(workspaceId: string) {
  try {
    return await prisma.alertEvent.findMany({
      where: {
        alert: { workspaceId },
        game: {
          platformListings: { some: { platform: "wechat_mini" } },
        },
      },
      orderBy: { triggeredAt: "desc" },
      take: 8,
      include: { game: true, alert: true },
    });
  } catch {
    return [];
  }
}

// ============================================================
// UI sub-components
// ============================================================

function RankCell({ row }: { row: RankRow }) {
  const name = row.name_zh || row.name;
  return (
    <Link
      href={`/games/${row.game_id}`}
      className="flex items-center gap-3 py-2 px-3 hover:bg-gray-50 rounded"
    >
      <span
        className={`flex-shrink-0 w-6 text-center font-bold text-xs ${
          row.rank_position <= 3
            ? "text-orange-500"
            : row.rank_position <= 10
              ? "text-gray-700"
              : "text-gray-400"
        }`}
      >
        {row.rank_position}
      </span>
      {row.icon && (
        <img
          src={row.icon}
          alt={name}
          className="w-8 h-8 rounded flex-shrink-0 object-cover"
        />
      )}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{name}</p>
        {row.developer && (
          <p className="text-xs text-gray-400 truncate">{row.developer}</p>
        )}
      </div>
      {row.rank_change != null && row.rank_change !== 0 && (
        <span
          className={`text-xs font-mono flex-shrink-0 ${
            row.rank_change > 0 ? "text-green-600" : "text-red-500"
          }`}
        >
          {row.rank_change > 0 ? "▲" : "▼"}
          {Math.abs(row.rank_change)}
        </span>
      )}
    </Link>
  );
}

function ChartCard({
  title,
  subtitle,
  rows,
  viewAllHref,
}: {
  title: string;
  subtitle: string;
  rows: RankRow[];
  viewAllHref: string;
}) {
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <div className="flex items-end justify-between mb-3">
        <div>
          <h3 className="font-semibold text-base">{title}</h3>
          <p className="text-xs text-gray-400">{subtitle}</p>
        </div>
        <Link
          href={viewAllHref}
          className="text-xs text-blue-500 hover:underline whitespace-nowrap"
        >
          查看全部 →
        </Link>
      </div>
      {rows.length === 0 ? (
        <p className="text-gray-400 text-sm py-4 text-center">暂无数据</p>
      ) : (
        <div className="space-y-0.5">
          {rows.map((r) => (
            <RankCell key={`${r.game_id}-${r.rank_position}`} row={r} />
          ))}
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-3xl font-bold mt-1">{value.toLocaleString()}</p>
    </div>
  );
}

function WechatIntelSection({
  reportId,
  generatedAt,
  payload,
}: {
  reportId: number | null;
  generatedAt: Date | null;
  payload: IntelPayload | null;
}) {
  if (!payload) {
    return (
      <section className="bg-gradient-to-br from-indigo-50 to-purple-50 rounded-lg shadow p-6 border border-indigo-100">
        <div className="flex items-start gap-3">
          <span className="text-2xl">🧠</span>
          <div className="flex-1">
            <h2 className="text-lg font-semibold text-indigo-900">
              智库洞察
            </h2>
            <p className="text-sm text-indigo-700 mt-1">
              全球顶尖微信小游戏 IAA 智库分析 · 每日 13:00 HKT 自动生成
            </p>
            <p className="text-xs text-gray-500 mt-3">
              暂无今日简报 — 手动触发{" "}
              <code className="bg-white px-1.5 py-0.5 rounded border">
                python -m src.worker wechat_intelligence
              </code>
            </p>
          </div>
        </div>
      </section>
    );
  }

  const pulse = PULSE_LABELS[payload.market_pulse || "stable"] || PULSE_LABELS.stable;
  const confidencePct = Math.round((payload.overall_confidence ?? 0) * 100);

  return (
    <section className="bg-gradient-to-br from-indigo-50 via-white to-purple-50 rounded-lg shadow-lg p-6 border border-indigo-100 space-y-5">
      {/* Header */}
      <div className="flex items-start gap-3">
        <span className="text-3xl">🧠</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-xl font-bold text-indigo-900">智库洞察</h2>
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${pulse.color}`}
            >
              {pulse.label}
            </span>
            <span className="text-xs text-gray-400">
              置信度 {confidencePct}%
            </span>
            {generatedAt && (
              <span className="text-xs text-gray-400">
                · {generatedAt.toLocaleString("zh-CN", { hour: "2-digit", minute: "2-digit", month: "2-digit", day: "2-digit" })}
              </span>
            )}
          </div>
          {payload.headline && (
            <p className="text-base font-semibold text-gray-800 mt-1.5">
              {payload.headline}
            </p>
          )}
        </div>
        {reportId && (
          <Link
            href={`/reports/${reportId}`}
            className="text-xs text-indigo-600 hover:text-indigo-800 hover:underline whitespace-nowrap"
          >
            完整报告 →
          </Link>
        )}
      </div>

      {/* Market snapshot */}
      {payload.market_snapshot && (
        <div className="bg-white rounded-lg p-4 border-l-4 border-indigo-400">
          <p className="text-xs font-semibold text-indigo-700 uppercase tracking-wide mb-1.5">
            当日市场快照
          </p>
          <p className="text-sm text-gray-700 leading-relaxed">
            {payload.market_snapshot}
          </p>
        </div>
      )}

      {/* Project recommendations — the decision-grade payload */}
      {payload.project_recommendations && payload.project_recommendations.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-indigo-700 uppercase tracking-wide mb-2">
            立项建议 · 智库结论
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {payload.project_recommendations.map((rec, i) => (
              <div
                key={i}
                className="bg-white rounded-lg p-4 shadow-sm border border-indigo-100"
              >
                <div className="flex items-start justify-between mb-2">
                  <h4 className="font-semibold text-sm text-gray-800">
                    {rec.title}
                  </h4>
                  <span className="text-xs text-indigo-600 font-mono whitespace-nowrap">
                    {Math.round((rec.confidence ?? 0) * 100)}%
                  </span>
                </div>
                <div className="space-y-1.5 text-xs text-gray-600">
                  <p>
                    <span className="text-gray-400">品类:</span> {rec.genre}
                  </p>
                  <p>
                    <span className="text-gray-400">机制:</span> {rec.core_mechanic}
                  </p>
                  <p>
                    <span className="text-gray-400">变现:</span>{" "}
                    {rec.iaa_placement_hint}
                  </p>
                  <p>
                    <span className="text-gray-400">目标玩家:</span>{" "}
                    {rec.target_audience}
                  </p>
                  <p>
                    <span className="text-gray-400">估时:</span>{" "}
                    {rec.estimated_dev_weeks} 周
                  </p>
                </div>
                <p className="text-xs text-gray-700 mt-2 pt-2 border-t border-gray-100 leading-relaxed">
                  {rec.rationale}
                </p>
                {rec.inspirations && rec.inspirations.length > 0 && (
                  <div className="mt-2 flex items-center gap-1 flex-wrap">
                    <span className="text-xs text-gray-400">标的:</span>
                    {rec.inspirations.slice(0, 4).map((gid) => (
                      <Link
                        key={gid}
                        href={`/games/${gid}`}
                        className="text-xs text-indigo-600 hover:underline"
                      >
                        #{gid}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Opportunities + red flags side-by-side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {payload.market_opportunities && payload.market_opportunities.length > 0 && (
          <div className="bg-white rounded-lg p-4 border-l-4 border-green-400">
            <p className="text-xs font-semibold text-green-700 uppercase tracking-wide mb-2">
              市场机会 ({payload.market_opportunities.length})
            </p>
            <ul className="space-y-2.5">
              {payload.market_opportunities.map((o, i) => (
                <li key={i} className="text-sm">
                  <p className="font-medium text-gray-800">• {o.opportunity}</p>
                  <p className="text-xs text-gray-600 mt-0.5 pl-3">
                    {o.reasoning}
                  </p>
                  <p className="text-xs text-green-700 mt-0.5 pl-3 italic">
                    何时:{o.why_now}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}

        {payload.red_flags && payload.red_flags.length > 0 && (
          <div className="bg-white rounded-lg p-4 border-l-4 border-red-400">
            <p className="text-xs font-semibold text-red-700 uppercase tracking-wide mb-2">
              红灯预警 ({payload.red_flags.length})
            </p>
            <ul className="space-y-2.5">
              {payload.red_flags.map((f, i) => (
                <li key={i} className="text-sm">
                  <p className="font-medium text-gray-800">⚠ {f.pattern}</p>
                  <p className="text-xs text-gray-600 mt-0.5 pl-3">
                    {f.implication}
                  </p>
                  {f.affected_games && f.affected_games.length > 0 && (
                    <p className="text-xs text-gray-400 mt-0.5 pl-3">
                      涉及游戏:{" "}
                      {f.affected_games.slice(0, 5).map((gid, idx) => (
                        <span key={gid}>
                          <Link
                            href={`/games/${gid}`}
                            className="text-red-600 hover:underline"
                          >
                            #{gid}
                          </Link>
                          {idx < Math.min(4, f.affected_games!.length - 1)
                            ? ", "
                            : ""}
                        </span>
                      ))}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Top signal games */}
      {payload.top_signal_games && payload.top_signal_games.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-indigo-700 uppercase tracking-wide mb-2">
            信号最强游戏 ({payload.top_signal_games.length})
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {payload.top_signal_games.slice(0, 6).map((g) => (
              <Link
                key={g.game_id}
                href={`/games/${g.game_id}`}
                className="bg-white rounded p-3 text-xs hover:shadow-md transition-shadow border border-gray-100"
              >
                <p className="font-semibold text-gray-800 text-sm mb-1">
                  {g.name}
                </p>
                <p className="text-gray-600">{g.signal_strength}</p>
                <p className="text-indigo-600 mt-1 italic">{g.iaa_angle}</p>
              </Link>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ============================================================
// Page
// ============================================================

export default async function WechatDashboardPage() {
  const workspaceId = await getCurrentWorkspaceId();
  const [
    stats,
    hotChart,
    grossingChart,
    newChart,
    featuredChart,
    tagPuzzle,
    tagRpg,
    tagBoard,
    tagStrategy,
    tagAdventure,
    tagSingleplayer,
    iaaCandidates,
    latestReviews,
    socialBuzz,
    weeklyReports,
    recentAlerts,
    latestIntel,
  ] = await Promise.all([
    getStatCards(),
    getChartTop10("hot"),
    getChartTop10("top_grossing"),
    getChartTop10("new"),
    getChartTop10("featured"),
    getChartTop10("tag_puzzle"),
    getChartTop10("tag_rpg"),
    getChartTop10("tag_board"),
    getChartTop10("tag_strategy"),
    getChartTop10("tag_adventure"),
    getChartTop10("tag_singleplayer"),
    getIAACandidates(),
    getLatestReviews(),
    getSocialBuzz(),
    getGenreWeeklyReports(),
    getRecentAlerts(workspaceId),
    getLatestWechatIntel(),
  ]);

  // Parallel array aligned with ALL_CHARTS order
  const chartData: Array<RankRow[]> = [
    hotChart,
    grossingChart,
    newChart,
    featuredChart,
    tagPuzzle,
    tagRpg,
    tagBoard,
    tagStrategy,
    tagAdventure,
    tagSingleplayer,
  ];

  const intelPayload: IntelPayload | null = (() => {
    if (!latestIntel) return null;
    try {
      const raw = latestIntel.payload as unknown;
      return typeof raw === "string"
        ? (JSON.parse(raw) as IntelPayload)
        : (raw as IntelPayload);
    } catch {
      return null;
    }
  })();

  return (
    <div className="space-y-8">
      {/* Header */}
      <header>
        <h1 className="text-2xl font-bold">微信爆款中心</h1>
        <p className="text-sm text-gray-500 mt-1">
          围绕打造微信小游戏 IAA 爆款的核心信号 · 数据每日 06:40 - 06:49 HKT 更新
        </p>
      </header>

      {/* Stat cards */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="已追踪微信游戏"
          value={stats.wechatGameCount}
        />
        <StatCard
          label="今日榜单条目"
          value={stats.todayChartsCount}
        />
        <StatCard label="新游榜条目" value={stats.newEntriesToday} />
        <StatCard label="高潜力 (≥60)" value={stats.highPotential} />
      </section>

      {/* Section: 智库洞察 (Think-tank briefing — Opus-generated, daily) */}
      <WechatIntelSection
        reportId={latestIntel?.id ?? null}
        generatedAt={latestIntel?.generatedAt ?? null}
        payload={intelPayload}
      />

      {/* Section 1: all 10 ranking charts (top 10 preview each, click 查看全部
          to see full paginated list up to 400 rows at /charts/<key>) */}
      <section>
        <h2 className="text-lg font-semibold mb-3">榜单动态（全部 10 榜）</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {ALL_CHARTS.map((meta, i) => (
            <ChartCard
              key={meta.key}
              title={meta.title}
              subtitle={meta.subtitle}
              rows={chartData[i]}
              viewAllHref={`/charts/${meta.key}`}
            />
          ))}
        </div>
      </section>

      {/* Section 3: IAA candidates + Social buzz */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* IAA Candidates */}
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex items-end justify-between mb-3">
            <div>
              <h2 className="font-semibold text-base">IAA 候选 Top 15</h2>
              <p className="text-xs text-gray-400">
                综合评分与 IAA 适配度排序
              </p>
            </div>
            <Link
              href="/charts/iaa"
              className="text-xs text-blue-500 hover:underline"
            >
              查看全部 →
            </Link>
          </div>
          {iaaCandidates.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">
              等待评分数据生成
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-gray-500 border-b">
                  <tr>
                    <th className="text-left px-2 py-1.5">游戏</th>
                    <th className="text-center px-2 py-1.5">IAA</th>
                    <th className="text-center px-2 py-1.5">品类</th>
                    <th className="text-right px-2 py-1.5">评分</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {iaaCandidates.map((g) => (
                    <tr key={g.game_id} className="hover:bg-gray-50">
                      <td className="px-2 py-2">
                        <Link
                          href={`/games/${g.game_id}`}
                          className="text-blue-600 hover:underline text-sm font-medium"
                        >
                          {g.name_zh || g.name_en}
                        </Link>
                      </td>
                      <td className="text-center px-2 py-2">
                        {g.iaa_grade ? (
                          <span
                            className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                              GRADE_COLORS[g.iaa_grade] || "bg-gray-200"
                            }`}
                          >
                            {g.iaa_grade}
                          </span>
                        ) : (
                          <span className="text-gray-300 text-xs">-</span>
                        )}
                      </td>
                      <td className="text-center px-2 py-2 text-xs text-gray-500">
                        {g.genre || "-"}
                      </td>
                      <td className="text-right px-2 py-2">
                        <span
                          className={`text-xs font-bold ${
                            g.overall_score >= 75
                              ? "text-green-600"
                              : "text-gray-600"
                          }`}
                        >
                          {g.overall_score}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Social Buzz */}
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex items-end justify-between mb-3">
            <div>
              <h2 className="font-semibold text-base">社交热度 Top 10</h2>
              <p className="text-xs text-gray-400">
                近 7 天 B 站 / 抖音播放量聚合
              </p>
            </div>
            <Link
              href="/charts/social"
              className="text-xs text-blue-500 hover:underline whitespace-nowrap"
            >
              查看全部 →
            </Link>
          </div>
          {socialBuzz.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">
              暂无社媒数据
            </p>
          ) : (
            <div className="space-y-1">
              {socialBuzz.map((s) => (
                <Link
                  key={s.game_id}
                  href={`/games/${s.game_id}`}
                  className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-gray-50"
                >
                  <span className="text-sm truncate flex-1">
                    {s.name_zh || s.name_en}
                  </span>
                  <div className="flex items-center gap-3 flex-shrink-0 text-xs">
                    <span className="text-gray-400">
                      {s.total_videos} 视频
                    </span>
                    <span className="text-purple-600 font-mono font-bold">
                      {formatNumber(s.total_views)}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Section 4: Player voices (Bilibili reviews) */}
      <section>
        <div className="flex items-end justify-between mb-3">
          <div>
            <h2 className="text-lg font-semibold">玩家声音</h2>
            <p className="text-xs text-gray-400">
              B 站高赞评论 · 代理微信玩家意见（WeChat 评价系统闭环）
            </p>
          </div>
        </div>
        {latestReviews.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-8 text-center">
            <p className="text-gray-400 text-sm">
              暂无评论数据 — 待 Bilibili 抓取
            </p>
            <p className="text-gray-300 text-xs mt-2">
              每日 09:00 HKT 自动抓取；或手动触发{" "}
              <code className="bg-gray-100 px-1 rounded">
                python -m src.worker scrape_reviews
              </code>
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {latestReviews.map((r) => (
              <div
                key={r.id}
                className="bg-white rounded-lg shadow p-3 border-l-2 border-blue-300"
              >
                <div className="flex items-start justify-between mb-1.5">
                  <Link
                    href={`/games/${r.game_id}`}
                    className="text-xs font-medium text-blue-600 hover:underline truncate mr-2"
                  >
                    {r.game_name}
                  </Link>
                  <span className="text-xs text-gray-400 flex-shrink-0">
                    {r.helpful ?? 0} 👍
                  </span>
                </div>
                <p className="text-sm text-gray-700 leading-relaxed">
                  {r.content.slice(0, 140)}
                  {r.content.length > 140 ? "…" : ""}
                </p>
                <div className="flex items-center justify-between mt-2 text-xs text-gray-400">
                  <span>{r.author || "匿名"}</span>
                  {r.sentiment && (
                    <span
                      className={`px-1.5 py-0.5 rounded ${
                        r.sentiment === "positive"
                          ? "bg-green-100 text-green-700"
                          : r.sentiment === "negative"
                            ? "bg-red-100 text-red-700"
                            : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {r.sentiment === "positive"
                        ? "正面"
                        : r.sentiment === "negative"
                          ? "负面"
                          : "中性"}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Section 5: Weekly reports + alerts */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Genre weekly reports */}
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex items-end justify-between mb-3">
            <h2 className="font-semibold text-base">赛道周报</h2>
            <Link
              href="/reports"
              className="text-xs text-blue-500 hover:underline"
            >
              全部 →
            </Link>
          </div>
          {weeklyReports.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">
              暂无周报（每周一 09:00 HKT 自动生成）
            </p>
          ) : (
            <div className="space-y-3">
              {weeklyReports.map((r) => (
                <Link
                  key={r.id}
                  href={`/reports/${r.id}`}
                  className="block p-3 rounded border border-gray-100 hover:bg-gray-50"
                >
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium">{r.title}</p>
                    <span className="text-xs text-gray-400">
                      {r.generatedAt.toLocaleDateString("zh-CN")}
                    </span>
                  </div>
                  {r.summary && (
                    <p className="text-xs text-gray-500 mt-1 line-clamp-2">
                      {r.summary}
                    </p>
                  )}
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Recent alerts */}
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex items-end justify-between mb-3">
            <h2 className="font-semibold text-base">微信相关告警</h2>
            <Link
              href="/alerts"
              className="text-xs text-blue-500 hover:underline"
            >
              全部 →
            </Link>
          </div>
          {recentAlerts.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">
              暂无告警事件
            </p>
          ) : (
            <div className="space-y-2">
              {recentAlerts.map((a) => (
                <Link
                  key={a.id}
                  href={`/games/${a.gameId}`}
                  className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-gray-50"
                >
                  <div className="min-w-0 flex-1">
                    <span className="text-sm font-medium">
                      {a.game.nameZh || a.game.nameEn}
                    </span>
                    <p className="text-xs text-gray-400 truncate">
                      {a.alert.name}
                    </p>
                  </div>
                  <span className="text-xs text-gray-500 flex-shrink-0">
                    {a.triggeredAt.toLocaleDateString("zh-CN")}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Footer hint */}
      <footer className="text-center text-xs text-gray-400 py-4">
        想看全球多平台视图？前往{" "}
        <Link href="/global" className="text-blue-500 hover:underline">
          /global 全局 Dashboard
        </Link>
      </footer>
    </div>
  );
}
