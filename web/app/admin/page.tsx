import { prisma } from "@/lib/prisma";
import { PLATFORM_LABELS } from "@/lib/utils";

export const dynamic = "force-dynamic";

async function getScraperJobs() {
  try {
    return await prisma.scrapeJob.findMany({
      orderBy: { startedAt: "desc" },
      take: 50,
    });
  } catch {
    return [];
  }
}

async function getDataStats() {
  try {
    const [games, listings, snapshots, signals, ads, scores] =
      await Promise.all([
        prisma.game.count(),
        prisma.platformListing.count(),
        prisma.rankingSnapshot.count(),
        prisma.socialSignal.count(),
        prisma.adIntelligence.count(),
        prisma.potentialScore.count(),
      ]);
    return { games, listings, snapshots, signals, ads, scores };
  } catch {
    return {
      games: 0,
      listings: 0,
      snapshots: 0,
      signals: 0,
      ads: 0,
      scores: 0,
    };
  }
}

async function getPlatformCoverage() {
  try {
    return await prisma.platformListing.groupBy({
      by: ["platform"],
      _count: true,
    });
  } catch {
    return [];
  }
}

export default async function AdminPage() {
  const [jobs, stats, coverage] = await Promise.all([
    getScraperJobs(),
    getDataStats(),
    getPlatformCoverage(),
  ]);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">管理后台</h2>

      {/* Data Stats */}
      <div className="grid grid-cols-6 gap-4 mb-8">
        <StatCard label="游戏" value={stats.games} />
        <StatCard label="平台记录" value={stats.listings} />
        <StatCard label="排名快照" value={stats.snapshots} />
        <StatCard label="社交信号" value={stats.signals} />
        <StatCard label="广告数据" value={stats.ads} />
        <StatCard label="潜力评分" value={stats.scores} />
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Platform Coverage */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold mb-4">平台覆盖</h3>
          {coverage.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无数据</p>
          ) : (
            <div className="space-y-2">
              {coverage.map((c) => (
                <div
                  key={c.platform}
                  className="flex items-center justify-between py-1.5"
                >
                  <span className="text-sm">
                    {PLATFORM_LABELS[c.platform] || c.platform}
                  </span>
                  <span className="text-sm font-mono text-gray-500">
                    {c._count}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Scraper Job History */}
        <div className="col-span-2 bg-white rounded-lg shadow overflow-hidden">
          <div className="px-4 py-3 border-b bg-gray-50 flex justify-between items-center">
            <h3 className="font-semibold">爬虫任务历史</h3>
            <form action="/api/admin/trigger" method="POST">
              <button
                type="submit"
                className="text-xs bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-500"
              >
                手动触发全部
              </button>
            </form>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-3 py-2 font-medium text-gray-500">
                  平台
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-500">
                  类型
                </th>
                <th className="text-center px-3 py-2 font-medium text-gray-500">
                  状态
                </th>
                <th className="text-center px-3 py-2 font-medium text-gray-500">
                  数量
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-500">
                  时间
                </th>
                <th className="text-left px-3 py-2 font-medium text-gray-500">
                  错误
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {jobs.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-3 py-8 text-center text-gray-400"
                  >
                    暂无爬虫任务记录
                  </td>
                </tr>
              ) : (
                jobs.map((job) => (
                  <tr key={job.id} className="hover:bg-gray-50">
                    <td className="px-3 py-2">
                      {PLATFORM_LABELS[job.platform] || job.platform}
                    </td>
                    <td className="px-3 py-2 text-gray-500">{job.jobType}</td>
                    <td className="text-center px-3 py-2">
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          job.status === "success"
                            ? "bg-green-100 text-green-700"
                            : job.status === "failed"
                              ? "bg-red-100 text-red-700"
                              : job.status === "running"
                                ? "bg-blue-100 text-blue-700"
                                : "bg-yellow-100 text-yellow-700"
                        }`}
                      >
                        {job.status}
                      </span>
                    </td>
                    <td className="text-center px-3 py-2 font-mono text-gray-500">
                      {job.itemsScraped}
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-400">
                      {job.startedAt?.toLocaleString("zh-CN") || "-"}
                    </td>
                    <td className="px-3 py-2 text-xs text-red-500 max-w-[200px] truncate">
                      {job.errorMessage || "-"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white rounded-lg shadow p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-2xl font-bold mt-1">{value.toLocaleString()}</p>
    </div>
  );
}
