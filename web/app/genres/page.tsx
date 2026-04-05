import Link from "next/link";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

type GenreRow = {
  key: string;
  labelZh: string;
  labelEn: string;
  iaaBaseline: number;
  hotGamesCount: number;
  momentum: unknown; // Prisma Decimal
  topGameIds: number[];
  lastComputedAt: Date | null;
};

async function getGenres(): Promise<GenreRow[]> {
  try {
    const rows = await prisma.genre.findMany({
      orderBy: [{ momentum: "desc" }, { hotGamesCount: "desc" }],
    });
    return rows as unknown as GenreRow[];
  } catch {
    return [];
  }
}

async function getGameThumbs(ids: number[]) {
  if (!ids.length) return new Map<number, { id: number; nameZh: string | null; nameEn: string | null; thumbnailUrl: string | null }>();
  try {
    const games = await prisma.game.findMany({
      where: { id: { in: ids } },
      select: { id: true, nameZh: true, nameEn: true, thumbnailUrl: true },
    });
    return new Map(games.map((g) => [g.id, g]));
  } catch {
    return new Map<number, { id: number; nameZh: string | null; nameEn: string | null; thumbnailUrl: string | null }>();
  }
}

function momentumNum(v: unknown): number {
  if (v == null) return 0;
  if (typeof v === "number") return v;
  if (typeof v === "string") return parseFloat(v) || 0;
  // Prisma Decimal has toNumber() or can be coerced
  try {
    // @ts-expect-error - Decimal has toNumber
    if (typeof v.toNumber === "function") return v.toNumber();
  } catch {
    // ignore
  }
  return parseFloat(String(v)) || 0;
}

function MomentumBadge({ value }: { value: number }) {
  const rounded = Math.round(value * 10) / 10;
  if (value > 0.5) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs font-mono text-green-600">
        <span>▲</span>
        <span>{rounded.toFixed(1)}</span>
      </span>
    );
  }
  if (value < -0.5) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs font-mono text-red-500">
        <span>▼</span>
        <span>{Math.abs(rounded).toFixed(1)}</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-0.5 text-xs font-mono text-gray-400">
      <span>—</span>
      <span>{rounded.toFixed(1)}</span>
    </span>
  );
}

export default async function GenresPage() {
  const genres = await getGenres();
  const allTopIds = Array.from(
    new Set(genres.flatMap((g) => (g.topGameIds ?? []).slice(0, 3)))
  );
  const thumbs = await getGameThumbs(allTopIds);

  const totalHot = genres.reduce((s, g) => s + (g.hotGamesCount ?? 0), 0);
  const totalGenres = genres.length;

  return (
    <div>
      {/* Title */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold">赛道分析</h2>
        <p className="text-sm text-gray-500 mt-1">
          按品类聚合的潜力赛道热度与 7
          天动量，帮助发现新兴题材与规模化机会
        </p>
      </div>

      {/* Summary row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500">监控赛道</p>
          <p className="text-2xl font-bold">{totalGenres}</p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500">热门游戏总数</p>
          <p className="text-2xl font-bold text-blue-600">{totalHot}</p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500">最强动量</p>
          <p className="text-lg font-semibold">
            {genres[0]
              ? `${genres[0].labelZh} ${momentumNum(genres[0].momentum).toFixed(2)}`
              : "—"}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500">IAA 基线最高</p>
          <p className="text-lg font-semibold">
            {[...genres].sort((a, b) => b.iaaBaseline - a.iaaBaseline)[0]?.labelZh ??
              "—"}
          </p>
        </div>
      </div>

      {genres.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-8 text-center">
          <p className="text-gray-400 text-sm">
            暂无赛道数据，请等待每日聚合任务运行
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {genres.map((g) => {
            const m = momentumNum(g.momentum);
            const top3 = (g.topGameIds ?? []).slice(0, 3);
            return (
              <Link
                key={g.key}
                href={`/genres/${g.key}`}
                className="bg-white rounded-lg shadow p-4 hover:shadow-md transition-shadow block"
              >
                {/* Header */}
                <div className="flex items-start justify-between mb-3">
                  <div className="min-w-0 flex-1">
                    <h3 className="font-semibold text-base truncate">
                      {g.labelZh}
                    </h3>
                    <p className="text-xs text-gray-400 truncate">{g.labelEn}</p>
                  </div>
                  <MomentumBadge value={m} />
                </div>

                {/* Stats row */}
                <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
                  <div className="bg-gray-50 rounded px-2 py-1.5">
                    <p className="text-gray-500">热门游戏</p>
                    <p
                      className={`font-mono font-bold ${
                        g.hotGamesCount > 10
                          ? "text-blue-600"
                          : g.hotGamesCount > 3
                            ? "text-gray-700"
                            : "text-gray-400"
                      }`}
                    >
                      {g.hotGamesCount}
                    </p>
                  </div>
                  <div className="bg-gray-50 rounded px-2 py-1.5">
                    <p className="text-gray-500">IAA 基线</p>
                    <p
                      className={`font-mono font-bold ${
                        g.iaaBaseline >= 75
                          ? "text-green-600"
                          : g.iaaBaseline >= 50
                            ? "text-yellow-600"
                            : "text-gray-500"
                      }`}
                    >
                      {g.iaaBaseline}
                    </p>
                  </div>
                </div>

                {/* Top-3 thumbnails */}
                {top3.length > 0 && (
                  <div>
                    <p className="text-xs text-gray-500 mb-1.5">
                      Top {top3.length} 游戏
                    </p>
                    <div className="flex items-center gap-2">
                      {top3.map((gid) => {
                        const game = thumbs.get(gid);
                        if (!game) return null;
                        const name = game.nameZh || game.nameEn || "?";
                        return (
                          <div
                            key={gid}
                            className="flex items-center gap-1.5 min-w-0 flex-1"
                          >
                            {game.thumbnailUrl ? (
                              <img
                                src={game.thumbnailUrl}
                                alt={name}
                                className="w-8 h-8 rounded shadow-sm object-cover shrink-0"
                              />
                            ) : (
                              <div className="w-8 h-8 rounded bg-gray-100 shrink-0" />
                            )}
                            <span className="text-xs text-gray-600 truncate">
                              {name}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
