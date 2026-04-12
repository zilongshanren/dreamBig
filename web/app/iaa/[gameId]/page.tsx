import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";
import { GenerateReportButton } from "./generate-button";

export const dynamic = "force-dynamic";

// Grade color mapping
const GRADE_COLORS: Record<
  string,
  { bg: string; text: string; label: string }
> = {
  S: { bg: "bg-green-500", text: "text-white", label: "极适合" },
  A: { bg: "bg-lime-500", text: "text-white", label: "适合" },
  B: { bg: "bg-yellow-500", text: "text-white", label: "可尝试" },
  C: { bg: "bg-orange-500", text: "text-white", label: "谨慎" },
  D: { bg: "bg-red-500", text: "text-white", label: "不建议" },
};

// Inline GameReport payload type
type LoopBlock = {
  description?: string;
  evidence_refs?: string[];
};

type IaaAdvice = {
  overall_grade?: string;
  suitable_placements?: string[];
  forbidden_placements?: string[];
  risks?: string[];
  ab_test_order?: string[];
  confidence?: number;
};

type GameReportPayload = {
  positioning?: string;
  core_loop?: LoopBlock;
  meta_loop?: LoopBlock;
  pleasure_points?: string[];
  replay_drivers?: string[];
  spread_points?: string[];
  iaa_advice?: IaaAdvice;
  overall_confidence?: number;
};

async function getGameWithReport(id: number) {
  try {
    const game = await prisma.game.findUnique({
      where: { id },
      include: {
        gameReport: true,
        potentialScores: { orderBy: { scoredAt: "desc" }, take: 1 },
      },
    });
    return game;
  } catch {
    return null;
  }
}

/**
 * Find the latest internal report_generation job for a given game so
 * the "no report yet" state can show the real last-attempt status
 * (pending / running / success / failed) with any error reason from
 * scrape_jobs.error_message, instead of a silent black hole.
 */
async function getLatestReportJob(gameId: number) {
  try {
    const jobs = await prisma.scrapeJob.findMany({
      where: {
        platform: "internal",
        jobType: "report_generation",
        errorMessage: { contains: `"gameId":${gameId}` },
      },
      orderBy: { id: "desc" },
      take: 1,
    });
    return jobs[0] ?? null;
  } catch {
    return null;
  }
}

function parseJobPayload(
  raw: string | null
): { gameId?: number; reason?: string; url?: string } | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return { reason: raw };
  }
}

export default async function IaaDetailPage({
  params,
}: {
  params: Promise<{ gameId: string }>;
}) {
  const { gameId } = await params;
  const id = parseInt(gameId, 10);
  if (!Number.isFinite(id)) return notFound();

  const game = await getGameWithReport(id);
  if (!game) return notFound();

  const report = game.gameReport;
  const score = game.potentialScores[0];

  // No report yet → show the real last-job status instead of a silent
  // empty state (previously the UI would just keep saying "queued" even
  // when poll_internal_jobs had already failed the job).
  if (!report) {
    const lastJob = await getLatestReportJob(id);
    const jobPayload = parseJobPayload(lastJob?.errorMessage ?? null);
    const jobReason = jobPayload?.reason ?? null;

    // Check whether game 4589 has the minimum evidence we need:
    // review topic summaries. That's the #1 reason generate_for_game
    // returns None (line 242 of report_generator.py).
    let topicCount = 0;
    let reviewCount = 0;
    try {
      topicCount = await prisma.reviewTopicSummary.count({
        where: { gameId: id },
      });
      reviewCount = await prisma.review.count({
        where: { platformListing: { gameId: id } },
      });
    } catch {
      // best-effort
    }

    return (
      <div>
        {/* Breadcrumb */}
        <Link
          href="/iaa"
          className="text-sm text-gray-400 hover:text-gray-600 mb-4 inline-block"
        >
          &larr; 返回 IAA 候选列表
        </Link>
        <div className="flex items-start gap-4 mb-6">
          {game.thumbnailUrl && (
            <img
              src={game.thumbnailUrl}
              alt={game.nameZh || game.nameEn || ""}
              className="w-16 h-16 rounded-xl shadow object-cover shrink-0"
            />
          )}
          <div>
            <h2 className="text-2xl font-bold">
              {game.nameZh || game.nameEn || "Unknown"}
            </h2>
            {game.nameEn && game.nameZh && (
              <p className="text-gray-500">{game.nameEn}</p>
            )}
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-8 space-y-5">
          <div className="text-center">
            <p className="text-gray-600 text-base mb-1">
              此游戏尚未生成 IAA 分析报告
            </p>
            <p className="text-gray-400 text-sm">
              点击下方按钮立即生成，任务将进入后台队列
            </p>
          </div>

          {/* Evidence pre-check — tells the user BEFORE they click whether
              the generator will have enough data to produce a real report. */}
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
            <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-2">
              数据状态（生成前检查）
            </p>
            <ul className="space-y-1 text-sm">
              <li className="flex items-center gap-2">
                <span
                  className={
                    reviewCount > 0 ? "text-green-600" : "text-red-500"
                  }
                >
                  {reviewCount > 0 ? "✓" : "✗"}
                </span>
                <span className="text-gray-700">
                  评论数：<span className="font-mono">{reviewCount}</span>
                </span>
              </li>
              <li className="flex items-center gap-2">
                <span
                  className={
                    topicCount > 0 ? "text-green-600" : "text-red-500"
                  }
                >
                  {topicCount > 0 ? "✓" : "✗"}
                </span>
                <span className="text-gray-700">
                  已聚类话题：<span className="font-mono">{topicCount}</span>
                  {topicCount === 0 && (
                    <span className="text-red-500 ml-2 text-xs">
                      （报告生成器需要至少 1 条）
                    </span>
                  )}
                </span>
              </li>
            </ul>
            {topicCount === 0 && (
              <p className="mt-3 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                ⚠ 这个游戏还没有任何评论话题聚类。即使生成任务进入队列，
                Opus 也会因缺乏证据而跳过（返回 None），报告不会落库。
                请先运行完整 pipeline：
                <code className="block mt-1 bg-white px-2 py-1 rounded font-mono text-[11px]">
                  scrape_reviews → sentiment_classification → topic_extraction → topic_clustering
                </code>
                补齐证据后再点"立即生成"。
              </p>
            )}
          </div>

          {/* Last attempt status, if any */}
          {lastJob && (
            <div
              className={`rounded-lg p-4 border text-sm ${
                lastJob.status === "failed"
                  ? "bg-red-50 border-red-200"
                  : lastJob.status === "running"
                    ? "bg-blue-50 border-blue-200"
                    : "bg-gray-50 border-gray-200"
              }`}
            >
              <p className="text-xs font-semibold uppercase tracking-wide mb-1 text-gray-600">
                最近一次生成任务
              </p>
              <p className="text-xs text-gray-500">
                状态：
                <span
                  className={`font-mono ml-1 ${
                    lastJob.status === "failed"
                      ? "text-red-700"
                      : lastJob.status === "success"
                        ? "text-green-700"
                        : "text-blue-700"
                  }`}
                >
                  {lastJob.status}
                </span>
                {lastJob.finishedAt && (
                  <span className="ml-2 text-gray-400">
                    · {lastJob.finishedAt.toLocaleString("zh-CN")}
                  </span>
                )}
              </p>
              {jobReason && (
                <p className="text-xs text-gray-600 mt-2 leading-relaxed">
                  原因：{jobReason}
                </p>
              )}
            </div>
          )}

          <div className="flex justify-center">
            <GenerateReportButton gameId={game.id} />
          </div>
        </div>
      </div>
    );
  }

  const payload = report.payload as unknown as GameReportPayload;
  const advice = payload.iaa_advice ?? {};
  const grade = game.iaaGrade ?? advice.overall_grade ?? "-";
  const gradeStyle = GRADE_COLORS[grade] ?? {
    bg: "bg-gray-300",
    text: "text-white",
    label: "-",
  };
  const confidence =
    advice.confidence ?? payload.overall_confidence ?? Number(report.confidence);

  const suitablePlacements = advice.suitable_placements ?? [];
  const forbiddenPlacements = advice.forbidden_placements ?? [];
  const risks = advice.risks ?? [];
  const abTestOrder = advice.ab_test_order ?? [];

  const coreLoop = payload.core_loop ?? {};
  const metaLoop = payload.meta_loop ?? {};
  const pleasurePoints = payload.pleasure_points ?? [];
  const replayDrivers = payload.replay_drivers ?? [];
  const spreadPoints = payload.spread_points ?? [];

  return (
    <div>
      {/* Breadcrumb */}
      <Link
        href="/iaa"
        className="text-sm text-gray-400 hover:text-gray-600 mb-4 inline-block"
      >
        &larr; 返回 IAA 候选列表
      </Link>

      {/* Header */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="flex items-start gap-4">
          {game.thumbnailUrl && (
            <img
              src={game.thumbnailUrl}
              alt={game.nameZh || game.nameEn || ""}
              className="w-20 h-20 rounded-xl shadow object-cover shrink-0"
            />
          )}
          <div className="flex-1 min-w-0">
            <h2 className="text-2xl font-bold">
              {game.nameZh || game.nameEn || "Unknown"}
            </h2>
            {game.nameEn && game.nameZh && (
              <p className="text-gray-500">{game.nameEn}</p>
            )}
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              {game.genre && (
                <span className="text-xs bg-purple-100 text-purple-700 px-2 py-1 rounded">
                  {game.genre}
                </span>
              )}
              <span className="text-xs bg-orange-100 text-orange-700 px-2 py-1 rounded">
                IAA 适配: {game.iaaSuitability}
              </span>
              {score && (
                <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded">
                  潜力评分: {score.overallScore}
                </span>
              )}
              <Link
                href={`/games/${game.id}`}
                className="text-xs text-blue-500 hover:underline"
              >
                查看游戏详情 &rarr;
              </Link>
            </div>
          </div>
          <div className="text-center shrink-0">
            <div
              className={`${gradeStyle.bg} ${gradeStyle.text} w-20 h-20 rounded-xl flex items-center justify-center font-bold text-4xl shadow`}
            >
              {grade}
            </div>
            <p className="text-xs text-gray-500 mt-2">{gradeStyle.label}</p>
            <p className="text-xs text-gray-400 mt-1">
              置信度 {Math.round(confidence * 100)}%
            </p>
          </div>
        </div>
      </div>

      {/* Adaptation overall */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h3 className="font-semibold text-lg mb-3">适配总览</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-500">综合等级</p>
            <p className="text-2xl font-bold mt-1">
              {grade}{" "}
              <span className="text-sm font-normal text-gray-500">
                {gradeStyle.label}
              </span>
            </p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-500">IAA 建议置信度</p>
            <p className="text-2xl font-bold mt-1">
              {Math.round(confidence * 100)}
              <span className="text-sm font-normal text-gray-500">%</span>
            </p>
          </div>
          <div className="bg-gray-50 rounded p-3">
            <p className="text-xs text-gray-500">证据数量</p>
            <p className="text-2xl font-bold mt-1">
              {report.evidenceCount}
              <span className="text-sm font-normal text-gray-500"> 条</span>
            </p>
          </div>
        </div>
        {payload.positioning && (
          <blockquote className="border-l-4 border-blue-400 pl-4 py-2 bg-blue-50 rounded-r">
            <p className="text-sm text-gray-700 italic">
              {payload.positioning}
            </p>
          </blockquote>
        )}
      </div>

      {/* Breakdown section: 2x2 grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Suitable placements */}
        <div className="bg-white rounded-lg shadow p-4 border-l-4 border-green-500">
          <h3 className="font-semibold text-base mb-3 text-green-700">
            适合的广告位
          </h3>
          {suitablePlacements.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无建议</p>
          ) : (
            <ol className="space-y-2">
              {suitablePlacements.map((p, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="bg-green-100 text-green-700 font-bold rounded-full w-5 h-5 flex items-center justify-center shrink-0 text-xs">
                    {i + 1}
                  </span>
                  <span className="text-gray-700">{p}</span>
                </li>
              ))}
            </ol>
          )}
        </div>

        {/* Forbidden placements */}
        <div className="bg-white rounded-lg shadow p-4 border-l-4 border-red-500">
          <h3 className="font-semibold text-base mb-3 text-red-700">
            禁放位建议
          </h3>
          {forbiddenPlacements.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无禁放位建议</p>
          ) : (
            <ul className="space-y-2">
              {forbiddenPlacements.map((p, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="text-red-500 font-bold shrink-0">✕</span>
                  <span className="text-gray-700">{p}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Risks */}
        <div className="bg-white rounded-lg shadow p-4 border-l-4 border-amber-500">
          <h3 className="font-semibold text-base mb-3 text-amber-700">
            风险预警
          </h3>
          {risks.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无风险提示</p>
          ) : (
            <ul className="space-y-2">
              {risks.map((r, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="text-amber-500 font-bold shrink-0">!</span>
                  <span className="text-gray-700">{r}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* A/B test order */}
        <div className="bg-white rounded-lg shadow p-4 border-l-4 border-blue-500">
          <h3 className="font-semibold text-base mb-3 text-blue-700">
            A/B 测试顺序
          </h3>
          {abTestOrder.length === 0 ? (
            <p className="text-gray-400 text-sm">暂无测试顺序建议</p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {abTestOrder.map((step, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="bg-blue-100 text-blue-700 text-xs font-medium px-2 py-1 rounded">
                    {i + 1}. {step}
                  </span>
                  {i < abTestOrder.length - 1 && (
                    <span className="text-gray-400">&rarr;</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Gameplay loops section */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Core Loop */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
            <span className="bg-indigo-100 text-indigo-700 text-xs font-medium px-2 py-0.5 rounded">
              Core
            </span>
            核心循环
          </h3>
          {coreLoop.description ? (
            <>
              <p className="text-sm text-gray-700 mb-3">
                {coreLoop.description}
              </p>
              {coreLoop.evidence_refs && coreLoop.evidence_refs.length > 0 && (
                <EvidenceRefs gameId={game.id} refs={coreLoop.evidence_refs} />
              )}
            </>
          ) : (
            <p className="text-gray-400 text-sm">暂未识别核心循环</p>
          )}
        </div>

        {/* Meta Loop */}
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-base mb-3 flex items-center gap-2">
            <span className="bg-pink-100 text-pink-700 text-xs font-medium px-2 py-0.5 rounded">
              Meta
            </span>
            Meta 循环
          </h3>
          {metaLoop.description ? (
            <>
              <p className="text-sm text-gray-700 mb-3">
                {metaLoop.description}
              </p>
              {metaLoop.evidence_refs && metaLoop.evidence_refs.length > 0 && (
                <EvidenceRefs gameId={game.id} refs={metaLoop.evidence_refs} />
              )}
            </>
          ) : (
            <p className="text-gray-400 text-sm">暂未识别 Meta 循环</p>
          )}
        </div>
      </div>

      {/* Support signals */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3 text-gray-700">爽点</h3>
          {pleasurePoints.length === 0 ? (
            <p className="text-gray-400 text-xs">暂无</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {pleasurePoints.map((p, i) => (
                <span
                  key={i}
                  className="text-xs bg-yellow-100 text-yellow-800 px-2 py-1 rounded"
                >
                  {p}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3 text-gray-700">重玩驱动</h3>
          {replayDrivers.length === 0 ? (
            <p className="text-gray-400 text-xs">暂无</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {replayDrivers.map((p, i) => (
                <span
                  key={i}
                  className="text-xs bg-teal-100 text-teal-800 px-2 py-1 rounded"
                >
                  {p}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3 text-gray-700">传播卖点</h3>
          {spreadPoints.length === 0 ? (
            <p className="text-gray-400 text-xs">暂无</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {spreadPoints.map((p, i) => (
                <span
                  key={i}
                  className="text-xs bg-fuchsia-100 text-fuchsia-800 px-2 py-1 rounded"
                >
                  {p}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Metadata footer */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="font-semibold text-sm mb-3 text-gray-600">报告元信息</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
          <div>
            <p className="text-gray-400">Prompt 版本</p>
            <p className="font-mono text-gray-700">{report.promptVersion}</p>
          </div>
          <div>
            <p className="text-gray-400">模型</p>
            <p className="font-mono text-gray-700 truncate">
              {report.modelUsed}
            </p>
          </div>
          <div>
            <p className="text-gray-400">生成时间</p>
            <p className="font-mono text-gray-700">
              {report.generatedAt.toLocaleDateString("zh-CN")}
            </p>
          </div>
          <div>
            <p className="text-gray-400">Tokens</p>
            <p className="font-mono text-gray-700">
              {report.tokensUsed ?? "-"}
            </p>
          </div>
          <div>
            <p className="text-gray-400">成本 (USD)</p>
            <p className="font-mono text-gray-700">
              {report.costUsd != null ? `$${Number(report.costUsd).toFixed(4)}` : "-"}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// Evidence refs component: link review:N refs to games detail page
function EvidenceRefs({ gameId, refs }: { gameId: number; refs: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {refs.map((ref, i) => {
        const isReview = ref.startsWith("review:");
        return (
          <Link
            key={i}
            href={
              isReview
                ? `/games/${gameId}?highlight=${encodeURIComponent(ref)}`
                : `/games/${gameId}`
            }
            className="text-xs bg-gray-100 hover:bg-gray-200 text-gray-600 px-2 py-0.5 rounded font-mono"
          >
            {ref}
          </Link>
        );
      })}
    </div>
  );
}

// Generate button extracted to ./generate-button.tsx (client component).
// Kept the import at the top; nothing else needed here.
