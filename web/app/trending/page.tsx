import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { PLATFORM_LABELS } from "@/lib/utils";

export const dynamic = "force-dynamic";

async function getTrendingGames() {
  try {
    return await prisma.potentialScore.findMany({
      where: {
        scoredAt: new Date(new Date().toISOString().split("T")[0]),
        rankingVelocity: { gte: 30 },
      },
      orderBy: { rankingVelocity: "desc" },
      take: 50,
      include: {
        game: {
          include: {
            platformListings: {
              select: { platform: true },
            },
          },
        },
      },
    });
  } catch {
    return [];
  }
}

async function getNewEntries() {
  try {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);

    return await prisma.rankingSnapshot.findMany({
      where: {
        previousRank: null,
        snapshotDate: { gte: yesterday },
      },
      orderBy: { rankPosition: "asc" },
      take: 20,
      include: {
        platformListing: {
          include: { game: true },
        },
      },
    });
  } catch {
    return [];
  }
}

async function getGenreTrends() {
  try {
    const games = await prisma.game.groupBy({
      by: ["genre"],
      _count: true,
      where: { genre: { not: null } },
      orderBy: { _count: { genre: "desc" } },
      take: 20,
    });
    return games;
  } catch {
    return [];
  }
}

export default async function TrendingPage() {
  const [trending, newEntries] = await Promise.all([
    getTrendingGames(),
    getNewEntries(),
  ]);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">趋势分析</h2>

      <div className="space-y-6">
        {/* Velocity Leaderboard */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">
            上升速度排行榜 (Ranking Velocity)
          </h3>
          {trending.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">
              暂无数据
            </p>
          ) : (
            <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[600px]">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-center px-3 py-2 font-medium text-gray-500 w-12">
                    #
                  </th>
                  <th className="text-left px-3 py-2 font-medium text-gray-500">
                    游戏
                  </th>
                  <th className="text-left px-3 py-2 font-medium text-gray-500">
                    类型
                  </th>
                  <th className="text-left px-3 py-2 font-medium text-gray-500">
                    平台
                  </th>
                  <th className="text-center px-3 py-2 font-medium text-gray-500">
                    上升速度
                  </th>
                  <th className="text-center px-3 py-2 font-medium text-gray-500">
                    社交热度
                  </th>
                  <th className="text-center px-3 py-2 font-medium text-gray-500">
                    总分
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {trending.map((t, i) => (
                  <tr key={t.id} className="hover:bg-gray-50">
                    <td className="text-center px-3 py-2 text-gray-400">
                      {i + 1}
                    </td>
                    <td className="px-3 py-2">
                      <Link
                        href={`/games/${t.gameId}`}
                        className="text-blue-600 hover:underline font-medium"
                      >
                        {t.game.nameZh || t.game.nameEn || "Unknown"}
                      </Link>
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-xs bg-gray-100 px-2 py-0.5 rounded">
                        {t.game.genre || "-"}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex gap-1">
                        {t.game.platformListings.map((pl) => (
                          <span
                            key={pl.platform}
                            className="text-xs bg-blue-50 text-blue-700 px-1 py-0.5 rounded"
                          >
                            {PLATFORM_LABELS[pl.platform]?.slice(0, 2) ||
                              pl.platform.slice(0, 2)}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="text-center px-3 py-2">
                      <span className="text-green-600 font-bold font-mono">
                        {t.rankingVelocity}
                      </span>
                    </td>
                    <td className="text-center px-3 py-2">
                      <span className="font-mono text-purple-600">
                        {t.socialBuzz}
                      </span>
                    </td>
                    <td className="text-center px-3 py-2">
                      <span
                        className={`font-bold ${
                          t.overallScore >= 75
                            ? "text-green-600"
                            : t.overallScore >= 50
                              ? "text-yellow-600"
                              : "text-gray-500"
                        }`}
                      >
                        {t.overallScore}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>

        {/* New Chart Entries */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">新入榜游戏</h3>
          {newEntries.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">
              暂无新入榜游戏
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {newEntries.map((n) => (
                <Link
                  key={n.id}
                  href={`/games/${n.platformListing.game.id}`}
                  className="p-3 rounded border hover:shadow transition-shadow"
                >
                  <p className="font-medium text-sm truncate">
                    {n.platformListing.game.nameZh ||
                      n.platformListing.game.nameEn ||
                      n.platformListing.name}
                  </p>
                  <div className="flex justify-between mt-2 text-xs text-gray-500">
                    <span>
                      {PLATFORM_LABELS[n.platformListing.platform] ||
                        n.platformListing.platform}
                    </span>
                    <span className="text-purple-600 font-medium">
                      #{n.rankPosition}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
