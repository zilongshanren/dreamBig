import Link from "next/link";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

type Recommendation = "pursue" | "monitor" | "pass";

type ProjectAdvice = {
  recommendation: Recommendation;
  reasoning: string;
  strengths: string[];
  weaknesses: string[];
  similar_shipped_projects: string[];
  resource_estimate_weeks: number;
  risk_factors: string[];
  confidence: number;
};

type GameReportPayload = {
  project_advice?: ProjectAdvice;
};

const SECTION_META: Record<
  Recommendation,
  { title: string; section: string; badge: string; text: string }
> = {
  pursue: {
    title: "推荐立项",
    section: "bg-green-50 border-green-200",
    badge: "bg-green-500",
    text: "text-white",
  },
  monitor: {
    title: "建议观察",
    section: "bg-yellow-50 border-yellow-200",
    badge: "bg-yellow-500",
    text: "text-white",
  },
  pass: {
    title: "建议放弃",
    section: "bg-gray-50 border-gray-200",
    badge: "bg-gray-500",
    text: "text-white",
  },
};

const GRADE_COLORS: Record<string, string> = {
  S: "bg-green-500",
  A: "bg-lime-500",
  B: "bg-yellow-500",
  C: "bg-orange-500",
  D: "bg-red-500",
};

const ORDER: Recommendation[] = ["pursue", "monitor", "pass"];

async function getProjectCandidates() {
  try {
    const games = await prisma.game.findMany({
      where: { gameReport: { isNot: null } },
      include: {
        gameReport: true,
        potentialScores: { orderBy: { scoredAt: "desc" }, take: 1 },
      },
      take: 200,
    });
    return games.filter((g) => {
      const p = g.gameReport?.payload as unknown as GameReportPayload | null;
      return !!p?.project_advice?.recommendation;
    });
  } catch {
    return [];
  }
}

export default async function ProjectsPage() {
  const games = await getProjectCandidates();

  // Group by recommendation
  const grouped: Record<
    Recommendation,
    Array<(typeof games)[number]>
  > = {
    pursue: [],
    monitor: [],
    pass: [],
  };

  for (const g of games) {
    const payload = g.gameReport?.payload as unknown as GameReportPayload;
    const advice = payload?.project_advice;
    if (!advice) continue;
    if (advice.recommendation in grouped) {
      grouped[advice.recommendation].push(g);
    }
  }

  // Sort each group by potential score desc
  for (const key of ORDER) {
    grouped[key].sort((a, b) => {
      const as = a.potentialScores[0]?.overallScore ?? 0;
      const bs = b.potentialScores[0]?.overallScore ?? 0;
      return bs - as;
    });
  }

  const total = games.length;

  return (
    <div>
      {/* Title */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold">立项候选池</h2>
        <p className="text-sm text-gray-500 mt-1">
          AI 推荐的 pursue/monitor/pass 游戏
        </p>
      </div>

      {/* Stats bar */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <div className="grid grid-cols-4 gap-3">
          <div className="text-center">
            <p className="text-xs text-gray-500">总候选</p>
            <p className="text-2xl font-bold text-gray-900">{total}</p>
          </div>
          {ORDER.map((key) => {
            const meta = SECTION_META[key];
            const count = grouped[key].length;
            return (
              <div key={key} className="text-center">
                <p className="text-xs text-gray-500">{meta.title}</p>
                <p className="text-2xl font-bold text-gray-900">{count}</p>
              </div>
            );
          })}
        </div>
      </div>

      {total === 0 ? (
        <div className="bg-white rounded-lg shadow p-8 text-center">
          <p className="text-gray-400 text-sm">
            暂无立项建议，请等待 AI 生成 project_advice
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {ORDER.map((key) => {
            const meta = SECTION_META[key];
            const section = grouped[key];
            if (section.length === 0) return null;

            return (
              <section
                key={key}
                className={`${meta.section} border rounded-lg p-4`}
              >
                <div className="flex items-center gap-3 mb-4">
                  <span
                    className={`${meta.badge} ${meta.text} px-3 py-1 rounded-full text-sm font-bold`}
                  >
                    {meta.title}
                  </span>
                  <span className="text-xs text-gray-500">
                    {section.length} 款游戏
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {section.map((g) => {
                    const payload = g.gameReport
                      ?.payload as unknown as GameReportPayload;
                    const advice = payload.project_advice!;
                    const overall = g.potentialScores[0]?.overallScore ?? 0;
                    const grade = g.iaaGrade ?? "-";
                    const gradeColor = GRADE_COLORS[grade] ?? "bg-gray-300";
                    const oneLine =
                      advice.reasoning.length > 80
                        ? advice.reasoning.slice(0, 80) + "…"
                        : advice.reasoning;

                    return (
                      <Link
                        key={g.id}
                        href={`/games/${g.id}`}
                        className="bg-white rounded-lg shadow p-4 hover:shadow-md transition-shadow block"
                      >
                        {/* Header: thumb + name + grade */}
                        <div className="flex items-start gap-3 mb-3">
                          {g.thumbnailUrl ? (
                            <img
                              src={g.thumbnailUrl}
                              alt={g.nameZh || g.nameEn || ""}
                              className="w-12 h-12 rounded-lg shadow object-cover shrink-0"
                            />
                          ) : (
                            <div className="w-12 h-12 rounded-lg bg-gray-100 shrink-0 flex items-center justify-center text-gray-400 text-xs">
                              无图
                            </div>
                          )}
                          <div className="flex-1 min-w-0">
                            <h3 className="font-semibold text-sm truncate">
                              {g.nameZh || g.nameEn || "Unknown"}
                            </h3>
                            {g.nameEn && g.nameZh && (
                              <p className="text-xs text-gray-400 truncate">
                                {g.nameEn}
                              </p>
                            )}
                          </div>
                          <div
                            className={`${gradeColor} text-white w-10 h-10 rounded-lg flex items-center justify-center font-bold text-lg shrink-0 shadow`}
                          >
                            {grade}
                          </div>
                        </div>

                        {/* Genre + recommendation badge */}
                        <div className="flex items-center gap-2 mb-3 flex-wrap">
                          {g.genre && (
                            <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded">
                              {g.genre}
                            </span>
                          )}
                          <span
                            className={`${meta.badge} ${meta.text} text-xs px-2 py-0.5 rounded-full font-medium`}
                          >
                            {meta.title}
                          </span>
                        </div>

                        {/* 1-line reasoning */}
                        <p className="text-xs text-gray-700 mb-3 line-clamp-2">
                          {oneLine}
                        </p>

                        {/* Bottom stats */}
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div className="bg-gray-50 rounded px-2 py-1.5">
                            <p className="text-gray-500">潜力评分</p>
                            <p
                              className={`font-mono font-bold ${
                                overall >= 60
                                  ? "text-green-600"
                                  : overall >= 40
                                    ? "text-yellow-600"
                                    : "text-gray-500"
                              }`}
                            >
                              {overall}
                            </p>
                          </div>
                          <div className="bg-gray-50 rounded px-2 py-1.5">
                            <p className="text-gray-500">预估工期</p>
                            <p className="font-mono font-bold text-blue-600">
                              {advice.resource_estimate_weeks} 周
                            </p>
                          </div>
                        </div>
                      </Link>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
