import { auth, signOut } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

export const dynamic = "force-dynamic";

const ROLE_LABELS: Record<string, string> = {
  super_admin: "超级管理员",
  analyst: "分析师",
  publisher: "发行商",
  monetization: "变现专员",
  viewer: "观察员",
};

const ROLE_BADGE_CLASSES: Record<string, string> = {
  super_admin: "bg-red-100 text-red-800",
  analyst: "bg-blue-100 text-blue-800",
  publisher: "bg-green-100 text-green-800",
  monetization: "bg-purple-100 text-purple-800",
  viewer: "bg-gray-100 text-gray-800",
};

function formatDate(date: Date | null): string {
  if (!date) return "-";
  return new Date(date).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function updateName(formData: FormData) {
  "use server";
  const session = await auth();
  if (!session?.user?.id) redirect("/login");
  const name = String(formData.get("name") || "").trim().slice(0, 100);
  await prisma.user.update({
    where: { id: session.user.id },
    data: { name: name || null },
  });
  revalidatePath("/account");
}

async function handleSignOut() {
  "use server";
  await signOut({ redirectTo: "/login" });
}

export default async function AccountPage() {
  const session = await auth();
  if (!session?.user?.id) redirect("/login");

  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
  });
  if (!user) redirect("/login");

  const roleLabel = ROLE_LABELS[user.role] || user.role;
  const badgeClass =
    ROLE_BADGE_CLASSES[user.role] || "bg-gray-100 text-gray-800";

  return (
    <div>
      <h2 className="text-2xl font-bold mb-6">账户设置</h2>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* User Info Card */}
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="font-semibold text-lg mb-4">账户信息</h3>
          <dl className="space-y-3">
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <dt className="text-sm text-gray-500">邮箱</dt>
              <dd className="text-sm font-medium text-gray-900">
                {user.email}
              </dd>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <dt className="text-sm text-gray-500">姓名</dt>
              <dd className="text-sm font-medium text-gray-900">
                {user.name || "-"}
              </dd>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <dt className="text-sm text-gray-500">角色</dt>
              <dd>
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded ${badgeClass}`}
                >
                  {roleLabel}
                </span>
              </dd>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <dt className="text-sm text-gray-500">状态</dt>
              <dd>
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded ${
                    user.isActive
                      ? "bg-green-100 text-green-800"
                      : "bg-gray-100 text-gray-600"
                  }`}
                >
                  {user.isActive ? "已启用" : "已停用"}
                </span>
              </dd>
            </div>
            <div className="flex justify-between items-center py-2 border-b border-gray-100">
              <dt className="text-sm text-gray-500">注册时间</dt>
              <dd className="text-sm text-gray-900">
                {formatDate(user.createdAt)}
              </dd>
            </div>
            <div className="flex justify-between items-center py-2">
              <dt className="text-sm text-gray-500">最近登录</dt>
              <dd className="text-sm text-gray-900">
                {formatDate(user.lastLoginAt)}
              </dd>
            </div>
          </dl>
        </div>

        {/* Update Name Form */}
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="font-semibold text-lg mb-4">更新资料</h3>
          <form action={updateName} className="space-y-4">
            <div>
              <label
                htmlFor="name"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                姓名
              </label>
              <input
                id="name"
                name="name"
                type="text"
                defaultValue={user.name || ""}
                maxLength={100}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
            <button
              type="submit"
              className="bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-md px-4 py-2 text-sm transition-colors"
            >
              保存
            </button>
          </form>

          <div className="mt-8 pt-6 border-t border-gray-200">
            <h4 className="text-sm font-semibold text-gray-700 mb-3">
              会话操作
            </h4>
            <form action={handleSignOut}>
              <button
                type="submit"
                className="bg-gray-100 hover:bg-gray-200 text-gray-800 font-medium rounded-md px-4 py-2 text-sm transition-colors"
              >
                退出登录
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
