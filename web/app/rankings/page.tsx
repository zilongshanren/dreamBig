import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { PLATFORM_LABELS } from "@/lib/utils";

interface SearchParams {
  platform?: string;
  chart?: string;
  region?: string;
}

async function getRankings(params: SearchParams) {
  try {
    const where: any = {};
    if (params.platform) {
      where.platformListing = { platform: params.platform };
    }
    if (params.chart) {
      where.chartType = params.chart;
    }
    if (params.region) {
      where.region = params.region;
    }

    // Get latest snapshot date
    const latestDate = await prisma.rankingSnapshot.findFirst({
      orderBy: { snapshotDate: "desc" },
      select: { snapshotDate: true },
      where,
    });

    if (!latestDate) return { rankings: [], date: null };

    const rankings = await prisma.rankingSnapshot.findMany({
      where: {
        ...where,
        snapshotDate: latestDate.snapshotDate,
      },
      orderBy: { rankPosition: "asc" },
      take: 200,
      include: {
        platformListing: {
          include: {
            game: {
              include: {
                potentialScores: {
                  orderBy: { scoredAt: "desc" },
                  take: 1,
                },
              },
            },
          },
        },
      },
    });

    return {
      rankings,
      date: latestDate.snapshotDate.toISOString().split("T")[0],
    };
  } catch {
    return { rankings: [], date: null };
  }
}

async function getBiggestMovers() {
  try {
    return await prisma.rankingSnapshot.findMany({
      where: {
        rankChange: { not: null },
        snapshotDate: {
          gte: new Date(new Date().setDate(new Date().getDate() - 1)),
        },
      },
      orderBy: { rankChange: "desc" },
      take: 10,
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

export default async function RankingsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const [{ rankings, date }, movers] = await Promise.all([
    getRankings(params),
    getBiggestMovers(),
  ]);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">排行榜</h2>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <form className="flex gap-4 items-end">
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
              榜单类型
            </label>
            <select
              name="chart"
              defaultValue={params.chart}
              className="border rounded px-3 py-1.5 text-sm"
            >
              <option value="">全部</option>
              <option value="top_free">免费榜</option>
              <option value="top_grossing">畅销榜</option>
              <option value="trending">热门榜</option>
              <option value="new">新品榜</option>
              <option value="top_sellers">销量榜</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">地区</label>
            <select
              name="region"
              defaultValue={params.region}
              className="border rounded px-3 py-1.5 text-sm"
            >
              <option value="">全部</option>
              <option value="CN">中国</option>
              <option value="US">美国</option>
              <option value="JP">日本</option>
              <option value="KR">韩国</option>
              <option value="GLOBAL">全球</option>
            </select>
          </div>
          <button
            type="submit"
            className="bg-gray-900 text-white px-4 py-1.5 rounded text-sm hover:bg-gray-700"
          >
            查看
          </button>
        </form>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Rankings Table */}
        <div className="col-span-2 bg-white rounded-lg shadow overflow-hidden">
          <div className="px-4 py-3 border-b bg-gray-50 flex justify-between items-center">
            <h3 className="font-semibold">
              排行{date ? ` (${date})` : ""}
            </h3>
            <span className="text-xs text-gray-400">
              {rankings.length} 条记录
            </span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-center px-3 py-2 font-medium text-gray-500 w-16">
                  排名
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-500">
                  游戏
                </th>
                <th className="text-center px-3 py-2 font-medium text-gray-500 w-20">
                  平台
                </th>
                <th className="text-center px-3 py-2 font-medium text-gray-500 w-20">
                  变动
                </th>
                <th className="text-center px-3 py-2 font-medium text-gray-500 w-16">
                  潜力
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rankings.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-3 py-8 text-center text-gray-400"
                  >
                    暂无排名数据
                  </td>
                </tr>
              ) : (
                rankings.map((r) => {
                  const game = r.platformListing.game;
                  const score = game.potentialScores[0];
                  return (
                    <tr key={r.id} className="hover:bg-gray-50">
                      <td className="text-center px-3 py-2 font-mono text-gray-500">
                        #{r.rankPosition}
                      </td>
                      <td className="px-3 py-2">
                        <Link
                          href={`/games/${game.id}`}
                          className="text-blue-600 hover:underline font-medium"
                        >
                          {game.nameZh || game.nameEn || r.platformListing.name}
                        </Link>
                      </td>
                      <td className="text-center px-3 py-2">
                        <span className="text-xs bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
                          {PLATFORM_LABELS[r.platformListing.platform] ||
                            r.platformListing.platform}
                        </span>
                      </td>
                      <td className="text-center px-3 py-2">
                        {r.rankChange != null ? (
                          <span
                            className={`text-xs font-mono ${
                              r.rankChange > 0
                                ? "text-green-600"
                                : r.rankChange < 0
                                  ? "text-red-500"
                                  : "text-gray-400"
                            }`}
                          >
                            {r.rankChange > 0
                              ? `+${r.rankChange}`
                              : r.rankChange === 0
                                ? "—"
                                : r.rankChange}
                          </span>
                        ) : (
                          <span className="text-xs text-purple-500">NEW</span>
                        )}
                      </td>
                      <td className="text-center px-3 py-2">
                        {score ? (
                          <span
                            className={`text-xs font-bold ${
                              score.overallScore >= 75
                                ? "text-green-600"
                                : score.overallScore >= 50
                                  ? "text-yellow-600"
                                  : "text-gray-400"
                            }`}
                          >
                            {score.overallScore}
                          </span>
                        ) : (
                          "-"
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Biggest Movers */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold mb-4">今日最大变动</h3>
          {movers.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无数据</p>
          ) : (
            <div className="space-y-2">
              {movers.map((m) => (
                <Link
                  key={m.id}
                  href={`/games/${m.platformListing.game.id}`}
                  className="flex items-center justify-between py-2 px-2 rounded hover:bg-gray-50"
                >
                  <span className="text-sm truncate max-w-[140px]">
                    {m.platformListing.game.nameZh ||
                      m.platformListing.game.nameEn ||
                      m.platformListing.name}
                  </span>
                  <span
                    className={`text-sm font-mono font-bold ${
                      (m.rankChange || 0) > 0
                        ? "text-green-600"
                        : "text-red-500"
                    }`}
                  >
                    {(m.rankChange || 0) > 0 ? "+" : ""}
                    {m.rankChange}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
