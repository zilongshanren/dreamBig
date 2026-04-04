import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { PLATFORM_LABELS } from "@/lib/utils";

interface SearchParams {
  genre?: string;
  platform?: string;
  minScore?: string;
  sort?: string;
  order?: string;
  search?: string;
  page?: string;
}

const PAGE_SIZE = 50;

async function getGames(params: SearchParams) {
  try {
    const page = parseInt(params.page || "1");
    const skip = (page - 1) * PAGE_SIZE;

    const where: any = {};

    if (params.genre) {
      where.genre = params.genre;
    }
    if (params.search) {
      where.OR = [
        { nameZh: { contains: params.search, mode: "insensitive" } },
        { nameEn: { contains: params.search, mode: "insensitive" } },
      ];
    }
    if (params.platform) {
      where.platformListings = {
        some: { platform: params.platform },
      };
    }

    // If filtering by score, join with potential_scores
    const scoreFilter =
      params.minScore && parseInt(params.minScore) > 0
        ? {
            potentialScores: {
              some: { overallScore: { gte: parseInt(params.minScore) } },
            },
          }
        : {};

    const [games, total] = await Promise.all([
      prisma.game.findMany({
        where: { ...where, ...scoreFilter },
        include: {
          platformListings: {
            select: { platform: true, rating: true, ratingCount: true },
          },
          potentialScores: {
            orderBy: { scoredAt: "desc" },
            take: 1,
          },
        },
        orderBy: params.sort
          ? { [params.sort]: params.order || "desc" }
          : { updatedAt: "desc" },
        take: PAGE_SIZE,
        skip,
      }),
      prisma.game.count({ where: { ...where, ...scoreFilter } }),
    ]);

    return { games, total, page, totalPages: Math.ceil(total / PAGE_SIZE) };
  } catch {
    return { games: [], total: 0, page: 1, totalPages: 0 };
  }
}

async function getGenres() {
  try {
    const genres = await prisma.game.groupBy({
      by: ["genre"],
      _count: true,
      where: { genre: { not: null } },
      orderBy: { _count: { genre: "desc" } },
    });
    return genres;
  } catch {
    return [];
  }
}

export default async function GamesPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const [{ games, total, page, totalPages }, genres] = await Promise.all([
    getGames(params),
    getGenres(),
  ]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">游戏库</h2>
        <span className="text-sm text-gray-500">共 {total} 款游戏</span>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <form className="flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">搜索</label>
            <input
              name="search"
              type="text"
              defaultValue={params.search}
              placeholder="游戏名称..."
              className="border rounded px-3 py-1.5 text-sm w-48"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">类型</label>
            <select
              name="genre"
              defaultValue={params.genre}
              className="border rounded px-3 py-1.5 text-sm"
            >
              <option value="">全部</option>
              {genres.map((g) => (
                <option key={g.genre} value={g.genre!}>
                  {g.genre} ({g._count})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">平台</label>
            <select
              name="platform"
              defaultValue={params.platform}
              className="border rounded px-3 py-1.5 text-sm"
            >
              <option value="">全部</option>
              {Object.entries(PLATFORM_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">
              最低评分
            </label>
            <select
              name="minScore"
              defaultValue={params.minScore}
              className="border rounded px-3 py-1.5 text-sm"
            >
              <option value="0">不限</option>
              <option value="50">50+</option>
              <option value="60">60+</option>
              <option value="70">70+</option>
              <option value="80">80+</option>
              <option value="90">90+</option>
            </select>
          </div>
          <button
            type="submit"
            className="bg-gray-900 text-white px-4 py-1.5 rounded text-sm hover:bg-gray-700"
          >
            筛选
          </button>
        </form>
      </div>

      {/* Game Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 font-medium text-gray-500">
                游戏
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">
                类型
              </th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">
                平台
              </th>
              <th className="text-center px-4 py-3 font-medium text-gray-500">
                评分
              </th>
              <th className="text-center px-4 py-3 font-medium text-gray-500">
                IAA 适配
              </th>
              <th className="text-center px-4 py-3 font-medium text-gray-500">
                潜力分
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {games.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-8 text-center text-gray-400"
                >
                  暂无数据，等待首次爬取完成
                </td>
              </tr>
            ) : (
              games.map((game) => {
                const score = game.potentialScores[0];
                const bestRating = game.platformListings.reduce(
                  (best, pl) => {
                    const r = pl.rating ? Number(pl.rating) : 0;
                    return r > best ? r : best;
                  },
                  0
                );

                return (
                  <tr key={game.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <Link
                        href={`/games/${game.id}`}
                        className="font-medium text-blue-600 hover:underline"
                      >
                        {game.nameZh || game.nameEn || "Unknown"}
                      </Link>
                      {game.nameEn && game.nameZh && (
                        <span className="text-gray-400 text-xs ml-2">
                          {game.nameEn}
                        </span>
                      )}
                      {game.developer && (
                        <p className="text-xs text-gray-400">
                          {game.developer}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs bg-gray-100 px-2 py-0.5 rounded">
                        {game.genre || "-"}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1 flex-wrap">
                        {game.platformListings.map((pl) => (
                          <span
                            key={pl.platform}
                            className="text-xs bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded"
                          >
                            {PLATFORM_LABELS[pl.platform] || pl.platform}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-center">
                      {bestRating > 0 ? bestRating.toFixed(1) : "-"}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span
                        className={`text-xs font-medium ${
                          game.iaaSuitability >= 80
                            ? "text-green-600"
                            : game.iaaSuitability >= 60
                              ? "text-yellow-600"
                              : "text-gray-400"
                        }`}
                      >
                        {game.iaaSuitability || "-"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      {score ? (
                        <span
                          className={`font-bold ${
                            score.overallScore >= 75
                              ? "text-green-600"
                              : score.overallScore >= 50
                                ? "text-yellow-600"
                                : "text-gray-500"
                          }`}
                        >
                          {score.overallScore}
                        </span>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center gap-2 mt-6">
          {page > 1 && (
            <Link
              href={`/games?${new URLSearchParams({ ...params, page: String(page - 1) })}`}
              className="px-3 py-1 border rounded text-sm hover:bg-gray-100"
            >
              上一页
            </Link>
          )}
          <span className="px-3 py-1 text-sm text-gray-500">
            {page} / {totalPages}
          </span>
          {page < totalPages && (
            <Link
              href={`/games?${new URLSearchParams({ ...params, page: String(page + 1) })}`}
              className="px-3 py-1 border rounded text-sm hover:bg-gray-100"
            >
              下一页
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
