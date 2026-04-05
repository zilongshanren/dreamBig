import Link from "next/link";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  planned: "已计划",
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
};

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700",
  planned: "bg-blue-100 text-blue-700",
  running: "bg-yellow-100 text-yellow-700",
  completed: "bg-green-100 text-green-700",
  cancelled: "bg-red-100 text-red-700",
};

const PRIORITY_LABELS: Record<number, { label: string; className: string }> = {
  1: { label: "P1", className: "bg-red-100 text-red-700" },
  2: { label: "P2", className: "bg-orange-100 text-orange-700" },
  3: { label: "P3", className: "bg-yellow-100 text-yellow-700" },
  4: { label: "P4", className: "bg-blue-100 text-blue-700" },
  5: { label: "P5", className: "bg-gray-100 text-gray-700" },
};

const METRIC_LABELS: Record<string, string> = {
  day1_retention: "次日留存",
  day3_retention: "3日留存",
  day7_retention: "7日留存",
  arpdau: "ARPDAU",
  ad_arpdau: "广告 ARPDAU",
  iap_arpdau: "付费 ARPDAU",
  sessions_per_dau: "人均会话",
  session_length: "会话时长",
};

const STATUS_ORDER = ["running", "planned", "draft", "completed", "cancelled"];

type ExperimentListItem = {
  id: number;
  gameId: number;
  name: string;
  status: string;
  priority: number;
  successMetric: string;
  expectedLift: unknown;
  actualLift: unknown;
  createdAt: Date;
  game: { nameZh: string | null; nameEn: string | null } | null;
};

async function getExperiments(
  statusFilter: string | undefined,
): Promise<ExperimentListItem[]> {
  try {
    const where: Record<string, unknown> = {};
    if (statusFilter && STATUS_LABELS[statusFilter]) {
      where.status = statusFilter;
    }
    const rows = await prisma.experiment.findMany({
      where,
      orderBy: [{ priority: "asc" }, { createdAt: "desc" }],
      take: 200,
      include: {
        game: { select: { nameZh: true, nameEn: true } },
      },
    });
    return rows as unknown as ExperimentListItem[];
  } catch {
    return [];
  }
}

function formatLift(lift: unknown): string {
  if (lift === null || lift === undefined) return "-";
  const n = Number(lift);
  if (!Number.isFinite(n)) return "-";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function liftColor(lift: unknown): string {
  if (lift === null || lift === undefined) return "text-gray-400";
  const n = Number(lift);
  if (!Number.isFinite(n) || n === 0) return "text-gray-500";
  return n > 0 ? "text-green-600" : "text-red-600";
}

export default async function ExperimentsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;
  const experiments = await getExperiments(status);

  // Group by status when no filter selected
  const grouped: Record<string, ExperimentListItem[]> = {};
  for (const exp of experiments) {
    const key = exp.status || "draft";
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(exp);
  }

  const sortedStatuses = STATUS_ORDER.filter((s) => grouped[s]?.length);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">商业化实验</h2>
          <p className="text-sm text-gray-500 mt-1">
            追踪每款游戏的 IAA / 变现 A/B 测试计划与结果
          </p>
        </div>
        <Link
          href="/experiments/new"
          className="bg-gray-900 text-white px-4 py-1.5 rounded text-sm hover:bg-gray-700"
        >
          + 新建实验
        </Link>
      </div>

      {/* Status filter */}
      <div className="flex flex-wrap gap-2 mb-6 text-sm">
        <Link
          href="/experiments"
          className={`px-3 py-1 rounded border ${
            !status
              ? "bg-gray-900 text-white border-gray-900"
              : "bg-white text-gray-600 border-gray-300 hover:border-gray-500"
          }`}
        >
          全部 ({experiments.length})
        </Link>
        {Object.entries(STATUS_LABELS).map(([k, label]) => {
          const count = grouped[k]?.length ?? 0;
          return (
            <Link
              key={k}
              href={`/experiments?status=${k}`}
              className={`px-3 py-1 rounded border ${
                status === k
                  ? "bg-gray-900 text-white border-gray-900"
                  : "bg-white text-gray-600 border-gray-300 hover:border-gray-500"
              }`}
            >
              {label} ({count})
            </Link>
          );
        })}
      </div>

      {experiments.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <p className="text-gray-400 text-sm mb-4">
            {status
              ? `暂无 ${STATUS_LABELS[status] || status} 状态的实验`
              : "暂无实验记录"}
          </p>
          <Link
            href="/experiments/new"
            className="inline-block bg-gray-900 text-white px-4 py-2 rounded text-sm hover:bg-gray-700"
          >
            创建第一个实验
          </Link>
        </div>
      ) : status ? (
        // Filtered view: flat table
        <ExperimentTable experiments={experiments} />
      ) : (
        // Grouped view: one table per status
        <div className="space-y-8">
          {sortedStatuses.map((s) => (
            <div key={s}>
              <div className="flex items-center gap-2 mb-3">
                <h3 className="font-semibold text-lg">{STATUS_LABELS[s]}</h3>
                <span
                  className={`text-xs px-2 py-0.5 rounded ${STATUS_COLORS[s]}`}
                >
                  {grouped[s].length}
                </span>
              </div>
              <ExperimentTable experiments={grouped[s]} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ExperimentTable({
  experiments,
}: {
  experiments: ExperimentListItem[];
}) {
  return (
    <div className="bg-white rounded-lg shadow overflow-hidden overflow-x-auto">
      <table className="w-full text-sm min-w-[800px]">
        <thead className="bg-gray-50 border-b">
          <tr>
            <th className="text-left px-4 py-2 font-medium text-gray-500">
              名称
            </th>
            <th className="text-left px-4 py-2 font-medium text-gray-500">
              游戏
            </th>
            <th className="text-center px-4 py-2 font-medium text-gray-500">
              优先级
            </th>
            <th className="text-center px-4 py-2 font-medium text-gray-500">
              状态
            </th>
            <th className="text-left px-4 py-2 font-medium text-gray-500">
              成功指标
            </th>
            <th className="text-right px-4 py-2 font-medium text-gray-500">
              预期提升
            </th>
            <th className="text-right px-4 py-2 font-medium text-gray-500">
              实际提升
            </th>
            <th className="text-right px-4 py-2 font-medium text-gray-500">
              创建时间
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {experiments.map((exp) => {
            const pri =
              PRIORITY_LABELS[exp.priority] || PRIORITY_LABELS[3];
            const gameName =
              exp.game?.nameZh || exp.game?.nameEn || `游戏 #${exp.gameId}`;
            return (
              <tr key={exp.id} className="hover:bg-gray-50">
                <td className="px-4 py-2">
                  <Link
                    href={`/experiments/${exp.id}`}
                    className="text-blue-600 hover:underline font-medium"
                  >
                    {exp.name}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <Link
                    href={`/games/${exp.gameId}`}
                    className="text-gray-700 hover:underline text-xs"
                  >
                    {gameName}
                  </Link>
                </td>
                <td className="text-center px-4 py-2">
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${pri.className}`}
                  >
                    {pri.label}
                  </span>
                </td>
                <td className="text-center px-4 py-2">
                  <span
                    className={`text-xs px-2 py-0.5 rounded ${
                      STATUS_COLORS[exp.status] || STATUS_COLORS.draft
                    }`}
                  >
                    {STATUS_LABELS[exp.status] || exp.status}
                  </span>
                </td>
                <td className="px-4 py-2 text-xs text-gray-600">
                  {METRIC_LABELS[exp.successMetric] || exp.successMetric}
                </td>
                <td
                  className={`text-right px-4 py-2 text-xs ${liftColor(
                    exp.expectedLift,
                  )}`}
                >
                  {formatLift(exp.expectedLift)}
                </td>
                <td
                  className={`text-right px-4 py-2 text-xs font-medium ${liftColor(
                    exp.actualLift,
                  )}`}
                >
                  {formatLift(exp.actualLift)}
                </td>
                <td className="text-right px-4 py-2 text-xs text-gray-400">
                  {new Date(exp.createdAt).toLocaleDateString("zh-CN")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
