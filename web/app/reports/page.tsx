import Link from "next/link";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

const REPORT_TYPE_META: Record<
  string,
  { label: string; emoji: string; description: string }
> = {
  weekly_genre: {
    label: "赛道周报",
    emoji: "📊",
    description: "每周赛道趋势分析",
  },
  project_advice_batch: {
    label: "立项批量建议",
    emoji: "🎯",
    description: "Top 游戏立项建议合集",
  },
  experiment_summary: {
    label: "实验总结",
    emoji: "🧪",
    description: "商业化实验结果摘要",
  },
};

const TYPE_ORDER = [
  "weekly_genre",
  "project_advice_batch",
  "experiment_summary",
];

type ReportRow = {
  id: number;
  reportType: string;
  scope: string;
  title: string;
  summary: string | null;
  evidenceCount: number;
  generatedAt: Date;
};

async function getReports(): Promise<ReportRow[]> {
  try {
    const rows = await prisma.generatedReport.findMany({
      orderBy: { generatedAt: "desc" },
      take: 100,
      select: {
        id: true,
        reportType: true,
        scope: true,
        title: true,
        summary: true,
        evidenceCount: true,
        generatedAt: true,
      },
    });
    return rows as unknown as ReportRow[];
  } catch {
    return [];
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

export default async function ReportsPage() {
  const reports = await getReports();

  // Group by reportType
  const grouped = new Map<string, ReportRow[]>();
  for (const r of reports) {
    const arr = grouped.get(r.reportType) ?? [];
    arr.push(r);
    grouped.set(r.reportType, arr);
  }

  // Stable ordering: known types first, then anything else alphabetically
  const knownTypes = TYPE_ORDER.filter((t) => grouped.has(t));
  const extraTypes = Array.from(grouped.keys())
    .filter((t) => !TYPE_ORDER.includes(t))
    .sort();
  const orderedTypes = [...knownTypes, ...extraTypes];

  return (
    <div>
      {/* Title */}
      <div className="mb-6">
        <h2 className="text-2xl font-bold">分析报告</h2>
        <p className="text-sm text-gray-500 mt-1">
          自动生成的赛道周报、立项建议与实验总结，按类型分组查看
        </p>
      </div>

      {/* Empty state */}
      {reports.length === 0 && (
        <div className="bg-white rounded-lg shadow p-8 text-center">
          <p className="text-gray-400 text-sm">尚无报告，等待定时生成</p>
        </div>
      )}

      {/* Grouped report list */}
      {orderedTypes.map((type) => {
        const meta =
          REPORT_TYPE_META[type] ?? {
            label: type,
            emoji: "📄",
            description: "",
          };
        const list = grouped.get(type) ?? [];
        return (
          <section key={type} className="mb-8">
            <div className="flex items-baseline gap-2 mb-3">
              <h3 className="text-lg font-semibold">
                <span className="mr-2">{meta.emoji}</span>
                {meta.label}
              </h3>
              <span className="text-xs text-gray-400">{meta.description}</span>
              <span className="text-xs text-gray-400 ml-auto">
                共 {list.length} 篇
              </span>
            </div>

            <div className="space-y-2">
              {list.map((r) => (
                <Link
                  key={r.id}
                  href={`/reports/${r.id}`}
                  className="bg-white rounded-lg shadow p-4 block hover:shadow-md transition-shadow"
                >
                  <div className="flex items-start justify-between gap-3 mb-1">
                    <h4 className="font-semibold text-sm text-gray-900 flex-1 min-w-0">
                      {r.title}
                    </h4>
                    <span className="text-xs font-mono text-gray-500 shrink-0 bg-gray-50 px-2 py-0.5 rounded">
                      {r.scope}
                    </span>
                  </div>
                  {r.summary && (
                    <p className="text-xs text-gray-600 mt-1 line-clamp-2">
                      {r.summary}
                    </p>
                  )}
                  <div className="flex items-center gap-3 mt-2 text-xs text-gray-400">
                    <span>{formatDate(r.generatedAt)}</span>
                    {r.evidenceCount > 0 && (
                      <span>证据 {r.evidenceCount} 条</span>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
