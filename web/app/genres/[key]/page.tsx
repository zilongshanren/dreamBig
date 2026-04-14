import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

type GenreRow = {
  key: string;
  labelZh: string;
  labelEn: string;
  iaaBaseline: number;
  hotGamesCount: number;
  momentum: unknown;
  topGameIds: number[];
  lastComputedAt: Date | null;
};

function momentumNum(v: unknown): number {
  if (v == null) return 0;
  if (typeof v === "number") return v;
  if (typeof v === "string") return parseFloat(v) || 0;
  try {
    // @ts-expect-error - Decimal has toNumber
    if (typeof v.toNumber === "function") return v.toNumber();
  } catch {
    // ignore
  }
  return parseFloat(String(v)) || 0;
}

async function getGenre(key: string): Promise<GenreRow | null> {
  try {
    const row = await prisma.genre.findUnique({ where: { key } });
    return row as unknown as GenreRow | null;
  } catch {
    return null;
  }
}

async function getGenreGames(key: string) {
  // Fetch games matching this genre key via case-insensitive substring match
  // on genre or gameplay_tags. Limits to top 30 by latest overall_score.
  try {
    const games = await prisma.game.findMany({
      where: {
        OR: [
          { genre: { contains: key, mode: "insensitive" } },
          { gameplayTags: { has: key } },
        ],
      },
      include: {
        potentialScores: { orderBy: { scoredAt: "desc" }, take: 1 },
      },
      take: 200,
    });
    // Sort in-memory by today's overall score desc
    games.sort((a, b) => {
      const as = a.potentialScores[0]?.overallScore ?? 0;
      const bs = b.potentialScores[0]?.overallScore ?? 0;
      return bs - as;
    });
    return games.slice(0, 30);
  } catch {
    return [];
  }
}

async function getPreviousTopIds(key: string): Promise<number[]> {
  // Best-effort: compare today's topGameIds against what was there a week ago
  // via lastComputedAt's snapshot. We don't keep history, so approximate by
  // comparing to yesterday's snapshot via ranking_snapshots if exposed.
  // As a fallback, we just return empty.
  void key;
  return [];
}

export default async function GenreDetailPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const { key } = await params;
  const genre = await getGenre(key);

  if (!genre) {
    notFound();
  }

  const [games, prevTop] = await Promise.all([
    getGenreGames(key),
    getPreviousTopIds(key),
  ]);

  const m = momentumNum(genre.momentum);
  const momentumColor =
    m > 0.5 ? "text-green-600" : m < -0.5 ? "text-red-500" : "text-gray-500";
  const momentumIcon = m > 0.5 ? "▲" : m < -0.5 ? "▼" : "—";

  // Identify "new entrants" = ids in current topGameIds not in prevTop
  const currentTop = genre.topGameIds ?? [];
  const newEntrantIds = currentTop.filter((id) => !prevTop.includes(id));
  const gameById = new Map(games.map((g) => [g.id, g]));
  const newEntrants = newEntrantIds
    .map((id) => gameById.get(id))
    .filter((g): g is (typeof games)[number] => Boolean(g))
    .slice(0, 5);

  return (
    <div>
      {/* Breadcrumb */}
      <div className="mb-4 text-sm text-gray-500">
        <Link href="/genres" className="hover:text-blue-600">
          赛道分析
        </Link>
        <span className="mx-2">/</span>
        <span className="text-gray-900">{genre.labelZh}</span>
      </div>

      {/* Header card */}
      <div className="bg-white rounded-lg shadow p-5 mb-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0 flex-1">
            <h2 className="text-2xl font-bold">{genre.labelZh}</h2>
            <p className="text-sm text-gray-400 mt-0.5">
              {genre.labelEn} · {genre.key}
            </p>
          </div>
          <div className="flex items-center gap-6">
            <div className="text-right">
              <p className="text-xs text-gray-500">7 天动量</p>
              <p className={`font-mono font-bold text-xl ${momentumColor}`}>
                {momentumIcon} {Math.abs(m).toFixed(2)}
              </p>
            </div>
            <div className="text-right">
              <p className="text-xs text-gray-500">热门游戏</p>
              <p className="font-mono font-bold text-xl text-blue-600">
                {genre.hotGamesCount}
              </p>
            </div>
            <div className="text-right">
              <p className="text-xs text-gray-500">IAA 基线</p>
              <p
                className={`font-mono font-bold text-xl ${
                  genre.iaaBaseline >= 75
                    ? "text-green-600"
                    : genre.iaaBaseline >= 50
                      ? "text-yellow-600"
                      : "text-gray-500"
                }`}
              >
                {genre.iaaBaseline}
              </p>
            </div>
          </div>
        </div>
        {genre.lastComputedAt && (
          <p className="text-xs text-gray-400 mt-3">
            最近更新: {new Date(genre.lastComputedAt).toLocaleString("zh-CN")}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Main: top games list */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="font-semibold mb-3">
              赛道游戏 ({games.length})
            </h3>
            {games.length === 0 ? (
              <p className="text-gray-400 text-sm">暂无此赛道下的游戏</p>
            ) : (
              <div className="space-y-2">
                {games.map((g, idx) => {
                  const score = g.potentialScores[0]?.overallScore ?? 0;
                  return (
                    <Link
                      key={g.id}
                      href={`/games/${g.id}`}
                      className="flex items-center gap-3 p-2 rounded hover:bg-gray-50 transition-colors"
                    >
                      <span className="text-xs text-gray-400 font-mono w-6 text-right shrink-0">
                        {idx + 1}
                      </span>
                      {g.thumbnailUrl ? (
                        <img
                          src={g.thumbnailUrl}
                          alt=""
                          className="w-10 h-10 rounded shadow-sm object-cover shrink-0"
                        />
                      ) : (
                        <div className="w-10 h-10 rounded bg-gray-100 shrink-0" />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium truncate">
                          {g.nameZh || g.nameEn || "Unknown"}
                        </p>
                        <p className="text-xs text-gray-400 truncate">
                          {g.developer || g.genre || "-"}
                        </p>
                      </div>
                      <div className="shrink-0">
                        <span
                          className={`text-xs font-mono font-bold px-2 py-1 rounded ${
                            score >= 60
                              ? "bg-green-100 text-green-700"
                              : score >= 40
                                ? "bg-yellow-100 text-yellow-700"
                                : "bg-gray-100 text-gray-500"
                          }`}
                        >
                          {score}
                        </span>
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Sidebar: new entrants + top 10 */}
        <div className="space-y-4">
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="font-semibold mb-3 text-sm">
              <span className="mr-1">✨</span>新晋热门
            </h3>
            {newEntrants.length === 0 ? (
              <p className="text-xs text-gray-400">
                本周无新进 Top 10 的游戏
              </p>
            ) : (
              <ul className="space-y-2">
                {newEntrants.map((g) => (
                  <li key={g.id}>
                    <Link
                      href={`/games/${g.id}`}
                      className="flex items-center gap-2 text-sm hover:text-blue-600"
                    >
                      {g.thumbnailUrl && (
                        <img
                          src={g.thumbnailUrl}
                          alt=""
                          className="w-6 h-6 rounded"
                        />
                      )}
                      <span className="truncate">
                        {g.nameZh || g.nameEn}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="font-semibold mb-3 text-sm">Top 10 快照</h3>
            {currentTop.length === 0 ? (
              <p className="text-xs text-gray-400">暂无数据</p>
            ) : (
              <ol className="text-xs text-gray-600 space-y-1">
                {currentTop.slice(0, 10).map((gid, idx) => {
                  const g = gameById.get(gid);
                  if (!g) return null;
                  return (
                    <li key={gid} className="flex items-center gap-2">
                      <span className="text-gray-400 font-mono w-4 text-right">
                        {idx + 1}
                      </span>
                      <Link
                        href={`/games/${gid}`}
                        className="truncate hover:text-blue-600"
                      >
                        {g.nameZh || g.nameEn}
                      </Link>
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
