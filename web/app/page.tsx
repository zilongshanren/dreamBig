import Link from "next/link";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

async function getStats() {
  try {
    const [gameCount, platformCount, alertCount, recentScores] =
      await Promise.all([
        prisma.game.count(),
        prisma.platformListing.count(),
        prisma.alertEvent.count({
          where: {
            triggeredAt: { gte: new Date(Date.now() - 24 * 60 * 60 * 1000) },
          },
        }),
        prisma.potentialScore.findMany({
          where: { scoredAt: new Date(new Date().toISOString().split("T")[0]) },
          orderBy: { overallScore: "desc" },
          take: 10,
          include: { game: true },
        }),
      ]);

    return { gameCount, platformCount, alertCount, recentScores };
  } catch {
    return { gameCount: 0, platformCount: 0, alertCount: 0, recentScores: [] };
  }
}

async function getRisingStars() {
  try {
    return await prisma.potentialScore.findMany({
      where: {
        scoredAt: new Date(new Date().toISOString().split("T")[0]),
        rankingVelocity: { gte: 60 },
      },
      orderBy: { rankingVelocity: "desc" },
      take: 10,
      include: { game: true },
    });
  } catch {
    return [];
  }
}

async function getRecentAlerts() {
  try {
    return await prisma.alertEvent.findMany({
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

export default async function DashboardPage() {
  const [stats, risingStars, recentAlerts, scraperStatus] = await Promise.all([
    getStats(),
    getRisingStars(),
    getRecentAlerts(),
    getScraperStatus(),
  ]);

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">Dashboard</h2>

      {/* Stat Cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <StatCard label="总游戏数" value={stats.gameCount} />
        <StatCard label="平台记录" value={stats.platformCount} />
        <StatCard label="今日告警" value={stats.alertCount} />
        <StatCard
          label="高潜力 (75+)"
          value={
            stats.recentScores.filter((s) => s.overallScore >= 75).length
          }
        />
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Rising Stars */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">Rising Stars (上升最快)</h3>
          {risingStars.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无数据，等待首次爬取</p>
          ) : (
            <div className="space-y-2">
              {risingStars.map((s) => (
                <Link
                  key={s.id}
                  href={`/games/${s.gameId}`}
                  className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-50"
                >
                  <span className="text-sm font-medium truncate">
                    {s.game.nameZh || s.game.nameEn || "Unknown"}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-green-600 font-mono">
                      V:{s.rankingVelocity}
                    </span>
                    <span
                      className={`text-xs font-bold px-2 py-0.5 rounded ${
                        s.overallScore >= 75
                          ? "bg-green-100 text-green-800"
                          : s.overallScore >= 50
                            ? "bg-yellow-100 text-yellow-800"
                            : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {s.overallScore}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Recent Alerts */}
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

        {/* Top Potential Games */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-lg mb-4">
            今日 Top 10 高潜力游戏
          </h3>
          {stats.recentScores.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无评分数据</p>
          ) : (
            <div className="space-y-2">
              {stats.recentScores.map((s, i) => (
                <Link
                  key={s.id}
                  href={`/games/${s.gameId}`}
                  className="flex items-center justify-between py-2 px-3 rounded hover:bg-gray-50"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-gray-400 text-sm w-5">
                      #{i + 1}
                    </span>
                    <span className="text-sm font-medium truncate">
                      {s.game.nameZh || s.game.nameEn || "Unknown"}
                    </span>
                  </div>
                  <span
                    className={`text-sm font-bold ${
                      s.overallScore >= 75
                        ? "text-green-600"
                        : s.overallScore >= 50
                          ? "text-yellow-600"
                          : "text-gray-500"
                    }`}
                  >
                    {s.overallScore}
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Scraper Status */}
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
                  <span className="text-sm font-medium">{j.platform}</span>
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
