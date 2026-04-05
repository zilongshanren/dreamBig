import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
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
  1: { label: "P1 最高", className: "bg-red-100 text-red-700" },
  2: { label: "P2 高", className: "bg-orange-100 text-orange-700" },
  3: { label: "P3 中", className: "bg-yellow-100 text-yellow-700" },
  4: { label: "P4 低", className: "bg-blue-100 text-blue-700" },
  5: { label: "P5 最低", className: "bg-gray-100 text-gray-700" },
};

const METRIC_LABELS: Record<string, string> = {
  day1_retention: "次日留存",
  day3_retention: "3日留存",
  day7_retention: "7日留存",
  arpdau: "ARPDAU",
  ad_arpdau: "广告 ARPDAU",
  iap_arpdau: "付费 ARPDAU",
  sessions_per_dau: "人均会话数",
  session_length: "会话时长",
};

async function getExperiment(id: number) {
  try {
    return await prisma.experiment.findUnique({
      where: { id },
      include: {
        game: {
          select: {
            id: true,
            nameZh: true,
            nameEn: true,
            genre: true,
            iaaGrade: true,
          },
        },
      },
    });
  } catch {
    return null;
  }
}

// ============================================================
// Server actions
// ============================================================
async function updateStatus(formData: FormData) {
  "use server";
  const id = parseInt(String(formData.get("id") || ""));
  const status = String(formData.get("status") || "");
  const notes = String(formData.get("notes") || "");

  if (Number.isNaN(id) || !status) return;

  const data: Record<string, unknown> = { status, notes: notes || null };

  // Auto-set timestamps when status changes
  if (status === "running") {
    data.startedAt = new Date();
  } else if (status === "completed" || status === "cancelled") {
    data.completedAt = new Date();
  }

  try {
    await prisma.experiment.update({ where: { id }, data });
    revalidatePath(`/experiments/${id}`);
    revalidatePath("/experiments");
  } catch (e) {
    console.error("updateStatus failed:", e);
  }
}

async function updateActualLift(formData: FormData) {
  "use server";
  const id = parseInt(String(formData.get("id") || ""));
  const raw = String(formData.get("actualLift") || "").trim();
  if (Number.isNaN(id)) return;

  const actualLift = raw === "" ? null : Number(raw);

  try {
    await prisma.experiment.update({
      where: { id },
      data: { actualLift },
    });
    revalidatePath(`/experiments/${id}`);
  } catch (e) {
    console.error("updateActualLift failed:", e);
  }
}

async function deleteExperiment(formData: FormData) {
  "use server";
  const id = parseInt(String(formData.get("id") || ""));
  if (Number.isNaN(id)) return;
  try {
    await prisma.experiment.delete({ where: { id } });
    revalidatePath("/experiments");
  } catch (e) {
    console.error("deleteExperiment failed:", e);
    return;
  }
  redirect("/experiments");
}

// ============================================================
// Page
// ============================================================
export default async function ExperimentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const expId = parseInt(id);
  if (!Number.isFinite(expId)) notFound();

  const exp = await getExperiment(expId);
  if (!exp) notFound();

  const gameName =
    exp.game?.nameZh || exp.game?.nameEn || `游戏 #${exp.gameId}`;
  const pri = PRIORITY_LABELS[exp.priority] || PRIORITY_LABELS[3];
  const statusColor = STATUS_COLORS[exp.status] || STATUS_COLORS.draft;
  const statusLabel = STATUS_LABELS[exp.status] || exp.status;
  const metricLabel = METRIC_LABELS[exp.successMetric] || exp.successMetric;

  const expectedLift =
    exp.expectedLift !== null && exp.expectedLift !== undefined
      ? Number(exp.expectedLift)
      : null;
  const actualLift =
    exp.actualLift !== null && exp.actualLift !== undefined
      ? Number(exp.actualLift)
      : null;

  return (
    <div>
      {/* Breadcrumb */}
      <div className="text-sm text-gray-500 mb-4">
        <Link href="/experiments" className="hover:underline">
          商业化实验
        </Link>
        <span className="mx-2">/</span>
        <span className="text-gray-700">{exp.name}</span>
      </div>

      {/* Header */}
      <div className="bg-white rounded-lg shadow p-5 mb-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex-1 min-w-0">
            <h2 className="text-2xl font-bold mb-2 break-words">{exp.name}</h2>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Link
                href={`/games/${exp.gameId}`}
                className="text-blue-600 hover:underline"
              >
                {gameName}
              </Link>
              {exp.game?.genre && (
                <span className="text-gray-500">· {exp.game.genre}</span>
              )}
              {exp.game?.iaaGrade && (
                <span className="px-2 py-0.5 rounded bg-purple-100 text-purple-700">
                  IAA: {exp.game.iaaGrade}
                </span>
              )}
              <span className={`px-2 py-0.5 rounded ${pri.className}`}>
                {pri.label}
              </span>
              <span className={`px-2 py-0.5 rounded ${statusColor}`}>
                {statusLabel}
              </span>
            </div>
          </div>

          {/* Delete */}
          <form action={deleteExperiment}>
            <input type="hidden" name="id" value={exp.id} />
            <button
              type="submit"
              className="text-xs text-red-600 hover:text-red-800 border border-red-200 hover:border-red-400 px-3 py-1 rounded"
            >
              删除实验
            </button>
          </form>
        </div>

        {/* Hypothesis */}
        <div className="mt-4 bg-blue-50 border-l-4 border-blue-400 px-4 py-3 rounded">
          <p className="text-xs font-medium text-blue-700 mb-1">假设</p>
          <p className="text-sm text-gray-800 whitespace-pre-wrap">
            {exp.hypothesis}
          </p>
        </div>
      </div>

      {/* Variants */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <div className="bg-white rounded-lg shadow p-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-6 h-6 rounded-full bg-gray-200 flex items-center justify-center text-xs font-bold">
              A
            </span>
            <h3 className="font-semibold">对照组 (Control)</h3>
          </div>
          <pre className="text-xs bg-gray-50 rounded p-3 overflow-auto max-h-64 text-gray-700">
            {JSON.stringify(exp.variantA, null, 2)}
          </pre>
        </div>
        <div className="bg-white rounded-lg shadow p-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-6 h-6 rounded-full bg-green-200 flex items-center justify-center text-xs font-bold">
              B
            </span>
            <h3 className="font-semibold">实验组 (Treatment)</h3>
          </div>
          <pre className="text-xs bg-gray-50 rounded p-3 overflow-auto max-h-64 text-gray-700">
            {JSON.stringify(exp.variantB, null, 2)}
          </pre>
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500 mb-1">成功指标</p>
          <p className="font-medium text-sm">{metricLabel}</p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500 mb-1">样本量</p>
          <p className="font-medium text-sm">
            {exp.sampleSize != null
              ? `${exp.sampleSize.toLocaleString()} DAU`
              : "未设置"}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500 mb-1">预期提升</p>
          <p className="font-medium text-sm text-blue-600">
            {expectedLift !== null
              ? `${expectedLift > 0 ? "+" : ""}${expectedLift.toFixed(2)}%`
              : "-"}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-xs text-gray-500 mb-1">实际提升</p>
          <p
            className={`font-medium text-sm ${
              actualLift === null
                ? "text-gray-400"
                : actualLift > 0
                  ? "text-green-600"
                  : actualLift < 0
                    ? "text-red-600"
                    : "text-gray-500"
            }`}
          >
            {actualLift !== null
              ? `${actualLift > 0 ? "+" : ""}${actualLift.toFixed(2)}%`
              : "待填写"}
          </p>
        </div>
      </div>

      {/* Timeline */}
      {(exp.startedAt || exp.completedAt) && (
        <div className="bg-white rounded-lg shadow p-5 mb-6">
          <h3 className="font-semibold text-sm mb-3">时间线</h3>
          <div className="text-xs text-gray-600 space-y-1">
            <p>
              创建: {new Date(exp.createdAt).toLocaleString("zh-CN")}
            </p>
            {exp.startedAt && (
              <p>
                启动: {new Date(exp.startedAt).toLocaleString("zh-CN")}
              </p>
            )}
            {exp.completedAt && (
              <p>
                结束: {new Date(exp.completedAt).toLocaleString("zh-CN")}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Status update form */}
      <div className="bg-white rounded-lg shadow p-5 mb-6">
        <h3 className="font-semibold text-sm mb-3">更新状态</h3>
        <form action={updateStatus} className="space-y-3">
          <input type="hidden" name="id" value={exp.id} />
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[160px]">
              <label className="block text-xs font-medium text-gray-700 mb-1">
                状态
              </label>
              <select
                name="status"
                defaultValue={exp.status}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              >
                {Object.entries(STATUS_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              className="bg-gray-900 text-white px-4 py-2 rounded text-sm hover:bg-gray-700"
            >
              保存状态
            </button>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              备注 / 笔记
            </label>
            <textarea
              name="notes"
              defaultValue={exp.notes || ""}
              rows={3}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="观察记录、阶段性结论..."
            />
          </div>
        </form>
      </div>

      {/* Actual lift update */}
      <div className="bg-white rounded-lg shadow p-5">
        <h3 className="font-semibold text-sm mb-3">录入实际结果</h3>
        <form action={updateActualLift} className="flex items-end gap-3">
          <input type="hidden" name="id" value={exp.id} />
          <div className="flex-1 max-w-[200px]">
            <label className="block text-xs font-medium text-gray-700 mb-1">
              实际提升 (%)
            </label>
            <input
              type="number"
              name="actualLift"
              step="0.01"
              defaultValue={actualLift ?? ""}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              placeholder="如 4.2 或 -1.5"
            />
          </div>
          <button
            type="submit"
            className="bg-gray-900 text-white px-4 py-2 rounded text-sm hover:bg-gray-700"
          >
            保存结果
          </button>
        </form>
      </div>
    </div>
  );
}
