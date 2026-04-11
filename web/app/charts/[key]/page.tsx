import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";
import { formatNumber } from "@/lib/utils";

export const dynamic = "force-dynamic";

// ============================================================
// Chart metadata
// ============================================================
type ChartMeta = {
  title: string;
  subtitle: string;
  kind: "rank" | "iaa" | "social";
};

const CHART_META: Record<string, ChartMeta> = {
  hot: { title: "热门榜", subtitle: "Tencent YYB 热度排行", kind: "rank" },
  top_grossing: {
    title: "畅销榜",
    subtitle: "广告 / 付费收入领先",
    kind: "rank",
  },
  new: { title: "新游榜", subtitle: "新入榜的机会窗口", kind: "rank" },
  featured: {
    title: "精选榜",
    subtitle: "腾讯官方编辑精选",
    kind: "rank",
  },
  tag_puzzle: {
    title: "休闲益智榜",
    subtitle: "休闲益智类微信游戏",
    kind: "rank",
  },
  tag_rpg: {
    title: "角色扮演榜",
    subtitle: "角色扮演类微信游戏",
    kind: "rank",
  },
  tag_board: {
    title: "棋牌榜",
    subtitle: "棋牌类微信游戏",
    kind: "rank",
  },
  tag_strategy: {
    title: "策略榜",
    subtitle: "策略类微信游戏",
    kind: "rank",
  },
  tag_adventure: {
    title: "动作冒险榜",
    subtitle: "动作冒险类微信游戏",
    kind: "rank",
  },
  tag_singleplayer: {
    title: "单机榜",
    subtitle: "单机类微信游戏",
    kind: "rank",
  },
  iaa: {
    title: "IAA 候选 - 全部",
    subtitle: "所有微信小游戏按今日综合评分排序",
    kind: "iaa",
  },
  social: {
    title: "社交热度 - 全部",
    subtitle: "近 7 天 Bilibili 聚合播放量排序",
    kind: "social",
  },
};

const PAGE_SIZE = 50;

const GRADE_COLORS: Record<string, string> = {
  S: "bg-green-500 text-white",
  A: "bg-lime-500 text-white",
  B: "bg-yellow-500 text-white",
  C: "bg-orange-500 text-white",
  D: "bg-red-500 text-white",
};

// ============================================================
// Row shape
// ============================================================
type Row = {
  rank: number;
  game_id: number;
  name: string;
  developer: string | null;
  icon: string | null;
  genre: string | null;
  // rank-chart fields
  rank_change?: number | null;
  // iaa fields
  iaa_grade?: string | null;
  iaa_suitability?: number | null;
  overall_score?: number | null;
  // social fields
  total_views?: number | null;
  total_videos?: number | null;
};

// ============================================================
// Loaders
// ============================================================
function todayDate(): Date {
  return new Date(new Date().toISOString().split("T")[0]);
}

async function loadRankChart(
  chartType: string,
  page: number,
): Promise<{ rows: Row[]; total: number }> {
  try {
    const today = todayDate();
    const where = {
      chartType,
      snapshotDate: today,
      platformListing: { platform: "wechat_mini" },
    };
    const [rows, total] = await Promise.all([
      prisma.rankingSnapshot.findMany({
        where,
        orderBy: { rankPosition: "asc" },
        skip: (page - 1) * PAGE_SIZE,
        take: PAGE_SIZE,
        include: {
          platformListing: { include: { game: true } },
        },
      }),
      prisma.rankingSnapshot.count({ where }),
    ]);
    return {
      rows: rows.map((r) => ({
        rank: r.rankPosition,
        rank_change: r.rankChange,
        game_id: r.platformListing.gameId,
        name:
          r.platformListing.game.nameZh ||
          r.platformListing.game.nameEn ||
          r.platformListing.name,
        developer: r.platformListing.game.developer,
        icon: r.platformListing.game.thumbnailUrl,
        genre: r.platformListing.game.genre,
      })),
      total,
    };
  } catch {
    return { rows: [], total: 0 };
  }
}

async function loadIaaCandidates(
  page: number,
): Promise<{ rows: Row[]; total: number }> {
  try {
    const today = todayDate();
    const where = {
      scoredAt: today,
      game: {
        platformListings: { some: { platform: "wechat_mini" } },
      },
    };
    const [rows, total] = await Promise.all([
      prisma.potentialScore.findMany({
        where,
        orderBy: [{ overallScore: "desc" }],
        skip: (page - 1) * PAGE_SIZE,
        take: PAGE_SIZE,
        include: { game: true },
      }),
      prisma.potentialScore.count({ where }),
    ]);
    return {
      rows: rows.map((r, i) => ({
        rank: (page - 1) * PAGE_SIZE + i + 1,
        game_id: r.gameId,
        name: r.game.nameZh || r.game.nameEn || "Unknown",
        developer: r.game.developer,
        icon: r.game.thumbnailUrl,
        genre: r.game.genre,
        iaa_grade: r.game.iaaGrade,
        iaa_suitability: r.game.iaaSuitability,
        overall_score: r.overallScore,
      })),
      total,
    };
  } catch {
    return { rows: [], total: 0 };
  }
}

async function loadSocialBuzz(
  page: number,
): Promise<{ rows: Row[]; total: number }> {
  try {
    // Aggregate in memory — social_signals is small enough (< few thousand rows)
    const since = new Date();
    since.setDate(since.getDate() - 7);
    const all = await prisma.socialSignal.findMany({
      where: {
        signalDate: { gte: since },
        game: {
          platformListings: { some: { platform: "wechat_mini" } },
        },
      },
      include: { game: true },
    });
    const ZERO = BigInt(0);
    type Agg = {
      game_id: number;
      name: string;
      developer: string | null;
      icon: string | null;
      genre: string | null;
      total_views: bigint;
      total_videos: number;
    };
    const byGame = new Map<number, Agg>();
    for (const r of all) {
      let a = byGame.get(r.gameId);
      if (!a) {
        a = {
          game_id: r.gameId,
          name: r.game.nameZh || r.game.nameEn || "Unknown",
          developer: r.game.developer,
          icon: r.game.thumbnailUrl,
          genre: r.game.genre,
          total_views: ZERO,
          total_videos: 0,
        };
        byGame.set(r.gameId, a);
      }
      a.total_views += r.viewCount;
      a.total_videos += r.videoCount;
    }
    const sorted = Array.from(byGame.values()).sort((a, b) =>
      b.total_views > a.total_views ? 1 : -1,
    );
    const total = sorted.length;
    const slice = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    return {
      rows: slice.map((x, i) => ({
        rank: (page - 1) * PAGE_SIZE + i + 1,
        game_id: x.game_id,
        name: x.name,
        developer: x.developer,
        icon: x.icon,
        genre: x.genre,
        total_views: Number(x.total_views),
        total_videos: x.total_videos,
      })),
      total,
    };
  } catch {
    return { rows: [], total: 0 };
  }
}

// ============================================================
// Page
// ============================================================
export default async function ChartViewAllPage({
  params,
  searchParams,
}: {
  params: Promise<{ key: string }>;
  searchParams: Promise<{ page?: string }>;
}) {
  const { key } = await params;
  const { page: pageRaw } = await searchParams;

  const meta = CHART_META[key];
  if (!meta) return notFound();

  const page = Math.max(1, parseInt(pageRaw || "1", 10) || 1);

  let data: { rows: Row[]; total: number };
  if (meta.kind === "rank") {
    data = await loadRankChart(key, page);
  } else if (meta.kind === "iaa") {
    data = await loadIaaCandidates(page);
  } else {
    data = await loadSocialBuzz(page);
  }

  const { rows, total } = data;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasPrev = page > 1;
  const hasNext = page < totalPages;

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <Link
        href="/"
        className="text-sm text-gray-400 hover:text-gray-600 inline-block"
      >
        ← 返回微信爆款中心
      </Link>

      {/* Header */}
      <header>
        <h1 className="text-2xl font-bold">{meta.title}</h1>
        <p className="text-sm text-gray-500 mt-1">
          {meta.subtitle} · 共 {total.toLocaleString()} 款游戏 · 第 {page} /{" "}
          {totalPages} 页
        </p>
      </header>

      {/* Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        {rows.length === 0 ? (
          <p className="text-gray-400 text-center py-12">暂无数据</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr className="text-xs text-gray-500">
                <th className="text-left px-3 py-2 w-14">排名</th>
                <th className="text-left px-3 py-2">游戏</th>
                <th className="text-left px-3 py-2 hidden md:table-cell">
                  开发商
                </th>
                <th className="text-center px-3 py-2 hidden md:table-cell">
                  品类
                </th>
                {meta.kind === "rank" && (
                  <th className="text-right px-3 py-2">变化</th>
                )}
                {meta.kind === "iaa" && (
                  <>
                    <th className="text-center px-3 py-2">IAA</th>
                    <th className="text-right px-3 py-2">评分</th>
                  </>
                )}
                {meta.kind === "social" && (
                  <>
                    <th className="text-right px-3 py-2 hidden sm:table-cell">
                      视频
                    </th>
                    <th className="text-right px-3 py-2">播放量</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((r) => (
                <tr key={`${r.game_id}-${r.rank}`} className="hover:bg-gray-50">
                  <td className="px-3 py-2.5 font-bold text-gray-700">
                    {r.rank}
                  </td>
                  <td className="px-3 py-2.5">
                    <Link
                      href={`/games/${r.game_id}`}
                      className="flex items-center gap-2"
                    >
                      {r.icon && (
                        <img
                          src={r.icon}
                          alt={r.name}
                          className="w-8 h-8 rounded flex-shrink-0 object-cover"
                        />
                      )}
                      <span className="text-blue-600 hover:underline font-medium">
                        {r.name}
                      </span>
                    </Link>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-gray-500 hidden md:table-cell">
                    {r.developer || "-"}
                  </td>
                  <td className="text-center px-3 py-2.5 text-xs text-gray-500 hidden md:table-cell">
                    {r.genre || "-"}
                  </td>
                  {meta.kind === "rank" && (
                    <td className="text-right px-3 py-2.5 text-xs font-mono">
                      {r.rank_change != null && r.rank_change !== 0 ? (
                        <span
                          className={
                            r.rank_change > 0
                              ? "text-green-600"
                              : "text-red-500"
                          }
                        >
                          {r.rank_change > 0 ? "▲" : "▼"}
                          {Math.abs(r.rank_change)}
                        </span>
                      ) : (
                        <span className="text-gray-300">-</span>
                      )}
                    </td>
                  )}
                  {meta.kind === "iaa" && (
                    <>
                      <td className="text-center px-3 py-2.5">
                        {r.iaa_grade ? (
                          <span
                            className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                              GRADE_COLORS[r.iaa_grade] || "bg-gray-200"
                            }`}
                          >
                            {r.iaa_grade}
                          </span>
                        ) : (
                          <span className="text-gray-300 text-xs">-</span>
                        )}
                      </td>
                      <td className="text-right px-3 py-2.5">
                        <span
                          className={`font-bold ${
                            (r.overall_score ?? 0) >= 75
                              ? "text-green-600"
                              : "text-gray-700"
                          }`}
                        >
                          {r.overall_score}
                        </span>
                      </td>
                    </>
                  )}
                  {meta.kind === "social" && (
                    <>
                      <td className="text-right px-3 py-2.5 text-xs text-gray-400 hidden sm:table-cell">
                        {r.total_videos}
                      </td>
                      <td className="text-right px-3 py-2.5 text-purple-600 font-mono font-bold">
                        {formatNumber(r.total_views ?? 0)}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <nav className="flex items-center justify-center gap-2">
          <Link
            href={hasPrev ? `/charts/${key}?page=${page - 1}` : "#"}
            className={`px-3 py-1.5 text-sm border rounded ${
              hasPrev
                ? "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                : "bg-gray-50 text-gray-300 border-gray-200 cursor-not-allowed"
            }`}
            aria-disabled={!hasPrev}
          >
            ← 上一页
          </Link>
          <span className="text-sm text-gray-500 px-3">
            第 {page} / {totalPages} 页
          </span>
          <Link
            href={hasNext ? `/charts/${key}?page=${page + 1}` : "#"}
            className={`px-3 py-1.5 text-sm border rounded ${
              hasNext
                ? "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
                : "bg-gray-50 text-gray-300 border-gray-200 cursor-not-allowed"
            }`}
            aria-disabled={!hasNext}
          >
            下一页 →
          </Link>
        </nav>
      )}
    </div>
  );
}
