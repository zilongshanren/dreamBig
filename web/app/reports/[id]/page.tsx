import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

// ================================================================
// Payload types
// ================================================================
type GenreInsight = {
  genre_key: string;
  label_zh: string;
  trend: string;
  hot_games_count?: number;
  momentum: number;
  key_movement: string;
  top_game_names: string[];
};

type WeeklyGenrePayload = {
  week: string;
  headline: string;
  summary: string;
  top_rising: GenreInsight[];
  top_declining: GenreInsight[];
  best_iaa_opportunity: GenreInsight;
  emerging_themes: string[];
  recommendations: string[];
  overall_confidence: number;
};

const REPORT_TYPE_META: Record<string, { label: string; emoji: string }> = {
  weekly_genre: { label: "赛道周报", emoji: "📊" },
  project_advice_batch: { label: "立项批量建议", emoji: "🎯" },
  experiment_summary: { label: "实验总结", emoji: "🧪" },
};

async function getReport(id: number) {
  try {
    return await prisma.generatedReport.findUnique({ where: { id } });
  } catch {
    return null;
  }
}

function formatDate(d: Date): string {
  try {
    return new Date(d).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(d);
  }
}

function formatCost(v: unknown): string {
  if (v == null) return "-";
  const n = typeof v === "number" ? v : parseFloat(String(v));
  if (!Number.isFinite(n)) return "-";
  return `$${n.toFixed(4)}`;
}

function TrendBadge({ trend }: { trend: string }) {
  const styles: Record<string, { bg: string; text: string; label: string }> = {
    rising: { bg: "bg-green-100", text: "text-green-700", label: "上升" },
    stable: { bg: "bg-gray-100", text: "text-gray-600", label: "稳定" },
    declining: { bg: "bg-red-100", text: "text-red-700", label: "下行" },
  };
  const s = styles[trend] ?? styles.stable;
  return (
    <span
      className={`text-xs ${s.bg} ${s.text} px-2 py-0.5 rounded font-medium`}
    >
      {s.label}
    </span>
  );
}

function MomentumValue({ v }: { v: number }) {
  const rounded = Math.round(v * 10) / 10;
  const sign = v > 0 ? "+" : "";
  const color =
    v > 0.5 ? "text-green-600" : v < -0.5 ? "text-red-500" : "text-gray-400";
  return (
    <span className={`font-mono text-xs ${color}`}>
      {sign}
      {rounded.toFixed(1)}
    </span>
  );
}

function InsightCard({
  insight,
  accent,
}: {
  insight: GenreInsight;
  accent: "green" | "red" | "blue";
}) {
  const accentStyles = {
    green: "border-green-200 bg-green-50/40",
    red: "border-red-200 bg-red-50/40",
    blue: "border-blue-200 bg-blue-50/40",
  };
  return (
    <div
      className={`rounded-lg border ${accentStyles[accent]} p-3 space-y-2`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-semibold text-sm truncate">
            {insight.label_zh}
          </span>
          <TrendBadge trend={insight.trend} />
        </div>
        <MomentumValue v={insight.momentum} />
      </div>
      <p className="text-xs text-gray-700 leading-relaxed">
        {insight.key_movement}
      </p>
      {insight.top_game_names.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {insight.top_game_names.map((n, i) => (
            <span
              key={i}
              className="text-xs bg-white border border-gray-200 text-gray-600 px-1.5 py-0.5 rounded truncate max-w-[140px]"
            >
              {n}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ================================================================
// Weekly genre report view
// ================================================================
function WeeklyGenreView({
  payload,
}: {
  payload: WeeklyGenrePayload;
}) {
  const confidence = Math.round((payload.overall_confidence ?? 0) * 100);

  return (
    <div className="space-y-6">
      {/* Summary card */}
      <div className="bg-white rounded-lg shadow p-5">
        <div className="flex items-center justify-between gap-3 mb-2">
          <span className="text-xs text-gray-500">执行摘要</span>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-gray-500">置信度</span>
            <div className="w-20 h-1.5 bg-gray-200 rounded overflow-hidden">
              <div
                className="h-full bg-blue-500"
                style={{ width: `${confidence}%` }}
              />
            </div>
            <span className="font-mono text-gray-600">{confidence}%</span>
          </div>
        </div>
        <p className="text-sm text-gray-800 leading-relaxed">
          {payload.summary}
        </p>
      </div>

      {/* Rising / Declining grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
            <span className="text-green-600">▲</span>
            升温赛道 Top {payload.top_rising.length}
          </h3>
          {payload.top_rising.length === 0 ? (
            <p className="text-xs text-gray-400">本周无明显升温赛道</p>
          ) : (
            <div className="space-y-2">
              {payload.top_rising.map((ins) => (
                <InsightCard
                  key={ins.genre_key}
                  insight={ins}
                  accent="green"
                />
              ))}
            </div>
          )}
        </div>

        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
            <span className="text-red-500">▼</span>
            降温赛道 Top {payload.top_declining.length}
          </h3>
          {payload.top_declining.length === 0 ? (
            <p className="text-xs text-gray-400">本周无明显降温赛道</p>
          ) : (
            <div className="space-y-2">
              {payload.top_declining.map((ins) => (
                <InsightCard
                  key={ins.genre_key}
                  insight={ins}
                  accent="red"
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Best IAA opportunity spotlight */}
      {payload.best_iaa_opportunity && (
        <div className="bg-white rounded-lg shadow p-5 border-l-4 border-blue-500">
          <div className="flex items-baseline gap-2 mb-3">
            <span className="text-xs text-blue-600 font-semibold uppercase tracking-wide">
              本周最佳 IAA 机会
            </span>
            <span className="font-bold text-lg">
              {payload.best_iaa_opportunity.label_zh}
            </span>
            <TrendBadge trend={payload.best_iaa_opportunity.trend} />
          </div>
          <p className="text-sm text-gray-800 leading-relaxed mb-3">
            {payload.best_iaa_opportunity.key_movement}
          </p>
          {payload.best_iaa_opportunity.top_game_names.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              <span className="text-xs text-gray-500 self-center">
                头部游戏:
              </span>
              {payload.best_iaa_opportunity.top_game_names.map((n, i) => (
                <span
                  key={i}
                  className="text-xs bg-blue-50 border border-blue-200 text-blue-700 px-2 py-0.5 rounded"
                >
                  {n}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Emerging themes */}
      {payload.emerging_themes.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3">跨赛道主题</h3>
          <div className="flex flex-wrap gap-2">
            {payload.emerging_themes.map((theme, i) => (
              <span
                key={i}
                className="text-xs bg-purple-100 text-purple-700 px-2.5 py-1 rounded-full"
              >
                {theme}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Recommendations */}
      {payload.recommendations.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3">行动建议</h3>
          <ol className="space-y-2">
            {payload.recommendations.map((rec, i) => (
              <li key={i} className="flex gap-3 text-sm text-gray-800">
                <span className="shrink-0 w-5 h-5 bg-blue-600 text-white text-xs font-bold rounded-full flex items-center justify-center">
                  {i + 1}
                </span>
                <span className="leading-relaxed">{rec}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

// ================================================================
// Page component
// ================================================================
export default async function ReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const parsed = parseInt(id, 10);
  if (!Number.isFinite(parsed)) return notFound();

  const report = await getReport(parsed);
  if (!report) return notFound();

  const meta = REPORT_TYPE_META[report.reportType] ?? {
    label: report.reportType,
    emoji: "📄",
  };

  return (
    <div>
      {/* Breadcrumb */}
      <Link
        href="/reports"
        className="text-sm text-gray-400 hover:text-gray-600 mb-4 inline-block"
      >
        &larr; 返回报告列表
      </Link>

      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
            {meta.emoji} {meta.label}
          </span>
          <span className="text-xs font-mono bg-gray-50 text-gray-500 px-2 py-0.5 rounded">
            {report.scope}
          </span>
        </div>
        <h2 className="text-2xl font-bold leading-snug">{report.title}</h2>
        <p className="text-xs text-gray-400 mt-2">
          生成于 {formatDate(report.generatedAt)}
        </p>
      </div>

      {/* Body by reportType */}
      {report.reportType === "weekly_genre" ? (
        <WeeklyGenreView
          payload={report.payload as unknown as WeeklyGenrePayload}
        />
      ) : (
        // TODO: dedicated views for project_advice_batch & experiment_summary
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-semibold text-sm mb-3 text-gray-600">
            原始载荷（待适配专属视图）
          </h3>
          <pre className="text-xs bg-gray-50 rounded p-3 overflow-x-auto text-gray-800">
            {JSON.stringify(report.payload, null, 2)}
          </pre>
        </div>
      )}

      {/* Metadata footer */}
      <div className="mt-8 pt-4 border-t border-gray-200">
        <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div>
            <dt className="text-gray-400">模型</dt>
            <dd className="font-mono text-gray-700 mt-0.5 truncate">
              {report.modelUsed ?? "-"}
            </dd>
          </div>
          <div>
            <dt className="text-gray-400">Token 用量</dt>
            <dd className="font-mono text-gray-700 mt-0.5">
              {report.tokensUsed ?? "-"}
            </dd>
          </div>
          <div>
            <dt className="text-gray-400">成本</dt>
            <dd className="font-mono text-gray-700 mt-0.5">
              {formatCost(report.costUsd)}
            </dd>
          </div>
          <div>
            <dt className="text-gray-400">证据数</dt>
            <dd className="font-mono text-gray-700 mt-0.5">
              {report.evidenceCount}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
