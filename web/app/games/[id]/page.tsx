import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";
import { PLATFORM_LABELS, PLATFORM_COLORS, formatNumber } from "@/lib/utils";
import { ScoreRadar } from "@/components/charts/score-radar";
import { SimilarGamesCard } from "@/components/similar-games-card";
import { SocialContentCard } from "@/components/social-content-card";
import { VisualAnalysisCard } from "@/components/visual-analysis-card";
import { getCurrentWorkspaceId } from "@/lib/workspace";

export const dynamic = "force-dynamic";
import { RankingChart } from "@/components/charts/ranking-chart";

type GameReportPayload = {
  positioning?: string;
  core_loop?: { description: string; evidence_refs: string[] };
  meta_loop?: { description: string; evidence_refs: string[] };
  pleasure_points?: string[];
  replay_drivers?: string[];
  spread_points?: string[];
  iaa_advice?: {
    overall_grade: "S" | "A" | "B" | "C" | "D";
    suitable_placements: string[];
    forbidden_placements: string[];
    risks: string[];
    ab_test_order: string[];
    confidence: number;
  };
  overall_confidence?: number;
};

type GameplayIntelMetadata = {
  gameplay_intro?: string;
  features?: string[];
  art_style_primary?: string | null;
  art_style_secondary?: string[];
  art_style_evidence?: string[];
  screenshot_refs?: number[];
  confidence?: number;
  source_count?: number;
  model_used?: string;
  generated_at?: string;
  data_blind_spots?: string[];
};

type GameMetadata = {
  description?: string;
  description_source?: string;
  screenshots?: string[];
  gameplay_intel?: GameplayIntelMetadata;
};

type ReviewTopicSummaryView = {
  id: number;
  topic: string;
  sentiment: string;
  snippet: string;
  reviewCount: number;
  computedAt: Date;
};

type GamePageRelations = {
  gameReport?: {
    payload: unknown;
    confidence: unknown;
    generatedAt: Date;
  } | null;
  reviewTopicSummaries?: ReviewTopicSummaryView[];
};

function parseEvidenceRef(ref: string): { type: string; id?: string } {
  const [type, id] = ref.split(":");
  return { type: type || "unknown", id };
}

const IAA_GRADE_COLORS: Record<string, string> = {
  S: "bg-green-500 text-white",
  A: "bg-lime-500 text-white",
  B: "bg-yellow-500 text-white",
  C: "bg-orange-500 text-white",
  D: "bg-red-500 text-white",
};

async function getGame(id: number, workspaceId: string) {
  // Try full query first with new relations
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
        // Workspace-scoped tags only
        gameTags: { where: { workspaceId } },
        gameReport: true,
        reviewTopicSummaries: {
          orderBy: { computedAt: "desc" },
          take: 30,
        },
      },
    });
    return game;
  } catch {
    // Fallback: older schema without gameReport / reviewTopicSummaries
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
          gameTags: { where: { workspaceId } },
        },
      });
      return game;
    } catch {
      return null;
    }
  }
}

export default async function GameDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const workspaceId = await getCurrentWorkspaceId();
  const game = await getGame(parseInt(id), workspaceId);

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

  // Parse GameReport payload
  const gameWithRelations = game as typeof game & GamePageRelations;
  const gameReport = gameWithRelations.gameReport;
  const reportPayload = gameReport?.payload
    ? (gameReport.payload as unknown as GameReportPayload)
    : null;
  const reportConfidence = gameReport
    ? Number(gameReport.confidence)
    : reportPayload?.overall_confidence ?? 0;

  // Positioning: prefer payload, fallback to Game.positioning
  const positioning =
    reportPayload?.positioning || game.positioning || null;

  // Review topic summaries (keep most recent computed_at per topic, top 10 per sentiment)
  const topicSummaries = gameWithRelations.reviewTopicSummaries ?? [];

  // Dedupe: keep most recent computedAt per (topic, sentiment)
  const topicMap = new Map<
    string,
    {
      topic: string;
      sentiment: string;
      snippet: string;
      reviewCount: number;
      computedAt: Date;
    }
  >();
  for (const t of topicSummaries) {
    const key = `${t.topic}__${t.sentiment}`;
    const existing = topicMap.get(key);
    if (!existing || t.computedAt > existing.computedAt) {
      topicMap.set(key, t);
    }
  }
  const uniqueTopics = Array.from(topicMap.values());
  const positiveTopics = uniqueTopics
    .filter((t) => t.sentiment === "positive")
    .sort((a, b) => b.reviewCount - a.reviewCount)
    .slice(0, 10);
  const negativeTopics = uniqueTopics
    .filter((t) => t.sentiment === "negative")
    .sort((a, b) => b.reviewCount - a.reviewCount)
    .slice(0, 10);

  // IAA advice shortcuts
  const iaaAdvice = reportPayload?.iaa_advice;
  const iaaGrade = iaaAdvice?.overall_grade || game.iaaGrade || null;
  const iaaConfidence = iaaAdvice?.confidence ?? reportConfidence;

  // Combined pleasure / replay / spread
  const pleasurePoints =
    reportPayload?.pleasure_points && reportPayload.pleasure_points.length > 0
      ? reportPayload.pleasure_points
      : game.pleasurePoints || [];
  const replayDrivers =
    reportPayload?.replay_drivers && reportPayload.replay_drivers.length > 0
      ? reportPayload.replay_drivers
      : game.replayDrivers || [];
  const spreadPoints = reportPayload?.spread_points || [];

  const coreLoopDescription =
    reportPayload?.core_loop?.description || game.coreLoop || null;
  const coreLoopRefs = reportPayload?.core_loop?.evidence_refs || [];
  const metaLoopDescription =
    reportPayload?.meta_loop?.description || game.metaLoop || null;
  const metaLoopRefs = reportPayload?.meta_loop?.evidence_refs || [];

  const hasGameplayData =
    coreLoopDescription ||
    metaLoopDescription ||
    pleasurePoints.length > 0 ||
    replayDrivers.length > 0 ||
    spreadPoints.length > 0;

  return (
    <div>
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-start gap-4">
          {game.thumbnailUrl && (
            <img
              src={game.thumbnailUrl}
              alt={game.nameZh || game.nameEn || ""}
              className="w-16 h-16 rounded-xl shadow object-cover shrink-0"
            />
          )}
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

      {/* Gameplay intel fact sheet (metadata.gameplay_intel, LLM-synthesized) */}
      {(() => {
        const meta = game.metadata as GameMetadata | null | undefined;
        const intel = meta?.gameplay_intel;
        const isStub = intel?.model_used === "stub-no-llm";
        const fallbackIntro =
          typeof meta?.description === "string" ? meta.description.trim() : "";
        const useDescriptionFallback =
          !!fallbackIntro && (!intel?.gameplay_intro || isStub);
        const introText = useDescriptionFallback
          ? fallbackIntro
          : intel?.gameplay_intro || "";
        if (!introText) return null;
        const conf = Math.round((intel?.confidence ?? 0) * 100);
        const features = intel?.features || [];
        const primary = intel?.art_style_primary || null;
        const secondary = intel?.art_style_secondary || [];
        const evidence = intel?.art_style_evidence || [];
        const blindSpots = intel?.data_blind_spots || [];
        const introSource =
          meta?.description_source === "baidu_baike" ? "百度百科" : "平台详情";
        return (
          <section
            className={`mb-6 rounded-lg border p-5 space-y-4 ${
              useDescriptionFallback || isStub
                ? "bg-gray-50 border-gray-200"
                : "bg-gradient-to-br from-purple-50 to-pink-50 border-purple-100"
            }`}
          >
            <div className="flex items-center justify-between flex-wrap gap-2">
              <h3
                className={`font-semibold flex items-center gap-2 ${
                  useDescriptionFallback || isStub
                    ? "text-gray-600"
                    : "text-purple-900"
                }`}
              >
                <span>
                  {useDescriptionFallback ? "📝" : isStub ? "⏳" : "🎮"}
                </span>
                {useDescriptionFallback
                  ? "玩法速览 · 资料摘要"
                  : isStub
                    ? "玩法速览 · 等待数据"
                    : "玩法速览"}
                {!useDescriptionFallback && (
                  <span className="text-xs font-normal text-gray-500">
                    · 置信度 {conf}%
                  </span>
                )}
                {!useDescriptionFallback && intel?.source_count !== undefined && (
                  <span className="text-xs font-normal text-gray-400">
                    · {intel.source_count} 个数据源
                  </span>
                )}
                {useDescriptionFallback && (
                  <span className="text-xs font-normal text-gray-400">
                    · 来源 {introSource}
                  </span>
                )}
              </h3>
              {!useDescriptionFallback && intel?.model_used && (
                <span className="text-[10px] text-gray-400 font-mono">
                  {intel.model_used}
                </span>
              )}
            </div>
            <p className="text-sm text-gray-700 leading-relaxed">
              {introText}
            </p>
            {!useDescriptionFallback && features.length > 0 && (
              <div>
                <p className="text-[11px] font-semibold text-purple-700 uppercase tracking-wide mb-1.5">
                  玩法特色
                </p>
                <div className="flex gap-1.5 flex-wrap">
                  {features.map((f, i) => (
                    <span
                      key={i}
                      className="text-xs bg-white text-purple-700 px-2 py-1 rounded-full border border-purple-200"
                    >
                      {f}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {!useDescriptionFallback && (primary || secondary.length > 0) && (
              <div>
                <p className="text-[11px] font-semibold text-purple-700 uppercase tracking-wide mb-1.5">
                  美术风格
                </p>
                <div className="flex gap-1.5 flex-wrap items-center">
                  {primary && (
                    <span className="text-xs bg-purple-600 text-white px-2.5 py-1 rounded-full font-medium">
                      {primary}
                    </span>
                  )}
                  {secondary.map((s, i) => (
                    <span
                      key={i}
                      className="text-xs bg-white text-purple-600 px-2 py-1 rounded-full border border-purple-200"
                    >
                      {s}
                    </span>
                  ))}
                </div>
                {evidence.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {evidence.map((e, i) => (
                      <li
                        key={i}
                        className="text-[11px] text-gray-500 italic pl-3 border-l border-purple-200"
                      >
                        &ldquo;{e}&rdquo;
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {!useDescriptionFallback && blindSpots.length > 0 && (
              <div className="pt-3 border-t border-gray-200">
                <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                  数据盲区
                </p>
                <ul className="space-y-0.5">
                  {blindSpots.map((b, i) => (
                    <li
                      key={i}
                      className="text-[11px] text-gray-500 pl-3 border-l border-gray-300"
                    >
                      ⊘ {b}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        );
      })()}

      {/* Screenshots */}
      {(() => {
        const meta = game.metadata as GameMetadata | null | undefined;
        const screenshots = meta?.screenshots;
        if (!screenshots?.length) return null;
        const intel = meta?.gameplay_intel;
        const recommended = new Set<number>(intel?.screenshot_refs || []);
        return (
          <div className="mb-6">
            <h3 className="font-semibold mb-3">
              游戏截图
              {recommended.size > 0 && (
                <span className="ml-2 text-xs font-normal text-purple-600">
                  · 紫色标记 = AI 推荐的玩法画面
                </span>
              )}
            </h3>
            <div className="flex gap-3 overflow-x-auto pb-2">
              {screenshots.map((url, i) => (
                <img
                  key={i}
                  src={url}
                  alt={`Screenshot ${i + 1}`}
                  className={`h-48 rounded-lg object-cover shrink-0 ${
                    recommended.has(i)
                      ? "ring-2 ring-purple-500 shadow-md"
                      : "shadow"
                  }`}
                />
              ))}
            </div>
          </div>
        );
      })()}

      {/* Report generation notice (only when no report and no positioning) */}
      {!gameReport && !positioning && (
        <div className="mb-6 py-3 px-4 bg-gray-50 border border-dashed border-gray-300 rounded-lg text-sm text-gray-500">
          AI 战报生成中 · 数据积累后将自动生成定位、玩法拆解与 IAA 建议
        </div>
      )}

      {/* Section A: 一句话定位 */}
      {positioning ? (
        <div className="border-l-4 border-blue-500 pl-4 py-3 mb-6 bg-blue-50 rounded-r-lg">
          <p className="text-lg italic">&ldquo;{positioning}&rdquo;</p>
          <p className="text-xs text-gray-500 mt-1">
            AI 总结 · 置信度 {Math.round(reportConfidence * 100)}%
          </p>
        </div>
      ) : (
        <div className="mb-6 py-2 px-3 bg-gray-50 border border-gray-200 rounded text-xs text-gray-500">
          暂无 AI 定位，数据积累中
        </div>
      )}

      {/* Section B: 玩法机制拆解 */}
      {hasGameplayData && (
        <div className="bg-white rounded-lg shadow p-5 mb-6">
          <h3 className="font-semibold mb-4">玩法机制拆解</h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-5">
            {/* Core Loop */}
            <div>
              <h4 className="text-sm font-medium text-gray-700 mb-2">
                核心循环 Core Loop
              </h4>
              {coreLoopDescription ? (
                <>
                  <p className="text-sm text-gray-600 leading-relaxed mb-2">
                    {coreLoopDescription}
                  </p>
                  {coreLoopRefs.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {coreLoopRefs.map((ref, i) => {
                        const { type, id } = parseEvidenceRef(ref);
                        return (
                          <span
                            key={i}
                            className="text-xs bg-gray-200 px-1.5 py-0.5 rounded font-mono"
                            title={`${type}:${id ?? ""}`}
                          >
                            {ref}
                          </span>
                        );
                      })}
                    </div>
                  )}
                </>
              ) : (
                <p className="text-xs text-gray-400">暂无数据</p>
              )}
            </div>

            {/* Meta Loop */}
            <div>
              <h4 className="text-sm font-medium text-gray-700 mb-2">
                Meta 循环 Meta Loop
              </h4>
              {metaLoopDescription ? (
                <>
                  <p className="text-sm text-gray-600 leading-relaxed mb-2">
                    {metaLoopDescription}
                  </p>
                  {metaLoopRefs.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {metaLoopRefs.map((ref, i) => {
                        const { type, id } = parseEvidenceRef(ref);
                        return (
                          <span
                            key={i}
                            className="text-xs bg-gray-200 px-1.5 py-0.5 rounded font-mono"
                            title={`${type}:${id ?? ""}`}
                          >
                            {ref}
                          </span>
                        );
                      })}
                    </div>
                  )}
                </>
              ) : (
                <p className="text-xs text-gray-400">暂无数据</p>
              )}
            </div>
          </div>

          {/* Chip groups */}
          <div className="space-y-3 pt-4 border-t border-gray-100">
            {pleasurePoints.length > 0 && (
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1.5">
                  爽点 Pleasure Points
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {pleasurePoints.map((p, i) => (
                    <span
                      key={i}
                      className="text-xs bg-indigo-100 text-indigo-700 px-2.5 py-1 rounded-full"
                    >
                      {p}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {replayDrivers.length > 0 && (
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1.5">
                  重玩驱动 Replay Drivers
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {replayDrivers.map((r, i) => (
                    <span
                      key={i}
                      className="text-xs bg-purple-100 text-purple-700 px-2.5 py-1 rounded-full"
                    >
                      {r}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {spreadPoints.length > 0 && (
              <div>
                <p className="text-xs font-medium text-gray-500 mb-1.5">
                  传播卖点 Spread Points
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {spreadPoints.map((s, i) => (
                    <span
                      key={i}
                      className="text-xs bg-pink-100 text-pink-700 px-2.5 py-1 rounded-full"
                    >
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Section C: 口碑主题分析 */}
      {(positiveTopics.length > 0 || negativeTopics.length > 0) ? (
        <div className="bg-white rounded-lg shadow p-5 mb-6">
          <h3 className="font-semibold mb-4">口碑主题分析</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Positive */}
            <div>
              <h4 className="text-sm font-medium text-green-700 mb-3 flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-green-500"></span>
                好评主题
              </h4>
              {positiveTopics.length === 0 ? (
                <p className="text-xs text-gray-400">暂无数据</p>
              ) : (
                <div className="space-y-3">
                  {positiveTopics.map((t) => (
                    <div
                      key={`pos-${t.topic}-${t.computedAt.toISOString()}`}
                      className="border-l-2 border-green-200 pl-3"
                    >
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-sm font-medium text-gray-800">
                          {t.topic}
                        </span>
                        <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded">
                          {t.reviewCount} 条
                        </span>
                      </div>
                      <p className="text-xs text-gray-600 leading-relaxed">
                        &ldquo;{t.snippet}&rdquo;
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Negative */}
            <div>
              <h4 className="text-sm font-medium text-red-700 mb-3 flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-red-500"></span>
                差评主题
              </h4>
              {negativeTopics.length === 0 ? (
                <p className="text-xs text-gray-400">暂无数据</p>
              ) : (
                <div className="space-y-3">
                  {negativeTopics.map((t) => (
                    <div
                      key={`neg-${t.topic}-${t.computedAt.toISOString()}`}
                      className="border-l-2 border-red-200 pl-3"
                    >
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-sm font-medium text-gray-800">
                          {t.topic}
                        </span>
                        <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">
                          {t.reviewCount} 条
                        </span>
                      </div>
                      <p className="text-xs text-gray-600 leading-relaxed">
                        &ldquo;{t.snippet}&rdquo;
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : topicSummaries.length === 0 && !gameReport ? null : (
        <div className="bg-white rounded-lg shadow p-5 mb-6">
          <h3 className="font-semibold mb-2">口碑主题分析</h3>
          <p className="text-sm text-gray-400">等待评论数据积累</p>
        </div>
      )}

      {/* Section D: IAA 改造建议摘要 */}
      <div className="bg-white rounded-lg shadow p-5 mb-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="font-semibold">IAA 改造建议摘要</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              基于玩法拆解与评论分析的 IAA 适配洞察
            </p>
          </div>
          <Link
            href={`/iaa/${game.id}`}
            className="text-sm text-blue-600 hover:text-blue-800 hover:underline shrink-0"
          >
            查看完整 IAA 分析 &rarr;
          </Link>
        </div>

        {iaaAdvice || iaaGrade ? (
          <div className="flex items-start gap-6">
            {/* Grade badge */}
            <div className="shrink-0 text-center">
              <div
                className={`w-20 h-20 rounded-xl flex items-center justify-center text-4xl font-bold ${
                  IAA_GRADE_COLORS[iaaGrade || ""] || "bg-gray-300 text-gray-700"
                }`}
              >
                {iaaGrade || "?"}
              </div>
              <p className="text-xs text-gray-500 mt-2">IAA 等级</p>
              {iaaConfidence > 0 && (
                <p className="text-xs text-gray-400 mt-0.5">
                  置信度 {Math.round(iaaConfidence * 100)}%
                </p>
              )}
            </div>

            {/* Details */}
            <div className="flex-1 space-y-3">
              {iaaAdvice?.suitable_placements &&
                iaaAdvice.suitable_placements.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-1">
                      推荐广告位
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {iaaAdvice.suitable_placements.slice(0, 2).map((p, i) => (
                        <span
                          key={i}
                          className="text-xs bg-green-50 text-green-700 border border-green-200 px-2 py-0.5 rounded"
                        >
                          {p}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

              {iaaAdvice?.risks && iaaAdvice.risks.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-gray-500 mb-1">
                    主要风险
                  </p>
                  <p className="text-xs text-red-700 bg-red-50 border border-red-100 px-2 py-1 rounded">
                    {iaaAdvice.risks[0]}
                  </p>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="py-4 text-center">
            <p className="text-sm text-gray-400 mb-3">尚未生成 IAA 报告</p>
            <Link
              href={`/iaa/${game.id}`}
              className="inline-block text-sm px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
            >
              触发生成 &rarr;
            </Link>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
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

        {/* Similar Games (pgvector) */}
        <SimilarGamesCard gameId={game.id} />

        {/* Social Content samples (depth) */}
        <SocialContentCard gameId={game.id} />

        {/* Visual Analysis (GPT-4o-mini vision) */}
        <VisualAnalysisCard gameId={game.id} />

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
