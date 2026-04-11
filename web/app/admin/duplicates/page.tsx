import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { requirePermission } from "@/lib/auth";

export const dynamic = "force-dynamic";

type DuplicateRow = {
  id1: number;
  id2: number;
  name1_zh: string | null;
  name1_en: string | null;
  dev1: string | null;
  name2_zh: string | null;
  name2_en: string | null;
  dev2: string | null;
  sim: number;
};

async function getDuplicateCandidates(): Promise<DuplicateRow[]> {
  try {
    const rows = await prisma.$queryRaw<DuplicateRow[]>`
      SELECT g1.id AS id1, g2.id AS id2,
             g1.name_zh AS name1_zh, g1.name_en AS name1_en, g1.developer AS dev1,
             g2.name_zh AS name2_zh, g2.name_en AS name2_en, g2.developer AS dev2,
             similarity(COALESCE(g1.name_en, g1.name_zh, ''), COALESCE(g2.name_en, g2.name_zh, ''))::float AS sim
      FROM games g1
      JOIN games g2 ON g1.id < g2.id
      WHERE similarity(COALESCE(g1.name_en, g1.name_zh, ''), COALESCE(g2.name_en, g2.name_zh, '')) BETWEEN 0.6 AND 0.85
        AND COALESCE(g1.developer, '') = COALESCE(g2.developer, '')
        AND NOT (COALESCE((g1.metadata->'dedup_dismissed')::jsonb, '[]'::jsonb) @> to_jsonb(g2.id))
        AND NOT (COALESCE((g2.metadata->'dedup_dismissed')::jsonb, '[]'::jsonb) @> to_jsonb(g1.id))
      ORDER BY sim DESC
      LIMIT 50
    `;
    return rows;
  } catch {
    return [];
  }
}

export default async function AdminDuplicatesPage() {
  await requirePermission("manage_users");

  const pairs = await getDuplicateCandidates();

  return (
    <div>
      <AdminNav active="duplicates" />
      <h2 className="text-2xl font-bold mb-2">去重审核</h2>
      <p className="text-sm text-gray-500 mb-6">
        候选重复游戏 (pg_trgm 相似度 0.60 ~ 0.85, 同开发商)
      </p>

      {pairs.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-8 text-center">
          <p className="text-gray-400">暂无候选重复游戏</p>
        </div>
      ) : (
        <div className="space-y-4">
          {pairs.map((row) => (
            <div
              key={`${row.id1}-${row.id2}`}
              className="bg-white rounded-lg shadow p-4"
            >
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs text-gray-400">
                  相似度{" "}
                  <span className="font-mono text-blue-600">
                    {row.sim.toFixed(3)}
                  </span>
                </span>
                <form
                  action="/api/admin/duplicates/dismiss"
                  method="POST"
                  className="inline"
                >
                  <input type="hidden" name="gameId1" value={row.id1} />
                  <input type="hidden" name="gameId2" value={row.id2} />
                  <button
                    type="submit"
                    className="text-xs text-gray-500 hover:text-gray-700 underline"
                  >
                    标记为不同游戏
                  </button>
                </form>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <GameCard
                  id={row.id1}
                  nameZh={row.name1_zh}
                  nameEn={row.name1_en}
                  developer={row.dev1}
                  mergeIntoLabel={`合并到 #${row.id1}`}
                  mergeFromId={row.id2}
                  mergeIntoId={row.id1}
                />
                <GameCard
                  id={row.id2}
                  nameZh={row.name2_zh}
                  nameEn={row.name2_en}
                  developer={row.dev2}
                  mergeIntoLabel={`合并到 #${row.id2}`}
                  mergeFromId={row.id1}
                  mergeIntoId={row.id2}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function GameCard({
  id,
  nameZh,
  nameEn,
  developer,
  mergeIntoLabel,
  mergeFromId,
  mergeIntoId,
}: {
  id: number;
  nameZh: string | null;
  nameEn: string | null;
  developer: string | null;
  mergeIntoLabel: string;
  mergeFromId: number;
  mergeIntoId: number;
}) {
  return (
    <div className="border border-gray-200 rounded p-3">
      <div className="flex items-start justify-between mb-2">
        <div className="min-w-0 flex-1">
          <Link
            href={`/games/${id}`}
            className="text-sm font-medium hover:text-blue-600 block truncate"
          >
            {nameZh ?? nameEn ?? "(无名)"}
          </Link>
          {nameEn && nameZh && (
            <div className="text-xs text-gray-500 truncate">{nameEn}</div>
          )}
          <div className="text-xs text-gray-400 truncate">
            {developer ?? "-"}
          </div>
        </div>
        <span className="text-xs text-gray-300 font-mono ml-2 flex-shrink-0">
          #{id}
        </span>
      </div>
      <form
        action="/api/admin/duplicates/merge"
        method="POST"
        className="mt-2"
      >
        <input type="hidden" name="mergeFromId" value={mergeFromId} />
        <input type="hidden" name="mergeIntoId" value={mergeIntoId} />
        <button
          type="submit"
          className="text-xs bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-500 w-full"
        >
          {mergeIntoLabel}
        </button>
      </form>
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
