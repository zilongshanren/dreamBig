import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { PLATFORM_LABELS, formatNumber } from "@/lib/utils";
import { getCurrentWorkspaceId } from "@/lib/workspace";

export const dynamic = "force-dynamic";

// Platforms (mirrors shared/platforms.json keys)
const PLATFORM_KEYS: string[] = [
  "google_play",
  "app_store",
  "taptap",
  "steam",
  "wechat_mini",
  "poki",
  "crazygames",
  "4399",
];

// Genres (mirrors shared/genres.json)
const GENRE_ENTRIES: Array<[string, { label_zh: string }]> = [
  ["idle", { label_zh: "放置" }],
  ["merge", { label_zh: "合成" }],
  ["match3", { label_zh: "三消" }],
  ["puzzle", { label_zh: "益智" }],
  ["casual_action", { label_zh: "休闲动作" }],
  ["runner", { label_zh: "跑酷" }],
  ["tower_defense", { label_zh: "塔防" }],
  ["simulation", { label_zh: "模拟" }],
  ["word", { label_zh: "文字" }],
  ["trivia", { label_zh: "问答" }],
  ["arcade", { label_zh: "街机" }],
  ["board", { label_zh: "棋牌" }],
  ["card", { label_zh: "卡牌" }],
  ["sports", { label_zh: "体育" }],
  ["racing", { label_zh: "竞速" }],
  ["strategy", { label_zh: "策略" }],
  ["rpg", { label_zh: "角色扮演" }],
  ["adventure", { label_zh: "冒险" }],
  ["shooter", { label_zh: "射击" }],
  ["moba", { label_zh: "MOBA" }],
];

type Filters = {
  platform: string; // "all" or platform key
  window: "24h" | "7d" | "30d";
  genre: string; // "all" or genre key
};

function parseFilters(params: {
  platform?: string;
  window?: string;
  genre?: string;
}): Filters {
  const platform =
    params.platform && PLATFORM_KEYS.includes(params.platform)
      ? params.platform
      : "all";
  const window =
    params.window === "7d" || params.window === "30d" ? params.window : "24h";
  const genre = params.genre && params.genre.length > 0 ? params.genre : "all";
  return { platform, window, genre };
}

function windowToDays(w: Filters["window"]): number {
  if (w === "7d") return 7;
  if (w === "30d") return 30;
  return 1;
}

function todayDate(): Date {
  return new Date(new Date().toISOString().split("T")[0]);
}

function daysAgo(n: number): Date {
  const d = todayDate();
  d.setDate(d.getDate() - n);
  return d;
}

/** Return the start-of-window date given the current filter (exclusive of today is OK). */
function windowStartDate(w: Filters["window"]): Date {
  return daysAgo(windowToDays(w));
}

async function getStats(workspaceId: string) {
  try {
    const [gameCount, platformCount, alertCount, topScores] =
      await Promise.all([
        prisma.game.count(),
        prisma.platformListing.count(),
        prisma.alertEvent.count({
          where: {
            triggeredAt: { gte: new Date(Date.now() - 24 * 60 * 60 * 1000) },
            alert: { workspaceId },
          },
        }),
        prisma.potentialScore.findMany({
          where: { scoredAt: todayDate() },
          orderBy: { overallScore: "desc" },
          take: 10,
          include: { game: true },
        }),
      ]);
    return { gameCount, platformCount, alertCount, topScores };
  } catch {
    return { gameCount: 0, platformCount: 0, alertCount: 0, topScores: [] };
  }
}

// 1. 榜单异动 Top 10 — largest rank improvement within the selected window
async function getRankingMovement(filters: Filters) {
  try {
    const windowStart = windowStartDate(filters.window);
    const rows = await prisma.rankingSnapshot.findMany({
      where: {
        snapshotDate: { gte: windowStart },
        rankChange: { gt: 0 },
        ...(filters.platform !== "all"
          ? { platformListing: { platform: filters.platform } }
          : {}),
      },
      orderBy: { rankChange: "desc" },
      take: 50, // take more so genre filter has room
      include: {
        platformListing: {
          include: { game: true },
        },
      },
    });
    // Deduplicate per game (keep the biggest rankChange in window)
    const bestByGame = new Map<number, (typeof rows)[number]>();
    for (const r of rows) {
      const prev = bestByGame.get(r.platformListing.gameId);
      if (!prev || (r.rankChange ?? 0) > (prev.rankChange ?? 0)) {
        bestByGame.set(r.platformListing.gameId, r);
      }
    }
    let deduped = Array.from(bestByGame.values());

    if (filters.genre !== "all") {
      deduped = deduped.filter(
        (r) => r.platformListing.game.genre === filters.genre,
      );
    }
    deduped.sort((a, b) => (b.rankChange ?? 0) - (a.rankChange ?? 0));
    return deduped.slice(0, 10);
  } catch {
    return [];
  }
}

// 2. 口碑恶化榜 Top 10 — largest ratingQuality drop within the selected window
async function getReputationDecline(filters: Filters) {
  try {
    const today = todayDate();
    const past = windowStartDate(filters.window);

    // Build platform filter (applied via game's platform_listings join)
    const platformGameFilter =
      filters.platform !== "all"
        ? {
            game: {
              platformListings: { some: { platform: filters.platform } },
              ...(filters.genre !== "all" ? { genre: filters.genre } : {}),
            },
          }
        : filters.genre !== "all"
          ? { game: { genre: filters.genre } }
          : {};

    const [todays, pasts] = await Promise.all([
      prisma.potentialScore.findMany({
        where: { scoredAt: today, ...platformGameFilter },
        select: { gameId: true, ratingQuality: true, game: true },
      }),
      prisma.potentialScore.findMany({
        where: { scoredAt: past },
        select: { gameId: true, ratingQuality: true },
      }),
    ]);

    if (todays.length === 0 || pasts.length === 0) return [];

    const pastMap = new Map(pasts.map((p) => [p.gameId, p.ratingQuality]));
    type Delta = {
      gameId: number;
      game: (typeof todays)[number]["game"];
      drop: number;
      current: number;
    };
    const deltas: Delta[] = [];
    for (const t of todays) {
      const prior = pastMap.get(t.gameId);
      if (prior == null) continue;
      const drop = prior - t.ratingQuality;
      if (drop <= 0) continue;
      deltas.push({
        gameId: t.gameId,
        game: t.game,
        drop,
        current: t.ratingQuality,
      });
    }

    deltas.sort((a, b) => b.drop - a.drop);
    return deltas.slice(0, 10);
  } catch {
    return [];
  }
}

// 3. 社交爆发榜 Top 10 — highest view_count within the selected window
async function getSocialBurst(filters: Filters) {
  try {
    const since = windowStartDate(filters.window);
    // Platform filter applies to game's listings (not to social platform)
    const gameWhere =
      filters.platform !== "all"
        ? { platformListings: { some: { platform: filters.platform } } }
        : {};
    const rows = await prisma.socialSignal.findMany({
      where: {
        signalDate: { gte: since },
        ...(filters.platform !== "all" ? { game: gameWhere } : {}),
      },
      include: { game: true },
    });
    if (rows.length === 0) return [];

    const ZERO = BigInt(0);
    type Agg = {
      gameId: number;
      game: (typeof rows)[number]["game"];
      totalViews: bigint;
      platforms: Map<string, bigint>;
    };
    const byGame = new Map<number, Agg>();
    for (const r of rows) {
      if (filters.genre !== "all" && r.game.genre !== filters.genre) continue;
      let a = byGame.get(r.gameId);
      if (!a) {
        a = {
          gameId: r.gameId,
          game: r.game,
          totalViews: ZERO,
          platforms: new Map(),
        };
        byGame.set(r.gameId, a);
      }
      a.totalViews += r.viewCount;
      a.platforms.set(
        r.platform,
        (a.platforms.get(r.platform) ?? ZERO) + r.viewCount,
      );
    }
    const arr = Array.from(byGame.values());
    arr.sort((a, b) => (b.totalViews > a.totalViews ? 1 : -1));
    return arr.slice(0, 10).map((a) => {
      let topPlatform = "";
      let maxViews = ZERO;
      for (const [p, v] of a.platforms) {
        if (v > maxViews) {
          maxViews = v;
          topPlatform = p;
        }
      }
      return {
        gameId: a.gameId,
        game: a.game,
        totalViews: Number(a.totalViews),
        topPlatform,
      };
    });
  } catch {
    return [];
  }
}

// 4. IAA 适配榜 Top 10 — high IAA + high overall score within the selected window
async function getIAACandidates(filters: Filters) {
  try {
    const since = windowStartDate(filters.window);
    const rows = await prisma.potentialScore.findMany({
      where: {
        scoredAt: { gte: since },
        overallScore: { gte: 60 },
        game: {
          iaaSuitability: { gte: 70 },
          ...(filters.genre !== "all" ? { genre: filters.genre } : {}),
          ...(filters.platform !== "all"
            ? { platformListings: { some: { platform: filters.platform } } }
            : {}),
        },
      },
      orderBy: [{ scoredAt: "desc" }, { overallScore: "desc" }],
      take: 50,
      include: { game: true },
    });
    // Dedup per game, keep most recent scored row
    const bestByGame = new Map<number, (typeof rows)[number]>();
    for (const r of rows) {
      if (!bestByGame.has(r.gameId)) bestByGame.set(r.gameId, r);
    }
    return Array.from(bestByGame.values()).slice(0, 10);
  } catch {
    return [];
  }
}

async function getRecentAlerts(workspaceId: string) {
  try {
    return await prisma.alertEvent.findMany({
      where: { alert: { workspaceId } },
      orderBy: { triggeredAt: "desc" },
      take: 5,
      include: { game: true, alert: true },
    });
  } catch {
    return [];
  }
}

async function getScraperStatus() {
  try {
    return await prisma.scrapeJob.findMany({
      orderBy: { startedAt: "desc" },
      take: 10,
      distinct: ["platform"],
    });
  } catch {
    return [];
  }
}

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{
    platform?: string;
    window?: string;
    genre?: string;
  }>;
}) {
  const params = await searchParams;
  const filters = parseFilters(params);
  const workspaceId = await getCurrentWorkspaceId();

  const [
    stats,
    rankingMovement,
    reputationDecline,
    socialBurst,
    iaaCandidates,
    recentAlerts,
    scraperStatus,
  ] = await Promise.all([
    getStats(workspaceId),
    getRankingMovement(filters),
    getReputationDecline(filters),
    getSocialBurst(filters),
    getIAACandidates(filters),
    getRecentAlerts(workspaceId),
    getScraperStatus(),
  ]);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Dashboard</h2>

      {/* Filter Bar */}
      <form
        method="get"
        className="bg-white rounded-lg shadow p-4 mb-6 flex flex-wrap gap-3 items-end"
      >
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">平台</label>
          <select
            name="platform"
            defaultValue={filters.platform}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="all">全部</option>
            {PLATFORM_KEYS.map((k) => (
              <option key={k} value={k}>
                {PLATFORM_LABELS[k] ?? k}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">时间窗口</label>
          <select
            name="window"
            defaultValue={filters.window}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="24h">24 小时</option>
            <option value="7d">7 天</option>
            <option value="30d">30 天</option>
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">品类</label>
          <select
            name="genre"
            defaultValue={filters.genre}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="all">全部</option>
            {GENRE_ENTRIES.map(([key, meta]) => (
              <option key={key} value={key}>
                {meta.label_zh}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          className="text-sm bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-1.5"
        >
          应用
        </button>
        {(filters.platform !== "all" ||
          filters.window !== "24h" ||
          filters.genre !== "all") && (
          <Link
            href="/"
            className="text-sm text-gray-500 hover:text-gray-700 underline py-1.5"
          >
            重置
          </Link>
        )}
        <span className="text-xs text-gray-400 ml-auto self-center">
          窗口: {filters.window === "24h" ? "24 小时" : filters.window}
          {" · "}
          {windowToDays(filters.window)} 天
        </span>
      </form>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard label="总游戏数" value={stats.gameCount} />
        <StatCard label="平台记录" value={stats.platformCount} />
        <StatCard label="今日告警" value={stats.alertCount} />
        <StatCard
          label="高潜力 (75+)"
          value={
            stats.topScores.filter((s) => s.overallScore >= 75).length
          }
        />
      </div>

      {/* 4 Top-10 lists: 2x2 grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        {/* 1. 榜单异动 Top 10 */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">榜单异动 Top 10</h3>
          {rankingMovement.length === 0 ? (
            <p className="text-gray-400 text-sm">
              暂无数据，等待首次爬取
            </p>
          ) : (
            <div className="space-y-1">
              {rankingMovement.map((r) => (
                <Link
                  key={r.id}
                  href={`/games/${r.platformListing.gameId}`}
                  className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-50"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-medium truncate">
                      {r.platformListing.game.nameZh ||
                        r.platformListing.game.nameEn ||
                        r.platformListing.name}
                    </span>
                    <span className="text-xs text-gray-400 flex-shrink-0">
                      {PLATFORM_LABELS[r.platformListing.platform] ??
                        r.platformListing.platform}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className="text-xs text-green-600 font-mono font-bold">
                      +{r.rankChange}
                    </span>
                    <span className="text-xs text-gray-500 font-mono">
                      #{r.rankPosition}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* 2. 口碑恶化榜 Top 10 */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">口碑恶化榜 Top 10</h3>
          {reputationDecline.length === 0 ? (
            <p className="text-gray-400 text-sm">
              需要 7 天数据，等待历史积累
            </p>
          ) : (
            <div className="space-y-1">
              {reputationDecline.map((r) => (
                <Link
                  key={r.gameId}
                  href={`/games/${r.gameId}`}
                  className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-50"
                >
                  <span className="text-sm font-medium truncate">
                    {r.game.nameZh || r.game.nameEn || "Unknown"}
                  </span>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className="text-xs text-red-600 font-mono font-bold">
                      -{r.drop}
                    </span>
                    <span className="text-xs text-gray-500 font-mono">
                      {r.current}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* 3. 社交爆发榜 Top 10 */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">社交爆发榜 Top 10</h3>
          {socialBurst.length === 0 ? (
            <p className="text-gray-400 text-sm">
              暂无数据，等待首次爬取
            </p>
          ) : (
            <div className="space-y-1">
              {socialBurst.map((s) => (
                <Link
                  key={s.gameId}
                  href={`/games/${s.gameId}`}
                  className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-50"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-medium truncate">
                      {s.game.nameZh || s.game.nameEn || "Unknown"}
                    </span>
                    <span className="text-xs text-gray-400 flex-shrink-0">
                      {s.topPlatform}
                    </span>
                  </div>
                  <span className="text-xs text-purple-600 font-mono font-bold flex-shrink-0">
                    {formatNumber(s.totalViews)}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* 4. IAA 适配榜 Top 10 */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">IAA 适配榜 Top 10</h3>
          {iaaCandidates.length === 0 ? (
            <p className="text-gray-400 text-sm">
              暂无数据，等待首次爬取
            </p>
          ) : (
            <div className="space-y-1">
              {iaaCandidates.map((s) => (
                <Link
                  key={s.id}
                  href={`/games/${s.gameId}`}
                  className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-50"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-medium truncate">
                      {s.game.nameZh || s.game.nameEn || "Unknown"}
                    </span>
                    <span className="text-xs text-gray-400 flex-shrink-0">
                      {s.game.genre ?? "-"}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span
                      className={`text-xs font-bold px-2 py-0.5 rounded ${
                        s.overallScore >= 75
                          ? "bg-green-100 text-green-800"
                          : "bg-yellow-100 text-yellow-800"
                      }`}
                    >
                      {s.overallScore}
                    </span>
                    <span className="text-xs text-blue-600 font-mono">
                      IAA {s.game.iaaSuitability}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Recent Alerts + Scraper Status */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">最新告警</h3>
          {recentAlerts.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无告警</p>
          ) : (
            <div className="space-y-2">
              {recentAlerts.map((a) => (
                <Link
                  key={a.id}
                  href={`/games/${a.gameId}`}
                  className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-50"
                >
                  <div>
                    <span className="text-sm font-medium">
                      {a.game.nameZh || a.game.nameEn}
                    </span>
                    <span className="text-xs text-gray-400 ml-2">
                      {a.alert.name}
                    </span>
                  </div>
                  <span className="text-xs text-gray-500">
                    {a.triggeredAt.toLocaleDateString("zh-CN")}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">爬虫状态</h3>
          {scraperStatus.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无爬虫记录</p>
          ) : (
            <div className="space-y-2">
              {scraperStatus.map((j) => (
                <div
                  key={j.id}
                  className="flex items-center justify-between py-2 px-3"
                >
                  <span className="text-sm font-medium">
                    {PLATFORM_LABELS[j.platform] ?? j.platform}
                  </span>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        j.status === "success"
                          ? "bg-green-100 text-green-700"
                          : j.status === "failed"
                            ? "bg-red-100 text-red-700"
                            : "bg-yellow-100 text-yellow-700"
                      }`}
                    >
                      {j.status}
                    </span>
                    <span className="text-xs text-gray-400">
                      {j.itemsScraped} items
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <p className="text-sm text-gray-500">{label}</p>
      <p className="text-3xl font-bold mt-1">{value.toLocaleString()}</p>
    </div>
  );
}
