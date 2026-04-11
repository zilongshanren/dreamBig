import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";
import { requirePermission } from "@/lib/auth";
import { PLATFORM_LABELS } from "@/lib/utils";
import { EditGameForm } from "./edit-form";

export const dynamic = "force-dynamic";

async function getGame(id: number) {
  try {
    return await prisma.game.findUnique({
      where: { id },
      include: {
        platformListings: true,
        potentialScores: {
          orderBy: { scoredAt: "desc" },
          take: 1,
        },
      },
    });
  } catch {
    return null;
  }
}

export default async function AdminGameEditPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  await requirePermission("manage_users");

  const { id } = await params;
  const gameId = parseInt(id, 10);
  if (!Number.isFinite(gameId)) notFound();

  const game = await getGame(gameId);
  if (!game) notFound();

  const latestScore = game.potentialScores[0] ?? null;

  return (
    <div>
      <AdminNav active="games" />
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">
          编辑游戏 #{game.id}
          <span className="text-base font-normal text-gray-500 ml-3">
            {game.nameZh ?? game.nameEn ?? "-"}
          </span>
        </h2>
        <Link
          href="/admin/games"
          className="text-sm text-gray-500 hover:text-gray-700 underline"
        >
          返回列表
        </Link>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Edit form */}
        <div className="lg:col-span-2 bg-white rounded-lg shadow p-6">
          <h3 className="font-semibold mb-4">基本信息</h3>
          <EditGameForm
            gameId={game.id}
            initial={{
              nameZh: game.nameZh ?? "",
              nameEn: game.nameEn ?? "",
              developer: game.developer ?? "",
              genre: game.genre ?? "",
              iaaSuitability: game.iaaSuitability,
              iaaGrade: game.iaaGrade ?? "",
              gameplayTags: (game.gameplayTags ?? []).join(", "),
              positioning: game.positioning ?? "",
              coreLoop: game.coreLoop ?? "",
            }}
          />
        </div>

        {/* Read-only sidebar */}
        <div className="space-y-4">
          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="font-semibold mb-3">平台列表</h3>
            {game.platformListings.length === 0 ? (
              <p className="text-gray-400 text-sm">暂无平台记录</p>
            ) : (
              <div className="space-y-2">
                {game.platformListings.map((pl) => (
                  <div
                    key={pl.id}
                    className="flex items-center justify-between text-sm py-1.5 border-b last:border-b-0"
                  >
                    <div className="min-w-0">
                      <div className="font-medium truncate">
                        {PLATFORM_LABELS[pl.platform] ?? pl.platform}
                      </div>
                      <div className="text-xs text-gray-400 truncate">
                        {pl.platformId}
                      </div>
                    </div>
                    <div className="text-right text-xs text-gray-500 flex-shrink-0 ml-2">
                      {pl.rating != null && (
                        <div>★ {pl.rating.toString()}</div>
                      )}
                      {pl.ratingCount != null && (
                        <div className="font-mono">
                          {pl.ratingCount.toLocaleString()}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="bg-white rounded-lg shadow p-4">
            <h3 className="font-semibold mb-3">最新评分快照</h3>
            {latestScore == null ? (
              <p className="text-gray-400 text-sm">暂无评分</p>
            ) : (
              <div className="space-y-1 text-sm">
                <ScoreRow label="综合" value={latestScore.overallScore} bold />
                <ScoreRow label="榜单速度" value={latestScore.rankingVelocity} />
                <ScoreRow label="品类契合" value={latestScore.genreFit} />
                <ScoreRow label="社交热度" value={latestScore.socialBuzz} />
                <ScoreRow label="跨平台" value={latestScore.crossPlatform} />
                <ScoreRow label="评分质量" value={latestScore.ratingQuality} />
                <ScoreRow label="竞争缺口" value={latestScore.competitionGap} />
                <ScoreRow label="广告活跃" value={latestScore.adActivity} />
                <div className="text-xs text-gray-400 mt-2 pt-2 border-t">
                  {latestScore.scoredAt.toLocaleDateString("zh-CN")} ·{" "}
                  {latestScore.algorithmVersion}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ScoreRow({
  label,
  value,
  bold = false,
}: {
  label: string;
  value: number;
  bold?: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className={`text-gray-500 ${bold ? "font-semibold" : ""}`}>
        {label}
      </span>
      <span
        className={`font-mono ${bold ? "text-lg font-bold text-blue-600" : "text-gray-700"}`}
      >
        {value}
      </span>
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
