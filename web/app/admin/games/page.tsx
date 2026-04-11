import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { requirePermission } from "@/lib/auth";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;

type Filters = {
  q: string;
  genre: string;
  page: number;
};

function parseFilters(params: {
  q?: string;
  genre?: string;
  page?: string;
}): Filters {
  const pageRaw = parseInt(params.page ?? "1", 10);
  return {
    q: (params.q ?? "").trim(),
    genre: (params.genre ?? "").trim(),
    page: Number.isFinite(pageRaw) && pageRaw > 0 ? pageRaw : 1,
  };
}

async function getGames(filters: Filters) {
  try {
    const where = {
      ...(filters.q
        ? {
            OR: [
              { nameZh: { contains: filters.q, mode: "insensitive" as const } },
              { nameEn: { contains: filters.q, mode: "insensitive" as const } },
              { developer: { contains: filters.q, mode: "insensitive" as const } },
            ],
          }
        : {}),
      ...(filters.genre ? { genre: filters.genre } : {}),
    };

    const [rows, total] = await Promise.all([
      prisma.game.findMany({
        where,
        orderBy: { id: "desc" },
        skip: (filters.page - 1) * PAGE_SIZE,
        take: PAGE_SIZE,
        include: {
          _count: { select: { platformListings: true } },
        },
      }),
      prisma.game.count({ where }),
    ]);

    return { rows, total };
  } catch {
    return { rows: [], total: 0 };
  }
}

async function getDistinctGenres() {
  try {
    const rows = await prisma.game.findMany({
      where: { genre: { not: null } },
      distinct: ["genre"],
      select: { genre: true },
      take: 100,
    });
    return rows
      .map((r) => r.genre)
      .filter((g): g is string => typeof g === "string" && g.length > 0)
      .sort();
  } catch {
    return [];
  }
}

function buildQs(filters: Filters, overrides: Partial<Filters>): string {
  const merged = { ...filters, ...overrides };
  const sp = new URLSearchParams();
  if (merged.q) sp.set("q", merged.q);
  if (merged.genre) sp.set("genre", merged.genre);
  if (merged.page && merged.page !== 1) sp.set("page", String(merged.page));
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export default async function AdminGamesPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; genre?: string; page?: string }>;
}) {
  await requirePermission("manage_users");

  const params = await searchParams;
  const filters = parseFilters(params);

  const [{ rows, total }, genres] = await Promise.all([
    getGames(filters),
    getDistinctGenres(),
  ]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      <AdminNav active="games" />
      <h2 className="text-2xl font-bold mb-6">游戏主档管理</h2>

      {/* Filter bar */}
      <form
        method="get"
        className="bg-white rounded-lg shadow p-4 mb-6 flex flex-wrap gap-3 items-end"
      >
        <div className="flex flex-col flex-1 min-w-[220px]">
          <label className="text-xs text-gray-500 mb-1">
            搜索 (名称 / 开发者)
          </label>
          <input
            type="text"
            name="q"
            defaultValue={filters.q}
            placeholder="输入关键词..."
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          />
        </div>
        <div className="flex flex-col">
          <label className="text-xs text-gray-500 mb-1">品类</label>
          <select
            name="genre"
            defaultValue={filters.genre}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            <option value="">全部</option>
            {genres.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          className="text-sm bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-1.5"
        >
          搜索
        </button>
        {(filters.q || filters.genre) && (
          <Link
            href="/admin/games"
            className="text-sm text-gray-500 hover:text-gray-700 underline py-1.5"
          >
            重置
          </Link>
        )}
        <span className="text-xs text-gray-400 ml-auto self-center">
          共 {total.toLocaleString()} 条 · 第 {filters.page} / {totalPages} 页
        </span>
      </form>

      {/* Games table */}
      <div className="bg-white rounded-lg shadow overflow-hidden overflow-x-auto">
        <table className="w-full text-sm min-w-[900px]">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                ID
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                中文名
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                英文名
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                开发商
              </th>
              <th className="text-left px-3 py-2 font-medium text-gray-500">
                品类
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                IAA 适配
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                等级
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                平台数
              </th>
              <th className="text-center px-3 py-2 font-medium text-gray-500">
                操作
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={9}
                  className="px-3 py-8 text-center text-gray-400"
                >
                  暂无匹配的游戏
                </td>
              </tr>
            ) : (
              rows.map((g) => (
                <tr key={g.id} className="hover:bg-gray-50">
                  <td className="px-3 py-2 text-xs text-gray-400 font-mono">
                    {g.id}
                  </td>
                  <td className="px-3 py-2 font-medium">{g.nameZh ?? "-"}</td>
                  <td className="px-3 py-2 text-gray-500">
                    {g.nameEn ?? "-"}
                  </td>
                  <td className="px-3 py-2 text-gray-500">
                    {g.developer ?? "-"}
                  </td>
                  <td className="px-3 py-2 text-gray-500">
                    {g.genre ?? "-"}
                  </td>
                  <td className="text-center px-3 py-2 font-mono text-blue-600">
                    {g.iaaSuitability}
                  </td>
                  <td className="text-center px-3 py-2">
                    {g.iaaGrade ? (
                      <span className="text-xs font-bold px-2 py-0.5 rounded bg-blue-100 text-blue-800">
                        {g.iaaGrade}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">-</span>
                    )}
                  </td>
                  <td className="text-center px-3 py-2 font-mono text-gray-500">
                    {g._count.platformListings}
                  </td>
                  <td className="text-center px-3 py-2">
                    <Link
                      href={`/admin/games/${g.id}/edit`}
                      className="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-500"
                    >
                      编辑
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-2">
          {filters.page > 1 && (
            <Link
              href={`/admin/games${buildQs(filters, { page: filters.page - 1 })}`}
              className="text-sm px-3 py-1 rounded bg-white border border-gray-300 hover:bg-gray-50"
            >
              上一页
            </Link>
          )}
          <span className="text-sm text-gray-500">
            第 {filters.page} / {totalPages} 页
          </span>
          {filters.page < totalPages && (
            <Link
              href={`/admin/games${buildQs(filters, { page: filters.page + 1 })}`}
              className="text-sm px-3 py-1 rounded bg-white border border-gray-300 hover:bg-gray-50"
            >
              下一页
            </Link>
          )}
        </div>
      )}
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
