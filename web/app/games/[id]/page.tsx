import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";
import { PLATFORM_LABELS, PLATFORM_COLORS, formatNumber } from "@/lib/utils";
import { ScoreRadar } from "@/components/charts/score-radar";

export const dynamic = "force-dynamic";
import { RankingChart } from "@/components/charts/ranking-chart";

async function getGame(id: number) {
  try {
    const game = await prisma.game.findUnique({
      where: { id },
      include: {
        platformListings: {
          include: {
            rankingSnapshots: {
              orderBy: { snapshotDate: "desc" },
              take: 30,
            },
          },
        },
        potentialScores: {
          orderBy: { scoredAt: "desc" },
          take: 1,
        },
        socialSignals: {
          orderBy: { signalDate: "desc" },
          take: 14,
        },
        adIntelligence: {
          orderBy: { signalDate: "desc" },
          take: 7,
        },
        gameTags: true,
      },
    });
    return game;
  } catch {
    return null;
  }
}

export default async function GameDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const game = await getGame(parseInt(id));

  if (!game) return notFound();

  const score = game.potentialScores[0];

  // Prepare ranking history for chart
  const rankingHistory = game.platformListings.flatMap((pl) =>
    pl.rankingSnapshots.map((rs) => ({
      date: rs.snapshotDate.toISOString().split("T")[0],
      platform: pl.platform,
      rank: rs.rankPosition,
      chart: rs.chartType,
    }))
  );

  const scoreData = score
    ? [
        { dimension: "排名速度", value: score.rankingVelocity, fullMark: 100 },
        { dimension: "玩法适配", value: score.genreFit, fullMark: 100 },
        { dimension: "社交热度", value: score.socialBuzz, fullMark: 100 },
        { dimension: "跨平台", value: score.crossPlatform, fullMark: 100 },
        { dimension: "评分质量", value: score.ratingQuality, fullMark: 100 },
        { dimension: "竞争空白", value: score.competitionGap, fullMark: 100 },
        { dimension: "广告活跃", value: score.adActivity, fullMark: 100 },
      ]
    : [];

  return (
    <div>
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <Link
            href="/games"
            className="text-sm text-gray-400 hover:text-gray-600 mb-2 inline-block"
          >
            &larr; 返回游戏库
          </Link>
          <h2 className="text-2xl font-bold">
            {game.nameZh || game.nameEn || "Unknown"}
          </h2>
          {game.nameEn && game.nameZh && (
            <p className="text-gray-500">{game.nameEn}</p>
          )}
          {game.developer && (
            <p className="text-sm text-gray-400 mt-1">
              开发者: {game.developer}
            </p>
          )}
        </div>
        {score && (
          <div className="text-center">
            <div
              className={`text-4xl font-bold ${
                score.overallScore >= 75
                  ? "text-green-600"
                  : score.overallScore >= 50
                    ? "text-yellow-600"
                    : "text-gray-500"
              }`}
            >
              {score.overallScore}
            </div>
            <p className="text-xs text-gray-400">潜力评分</p>
          </div>
        )}
      </div>

      {/* Tags */}
      <div className="flex gap-2 mb-6 flex-wrap">
        {game.genre && (
          <span className="text-xs bg-purple-100 text-purple-700 px-2 py-1 rounded">
            {game.genre}
          </span>
        )}
        {game.gameplayTags.map((tag) => (
          <span
            key={tag}
            className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded"
          >
            {tag}
          </span>
        ))}
        {game.gameTags.map((gt) => (
          <span
            key={gt.tag}
            className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded"
          >
            {gt.tag}
          </span>
        ))}
        <span className="text-xs bg-orange-100 text-orange-700 px-2 py-1 rounded">
          IAA 适配: {game.iaaSuitability}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Score Radar */}
        {score && (
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="font-semibold mb-4">评分详情</h3>
            <ScoreRadar data={scoreData} />
            <div className="grid grid-cols-2 gap-2 mt-4 text-xs">
              <div className="flex justify-between">
                <span className="text-gray-500">排名速度</span>
                <span className="font-mono">{score.rankingVelocity}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">玩法适配</span>
                <span className="font-mono">{score.genreFit}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">社交热度</span>
                <span className="font-mono">{score.socialBuzz}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">跨平台</span>
                <span className="font-mono">{score.crossPlatform}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">评分质量</span>
                <span className="font-mono">{score.ratingQuality}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">竞争空白</span>
                <span className="font-mono">{score.competitionGap}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">广告活跃</span>
                <span className="font-mono">{score.adActivity}</span>
              </div>
            </div>
          </div>
        )}

        {/* Ranking History Chart */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold mb-4">排名走势</h3>
          {rankingHistory.length > 0 ? (
            <RankingChart data={rankingHistory} />
          ) : (
            <p className="text-gray-400 text-sm py-8 text-center">
              暂无排名数据
            </p>
          )}
        </div>

        {/* Platform Listings */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold mb-4">平台信息</h3>
          <div className="space-y-3">
            {game.platformListings.map((pl) => (
              <div
                key={pl.id}
                className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded"
              >
                <div>
                  <span
                    className="text-sm font-medium"
                    style={{
                      color: PLATFORM_COLORS[pl.platform] || "#333",
                    }}
                  >
                    {PLATFORM_LABELS[pl.platform] || pl.platform}
                  </span>
                  <p className="text-xs text-gray-400">{pl.name}</p>
                </div>
                <div className="text-right">
                  {pl.rating && (
                    <p className="text-sm">
                      {Number(pl.rating).toFixed(1)} ({formatNumber(pl.ratingCount)})
                    </p>
                  )}
                  {pl.downloadEst && (
                    <p className="text-xs text-gray-400">
                      {formatNumber(Number(pl.downloadEst))} downloads
                    </p>
                  )}
                  {pl.platformUrl && (
                    <a
                      href={pl.platformUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-500 hover:underline"
                    >
                      查看 &rarr;
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Social Signals */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold mb-4">社交媒体信号</h3>
          {game.socialSignals.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">暂无数据</p>
          ) : (
            <div className="space-y-2">
              {game.socialSignals.slice(0, 7).map((ss) => (
                <div
                  key={ss.id}
                  className="flex items-center justify-between py-1.5 text-sm"
                >
                  <span className="text-gray-500 capitalize">
                    {ss.platform}
                  </span>
                  <div className="flex gap-4 text-xs">
                    <span>视频: {formatNumber(ss.videoCount)}</span>
                    <span>播放: {formatNumber(Number(ss.viewCount))}</span>
                    <span>点赞: {formatNumber(Number(ss.likeCount))}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Ad Intelligence */}
        <div className="bg-white rounded-lg shadow p-4 col-span-2">
          <h3 className="font-semibold mb-4">广告投放情报</h3>
          {game.adIntelligence.length === 0 ? (
            <p className="text-gray-400 text-sm py-4 text-center">暂无数据</p>
          ) : (
            <div className="grid grid-cols-2 gap-4">
              {game.adIntelligence.map((ad) => (
                <div
                  key={ad.id}
                  className="py-2 px-3 bg-gray-50 rounded text-sm"
                >
                  <div className="flex justify-between">
                    <span className="font-medium">{ad.source}</span>
                    <span className="text-xs text-gray-400">
                      {ad.signalDate.toISOString().split("T")[0]}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500 space-y-1">
                    <p>活跃素材: {ad.activeCreatives}</p>
                    <p>预估花费: {ad.estimatedSpend || "未知"}</p>
                    {ad.markets.length > 0 && (
                      <p>投放地区: {ad.markets.join(", ")}</p>
                    )}
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
