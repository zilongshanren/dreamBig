import Link from "next/link";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

// Grade color mapping
const GRADE_COLORS: Record<
  string,
  { bg: string; text: string; label: string; ring: string }
> = {
  S: { bg: "bg-green-500", text: "text-white", label: "极适合", ring: "ring-green-200" },
  A: { bg: "bg-lime-500", text: "text-white", label: "适合", ring: "ring-lime-200" },
  B: { bg: "bg-yellow-500", text: "text-white", label: "可尝试", ring: "ring-yellow-200" },
  C: { bg: "bg-orange-500", text: "text-white", label: "谨慎", ring: "ring-orange-200" },
  D: { bg: "bg-red-500", text: "text-white", label: "不建议", ring: "ring-red-200" },
};

const GRADE_ORDER = ["S", "A", "B", "C", "D"];

// Genre keys (mirrors dashboard)
const GENRE_ENTRIES: Array<[string, string]> = [
  ["idle", "放置"],
  ["merge", "合成"],
  ["match3", "三消"],
  ["puzzle", "益智"],
  ["casual_action", "休闲动作"],
  ["runner", "跑酷"],
  ["tower_defense", "塔防"],
  ["simulation", "模拟"],
  ["word", "文字"],
  ["trivia", "问答"],
  ["arcade", "街机"],
  ["board", "棋牌"],
  ["card", "卡牌"],
  ["sports", "体育"],
  ["racing", "竞速"],
  ["strategy", "策略"],
  ["rpg", "角色扮演"],
  ["adventure", "冒险"],
  ["shooter", "射击"],
  ["moba", "MOBA"],
];

// Inline GameReport payload type
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
  iaa_advice?: IaaAdvice;
  overall_confidence?: number;
};

type Filters = {
  grade: string; // "all" or one of S/A/B/C/D
  genre: string; // "all" or genre key
  minScore: number;
};

function parseFilters(params: {
  grade?: string;
  genre?: string;
  minScore?: string;
}): Filters {
  const grade =
    params.grade && GRADE_ORDER.includes(params.grade) ? params.grade : "all";
  const genre = params.genre && params.genre.length > 0 ? params.genre : "all";
  const minRaw = params.minScore ? parseInt(params.minScore, 10) : 50;
  const minScore = Number.isFinite(minRaw)
    ? Math.max(0, Math.min(100, minRaw))
    : 50;
  return { grade, genre, minScore };
}

async function getIaaCandidates() {
  try {
    const games = await prisma.game.findMany({
      where: { gameReport: { isNot: null } },
      include: {
        gameReport: true,
        potentialScores: { orderBy: { scoredAt: "desc" }, take: 1 },
      },
      take: 100,
    });
    // Sort in memory: S first, then A, B, C, D, then null
    games.sort((a, b) => {
      const ai = a.iaaGrade ? GRADE_ORDER.indexOf(a.iaaGrade) : 99;
      const bi = b.iaaGrade ? GRADE_ORDER.indexOf(b.iaaGrade) : 99;
      if (ai !== bi) return ai - bi;
      // secondary: overall score desc
      const as = a.potentialScores[0]?.overallScore ?? 0;
      const bs = b.potentialScores[0]?.overallScore ?? 0;
      return bs - as;
    });
    return games;
  } catch {
    return [];
  }
}

export default async function IaaListPage({
  searchParams,
}: {
  searchParams: Promise<{ grade?: string; genre?: string; minScore?: string }>;
}) {
  const params = await searchParams;
  const filters = parseFilters(params);
  const allCandidates = await getIaaCandidates();

  // Filter in-memory
  const candidates = allCandidates.filter((g) => {
    if (filters.grade !== "all" && g.iaaGrade !== filters.grade) return false;
    if (filters.genre !== "all" && g.genre !== filters.genre) return false;
    const overall = g.potentialScores[0]?.overallScore ?? 0;
    if (overall < filters.minScore) return false;
    return true;
  });

  // Grade distribution
  const distribution: Record<string, number> = { S: 0, A: 0, B: 0, C: 0, D: 0 };
  for (const g of allCandidates) {
    if (g.iaaGrade && distribution[g.iaaGrade] !== undefined) {
      distribution[g.iaaGrade] += 1;
    }
  }
  const maxDist = Math.max(1, ...Object.values(distribution));

  const topCandidates = candidates.slice(0, 50);

  return (
    <div>
      {/* Title */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold">IAA 改造顾问</h2>
        <p className="text-sm text-gray-500 mt-1">
          基于玩法循环、评论证据和广告位匹配度，为候选游戏提供 IAA
          适配建议与改造路径
        </p>
      </div>

      {/* Grade Distribution */}
      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="font-semibold text-sm mb-3 text-gray-600">等级分布</h3>
        <div className="flex items-end gap-3 h-28">
          {GRADE_ORDER.map((g) => {
            const count = distribution[g] ?? 0;
            const h = (count / maxDist) * 100;
            const color = GRADE_COLORS[g];
            return (
              <div
                key={g}
                className="flex-1 flex flex-col items-center justify-end gap-2"
              >
                <span className="text-xs font-mono text-gray-500">{count}</span>
                <div
                  className={`w-full ${color.bg} rounded-t`}
                  style={{ height: `${Math.max(h, 4)}%` }}
                />
                <span className="text-xs font-bold text-gray-700">{g}</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Filter Bar */}
      <form
        method="get"
        className="bg-white rounded-lg shadow p-4 mb-6 flex flex-wrap gap-3 items-end"
      >
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">等级</label>
          <select
            name="grade"
            defaultValue={filters.grade}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="all">全部</option>
            {GRADE_ORDER.map((g) => (
              <option key={g} value={g}>
                {g} - {GRADE_COLORS[g].label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">品类</label>
          <select
            name="genre"
            defaultValue={filters.genre}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="all">全部</option>
            {GENRE_ENTRIES.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">
            最低潜力评分: {filters.minScore}
          </label>
          <input
            type="range"
            name="minScore"
            min={0}
            max={100}
            step={5}
            defaultValue={filters.minScore}
            className="w-40"
          />
        </div>
        <button
          type="submit"
          className="text-sm bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-1.5"
        >
          应用
        </button>
        {(filters.grade !== "all" ||
          filters.genre !== "all" ||
          filters.minScore !== 50) && (
          <Link
            href="/iaa"
            className="text-sm text-gray-500 hover:text-gray-700 underline py-1.5"
          >
            重置
          </Link>
        )}
        <span className="text-xs text-gray-400 ml-auto self-center">
          {topCandidates.length} / {allCandidates.length} 个候选
        </span>
      </form>

      {/* Game Cards Grid */}
      {topCandidates.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-8 text-center">
          <p className="text-gray-400 text-sm">
            暂无符合条件的候选游戏，请尝试调整筛选条件或等待报告生成
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {topCandidates.map((g) => {
            const payload = g.gameReport?.payload as unknown as
              | GameReportPayload
              | null;
            const advice = payload?.iaa_advice;
            const grade = g.iaaGrade ?? "-";
            const gradeStyle = GRADE_COLORS[grade] ?? {
              bg: "bg-gray-300",
              text: "text-white",
              label: "-",
              ring: "ring-gray-200",
            };
            const overall = g.potentialScores[0]?.overallScore ?? 0;
            const confidence =
              advice?.confidence ?? payload?.overall_confidence ?? null;
            const topSuitable = (advice?.suitable_placements ?? []).slice(0, 2);

            return (
              <Link
                key={g.id}
                href={`/iaa/${g.id}`}
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
                    className={`${gradeStyle.bg} ${gradeStyle.text} w-10 h-10 rounded-lg flex items-center justify-center font-bold text-lg shrink-0 shadow`}
                  >
                    {grade}
                  </div>
                </div>

                {/* Genre + scores */}
                <div className="flex items-center gap-2 mb-3 flex-wrap">
                  {g.genre && (
                    <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded">
                      {g.genre}
                    </span>
                  )}
                  <span className="text-xs text-gray-500">
                    {gradeStyle.label}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
                  <div className="bg-gray-50 rounded px-2 py-1.5">
                    <p className="text-gray-500">IAA 适配</p>
                    <p className="font-mono font-bold text-blue-600">
                      {g.iaaSuitability}
                    </p>
                  </div>
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
                </div>

                {/* Top 2 suitable placements */}
                {topSuitable.length > 0 && (
                  <div className="mb-3">
                    <p className="text-xs text-gray-500 mb-1">推荐广告位</p>
                    <ul className="text-xs text-gray-700 space-y-0.5">
                      {topSuitable.map((p, i) => (
                        <li key={i} className="truncate">
                          <span className="text-green-600">✓</span> {p}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Confidence indicator */}
                {confidence != null && (
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-gray-500">置信度</span>
                    <div className="flex-1 h-1.5 bg-gray-200 rounded overflow-hidden">
                      <div
                        className="h-full bg-blue-500"
                        style={{ width: `${Math.round(confidence * 100)}%` }}
                      />
                    </div>
                    <span className="font-mono text-gray-600">
                      {Math.round(confidence * 100)}%
                    </span>
                  </div>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
