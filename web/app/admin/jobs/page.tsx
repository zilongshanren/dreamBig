import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { requirePermission } from "@/lib/auth";
import { PLATFORM_LABELS } from "@/lib/utils";

export const dynamic = "force-dynamic";

const JOB_STATUSES = ["pending", "running", "success", "failed"] as const;
const JOB_TYPES = [
  "rankings",
  "details",
  "social",
  "ads",
  "reviews",
  "report_generation",
] as const;

type Filters = {
  platform: string;
  status: string;
  jobType: string;
};

function parseFilters(params: {
  platform?: string;
  status?: string;
  jobType?: string;
}): Filters {
  return {
    platform: params.platform && params.platform !== "all" ? params.platform : "all",
    status: params.status && params.status !== "all" ? params.status : "all",
    jobType: params.jobType && params.jobType !== "all" ? params.jobType : "all",
  };
}

async function getJobs(filters: Filters) {
  try {
    return await prisma.scrapeJob.findMany({
      where: {
        ...(filters.platform !== "all" ? { platform: filters.platform } : {}),
        ...(filters.status !== "all" ? { status: filters.status } : {}),
        ...(filters.jobType !== "all" ? { jobType: filters.jobType } : {}),
      },
      orderBy: { startedAt: "desc" },
      take: 200,
    });
  } catch {
    return [];
  }
}

async function getLast24hStats() {
  try {
    const since = new Date(Date.now() - 24 * 3600 * 1000);
    const rows = await prisma.scrapeJob.groupBy({
      by: ["status"],
      where: { startedAt: { gte: since } },
      _count: { _all: true },
    });
    const stats: Record<string, number> = {
      success: 0,
      failed: 0,
      pending: 0,
      running: 0,
    };
    for (const r of rows) {
      stats[r.status] = r._count._all;
    }
    return stats;
  } catch {
    return { success: 0, failed: 0, pending: 0, running: 0 };
  }
}

async function getDistinctPlatforms() {
  try {
    const rows = await prisma.scrapeJob.findMany({
      distinct: ["platform"],
      select: { platform: true },
      take: 50,
    });
    return rows.map((r) => r.platform).sort();
  } catch {
    return [];
  }
}

function formatDuration(
  start: Date | null | undefined,
  end: Date | null | undefined,
): string {
  if (!start || !end) return "-";
  const ms = end.getTime() - start.getTime();
  if (ms < 0) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m${s}s`;
}

function statusBadge(status: string): string {
  if (status === "success") return "bg-green-100 text-green-700";
  if (status === "failed") return "bg-red-100 text-red-700";
  if (status === "running") return "bg-blue-100 text-blue-700";
  return "bg-yellow-100 text-yellow-700";
}

export default async function AdminJobsPage({
  searchParams,
}: {
  searchParams: Promise<{
    platform?: string;
    status?: string;
    jobType?: string;
  }>;
}) {
  await requirePermission("manage_users");

  const params = await searchParams;
  const filters = parseFilters(params);

  const [jobs, stats, platforms] = await Promise.all([
    getJobs(filters),
    getLast24hStats(),
    getDistinctPlatforms(),
  ]);

  return (
    <div>
      <AdminNav active="jobs" />
      <h2 className="text-2xl font-bold mb-6">爬虫任务监控</h2>

      {/* Last 24h stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        <StatCard label="成功 (24h)" value={stats.success} color="green" />
        <StatCard label="失败 (24h)" value={stats.failed} color="red" />
        <StatCard label="运行中" value={stats.running} color="blue" />
        <StatCard label="待处理" value={stats.pending} color="yellow" />
      </div>

      {/* Filter bar */}
      <form
        method="get"
        className="bg-white rounded-lg shadow p-4 mb-6 flex flex-wrap gap-3 items-end"
      >
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">平台</label>
          <select
            name="platform"
            defaultValue={filters.platform}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="all">全部</option>
            {platforms.map((p) => (
              <option key={p} value={p}>
                {PLATFORM_LABELS[p] ?? p}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">状态</label>
          <select
            name="status"
            defaultValue={filters.status}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="all">全部</option>
            {JOB_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">任务类型</label>
          <select
            name="jobType"
            defaultValue={filters.jobType}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="all">全部</option>
            {JOB_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          className="text-sm bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-1.5"
        >
          应用
        </button>
        {(filters.platform !== "all" ||
          filters.status !== "all" ||
          filters.jobType !== "all") && (
          <Link
            href="/admin/jobs"
            className="text-sm text-gray-500 hover:text-gray-700 underline py-1.5"
          >
            重置
          </Link>
        )}
        <span className="text-xs text-gray-400 ml-auto self-center">
          显示最近 200 条
        </span>
      </form>

      {/* Jobs table */}
      <div className="bg-white rounded-lg shadow overflow-hidden overflow-x-auto">
        <table className="w-full text-sm min-w-[900px]">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                #
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                平台
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                类型
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                状态
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                数量
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                重试
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                开始时间
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                结束时间
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                耗时
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                错误
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                操作
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {jobs.length === 0 ? (
              <tr>
                <td
                  colSpan={11}
                  className="px-3 py-8 text-center text-gray-400"
                >
                  暂无符合条件的任务
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr key={job.id} className="hover:bg-gray-50">
                  <td className="px-3 py-2 text-xs text-gray-400 font-mono">
                    {job.id}
                  </td>
                  <td className="px-3 py-2">
                    {PLATFORM_LABELS[job.platform] ?? job.platform}
                  </td>
                  <td className="px-3 py-2 text-gray-500">{job.jobType}</td>
                  <td className="text-center px-3 py-2">
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${statusBadge(job.status)}`}
                    >
                      {job.status}
                    </span>
                  </td>
                  <td className="text-center px-3 py-2 font-mono text-gray-500">
                    {job.itemsScraped}
                  </td>
                  <td className="text-center px-3 py-2 font-mono text-gray-500">
                    {job.retryCount}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-400">
                    {job.startedAt?.toLocaleString("zh-CN") ?? "-"}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-400">
                    {job.finishedAt?.toLocaleString("zh-CN") ?? "-"}
                  </td>
                  <td className="text-center px-3 py-2 text-xs text-gray-500 font-mono">
                    {formatDuration(job.startedAt, job.finishedAt)}
                  </td>
                  <td
                    className="px-3 py-2 text-xs text-red-500 max-w-[240px] truncate"
                    title={job.errorMessage ?? undefined}
                  >
                    {job.errorMessage ?? "-"}
                  </td>
                  <td className="text-center px-3 py-2">
                    <form
                      action={`/api/admin/jobs/${job.id}/retry`}
                      method="POST"
                    >
                      <button
                        type="submit"
                        className="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-500"
                      >
                        重试
                      </button>
                    </form>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: "green" | "red" | "blue" | "yellow";
}) {
  const colorMap = {
    green: "text-green-600",
    red: "text-red-600",
    blue: "text-blue-600",
    yellow: "text-yellow-600",
  };
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <p className="text-sm text-gray-500">{label}</p>
      <p className={`text-3xl font-bold mt-1 ${colorMap[color]}`}>
        {value.toLocaleString()}
      </p>
    </div>
  );
}

function AdminNav({
  active,
}: {
  active: "home" | "jobs" | "games" | "duplicates";
}) {
  const items: Array<{ key: typeof active; label: string; href: string }> = [
    { key: "home", label: "概览", href: "/admin" },
    { key: "jobs", label: "爬虫任务", href: "/admin/jobs" },
    { key: "games", label: "游戏主档", href: "/admin/games" },
    { key: "duplicates", label: "去重审核", href: "/admin/duplicates" },
  ];
  return (
    <div className="flex gap-1 mb-4 border-b pb-2">
      {items.map((it) => (
        <Link
          key={it.key}
          href={it.href}
          className={`text-sm px-3 py-1.5 rounded ${
            it.key === active
              ? "bg-blue-600 text-white"
              : "text-gray-600 hover:bg-gray-100"
          }`}
        >
          {it.label}
        </Link>
      ))}
    </div>
  );
}
