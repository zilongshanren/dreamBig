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
  wechat_intelligence: { label: "微信 IAA 智库简报", emoji: "🧠" },
};

// ================================================================
// WeChat intelligence payload types (aligned with Pydantic schema)
// ================================================================
type WechatIntelSignalGame = {
  game_id: number;
  name: string;
  signal_strength: string;
  iaa_angle: string;
  evidence_refs?: string[];
};

type WechatIntelOpportunity = {
  opportunity: string;
  reasoning: string;
  why_now: string;
  risk_factors?: string[];
  confidence: number;
};

type WechatIntelRedFlag = {
  pattern: string;
  affected_games?: number[];
  implication: string;
};

type WechatIntelProjectRec = {
  title: string;
  genre: string;
  core_mechanic: string;
  inspirations: number[];
  iaa_placement_hint: string;
  rationale: string;
  target_audience: string;
  estimated_dev_weeks: number;
  confidence: number;
};

type WechatIntelBlindSpot = {
  signal: string;
  reason: string;
  impact: string;
};

type WechatIntelPayload = {
  headline: string;
  market_pulse: "hot" | "warming" | "stable" | "cooling" | "cold";
  market_snapshot: string;
  top_signal_games?: WechatIntelSignalGame[];
  market_opportunities?: WechatIntelOpportunity[];
  red_flags?: WechatIntelRedFlag[];
  project_recommendations?: WechatIntelProjectRec[];
  data_blind_spots?: WechatIntelBlindSpot[];
  overall_confidence: number;
};

const PULSE_META: Record<
  string,
  { label: string; bg: string; text: string }
> = {
  hot: { label: "🔥 过热", bg: "bg-red-100", text: "text-red-700" },
  warming: { label: "⬆️ 升温", bg: "bg-orange-100", text: "text-orange-700" },
  stable: { label: "⏸ 稳态", bg: "bg-blue-100", text: "text-blue-700" },
  cooling: { label: "⬇️ 降温", bg: "bg-cyan-100", text: "text-cyan-700" },
  cold: { label: "🧊 清冷", bg: "bg-gray-100", text: "text-gray-600" },
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
// WeChat intelligence full view
// ================================================================
function WechatIntelView({ payload }: { payload: WechatIntelPayload }) {
  const pulse = PULSE_META[payload.market_pulse] ?? PULSE_META.stable;
  const confidence = Math.round((payload.overall_confidence ?? 0) * 100);
  const signalGames = payload.top_signal_games ?? [];
  const opportunities = payload.market_opportunities ?? [];
  const redFlags = payload.red_flags ?? [];
  const recs = payload.project_recommendations ?? [];
  const blindSpots = payload.data_blind_spots ?? [];

  return (
    <div className="space-y-6">
      {/* Header bar: headline + pulse + confidence */}
      <div className="bg-gradient-to-br from-indigo-50 via-white to-purple-50 rounded-lg shadow-lg border border-indigo-100 p-6">
        <div className="flex items-center gap-3 flex-wrap mb-3">
          <span
            className={`text-xs px-2.5 py-0.5 rounded-full font-medium ${pulse.bg} ${pulse.text}`}
          >
            {pulse.label}
          </span>
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <span>整体置信度</span>
            <div className="w-24 h-1.5 bg-gray-200 rounded overflow-hidden">
              <div
                className="h-full bg-indigo-500"
                style={{ width: `${confidence}%` }}
              />
            </div>
            <span className="font-mono">{confidence}%</span>
          </div>
        </div>
        <p className="text-lg font-semibold text-gray-900 leading-snug">
          {payload.headline}
        </p>
        <div className="mt-4 pt-4 border-t border-indigo-100">
          <p className="text-xs font-semibold text-indigo-700 uppercase tracking-wide mb-1.5">
            当日市场快照
          </p>
          <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">
            {payload.market_snapshot}
          </p>
        </div>
      </div>

      {/* Project recommendations — the core decision output */}
      {recs.length > 0 && (
        <div className="bg-white rounded-lg shadow p-5">
          <h3 className="font-semibold text-sm mb-4 flex items-center gap-2">
            <span>🎯</span>立项建议
            <span className="text-xs font-normal text-gray-400">
              · {recs.length} 条
            </span>
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {recs.map((rec, i) => (
              <div
                key={i}
                className="rounded-lg p-4 border border-indigo-100 bg-indigo-50/30"
              >
                <div className="flex items-start justify-between mb-2 gap-2">
                  <h4 className="font-semibold text-sm text-gray-800 leading-snug">
                    {rec.title}
                  </h4>
                  <span className="text-xs text-indigo-600 font-mono whitespace-nowrap">
                    {Math.round((rec.confidence ?? 0) * 100)}%
                  </span>
                </div>
                <dl className="space-y-1 text-xs text-gray-600">
                  <div className="flex gap-1">
                    <dt className="text-gray-400 shrink-0">品类</dt>
                    <dd>{rec.genre}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt className="text-gray-400 shrink-0">机制</dt>
                    <dd>{rec.core_mechanic}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt className="text-gray-400 shrink-0">变现</dt>
                    <dd>{rec.iaa_placement_hint}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt className="text-gray-400 shrink-0">受众</dt>
                    <dd>{rec.target_audience}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt className="text-gray-400 shrink-0">估时</dt>
                    <dd>{rec.estimated_dev_weeks} 周</dd>
                  </div>
                </dl>
                <p className="text-xs text-gray-700 mt-2 pt-2 border-t border-indigo-100 leading-relaxed">
                  {rec.rationale}
                </p>
                {rec.inspirations.length > 0 && (
                  <div className="mt-2 flex items-center gap-1 flex-wrap">
                    <span className="text-xs text-gray-400">标的:</span>
                    {rec.inspirations.slice(0, 5).map((gid) => (
                      <Link
                        key={gid}
                        href={`/games/${gid}`}
                        className="text-xs text-indigo-600 hover:underline"
                      >
                        #{gid}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Opportunities + red flags */}
      {(opportunities.length > 0 || redFlags.length > 0) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {opportunities.length > 0 && (
            <div className="bg-white rounded-lg shadow p-5 border-l-4 border-green-400">
              <h3 className="font-semibold text-sm mb-3 text-green-700 flex items-center gap-2">
                <span>🌱</span>市场机会 · {opportunities.length}
              </h3>
              <ul className="space-y-3">
                {opportunities.map((o, i) => (
                  <li key={i} className="text-sm">
                    <div className="flex items-baseline justify-between gap-2">
                      <p className="font-medium text-gray-800">
                        • {o.opportunity}
                      </p>
                      <span className="text-xs font-mono text-green-600 shrink-0">
                        {Math.round((o.confidence ?? 0) * 100)}%
                      </span>
                    </div>
                    <p className="text-xs text-gray-600 mt-1 pl-3 leading-relaxed">
                      {o.reasoning}
                    </p>
                    <p className="text-xs text-green-700 mt-1 pl-3 italic">
                      何时:{o.why_now}
                    </p>
                    {o.risk_factors && o.risk_factors.length > 0 && (
                      <ul className="mt-1 pl-3 space-y-0.5">
                        {o.risk_factors.map((r, ri) => (
                          <li
                            key={ri}
                            className="text-[11px] text-gray-500"
                          >
                            风险:{r}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {redFlags.length > 0 && (
            <div className="bg-white rounded-lg shadow p-5 border-l-4 border-red-400">
              <h3 className="font-semibold text-sm mb-3 text-red-700 flex items-center gap-2">
                <span>⚠</span>红灯预警 · {redFlags.length}
              </h3>
              <ul className="space-y-3">
                {redFlags.map((f, i) => (
                  <li key={i} className="text-sm">
                    <p className="font-medium text-gray-800">
                      ⚠ {f.pattern}
                    </p>
                    <p className="text-xs text-gray-600 mt-1 pl-3 leading-relaxed">
                      {f.implication}
                    </p>
                    {f.affected_games && f.affected_games.length > 0 && (
                      <p className="text-xs text-gray-400 mt-1 pl-3">
                        涉及:{" "}
                        {f.affected_games.slice(0, 6).map((gid, idx) => (
                          <span key={gid}>
                            <Link
                              href={`/games/${gid}`}
                              className="text-red-600 hover:underline"
                            >
                              #{gid}
                            </Link>
                            {idx < Math.min(5, f.affected_games!.length - 1)
                              ? ", "
                              : ""}
                          </span>
                        ))}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Top signal games */}
      {signalGames.length > 0 && (
        <div className="bg-white rounded-lg shadow p-5">
          <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
            <span>✨</span>信号最强游戏 · {signalGames.length}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {signalGames.map((g) => (
              <Link
                key={g.game_id}
                href={`/games/${g.game_id}`}
                className="rounded border border-gray-100 p-3 hover:shadow-md transition-shadow"
              >
                <p className="font-semibold text-sm text-gray-800">
                  {g.name}
                </p>
                <p className="text-xs text-gray-600 mt-1 leading-relaxed">
                  {g.signal_strength}
                </p>
                <p className="text-xs text-indigo-600 mt-1 italic">
                  {g.iaa_angle}
                </p>
                {g.evidence_refs && g.evidence_refs.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {g.evidence_refs.slice(0, 5).map((ref, i) => (
                      <span
                        key={i}
                        className="text-[10px] bg-gray-50 text-gray-500 px-1.5 py-0.5 rounded font-mono"
                      >
                        {ref}
                      </span>
                    ))}
                  </div>
                )}
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Data blind spots — honest disclosure */}
      {blindSpots.length > 0 && (
        <div className="bg-gray-50 rounded-lg border border-gray-200 p-5">
          <h3 className="font-semibold text-sm mb-3 text-gray-600 flex items-center gap-2">
            <span>⊘</span>数据盲区 · 本次报告未使用的信号 · {blindSpots.length}
          </h3>
          <p className="text-xs text-gray-500 italic mb-3">
            这份简报只基于本次输入信号作出结论。下列维度因数据缺失 /
            样本过少 / 未接入而未参与判断——读者应把这些作为置信度上限的参考。
          </p>
          <ul className="space-y-2">
            {blindSpots.map((b, i) => (
              <li
                key={i}
                className="text-xs bg-white rounded p-3 border border-gray-100"
              >
                <p className="font-medium text-gray-700">⊘ {b.signal}</p>
                <p className="text-gray-500 mt-1 pl-3">原因:{b.reason}</p>
                <p className="text-gray-500 mt-0.5 pl-3">影响:{b.impact}</p>
              </li>
            ))}
          </ul>
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
      {(() => {
        const rawPayload = report.payload as unknown;
        const parsedPayload: unknown =
          typeof rawPayload === "string"
            ? (() => {
                try {
                  return JSON.parse(rawPayload);
                } catch {
                  return rawPayload;
                }
              })()
            : rawPayload;

        if (report.reportType === "weekly_genre") {
          return (
            <WeeklyGenreView
              payload={parsedPayload as WeeklyGenrePayload}
            />
          );
        }
        if (report.reportType === "wechat_intelligence") {
          return (
            <WechatIntelView
              payload={parsedPayload as WechatIntelPayload}
            />
          );
        }
        return (
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="font-semibold text-sm mb-3 text-gray-600">
              原始载荷（待适配专属视图）
            </h3>
            <pre className="text-xs bg-gray-50 rounded p-3 overflow-x-auto text-gray-800">
              {JSON.stringify(parsedPayload, null, 2)}
            </pre>
          </div>
        );
      })()}

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
