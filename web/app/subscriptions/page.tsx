import { revalidatePath } from "next/cache";
import { auth } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

const DIMENSION_LABELS: Record<string, string> = {
  platform: "平台",
  genre: "类型",
  region: "地区",
  keyword: "关键词",
  game: "游戏",
};

const CHANNEL_LABELS: Record<string, string> = {
  feishu: "飞书",
  wecom: "企业微信",
  email: "邮件",
};

const SCHEDULE_LABELS: Record<string, string> = {
  daily: "每日",
  weekly: "每周",
  realtime: "实时",
};

// ============================================================
// Data loaders
// ============================================================
async function getSubscriptions(userId: string) {
  try {
    return await prisma.subscription.findMany({
      where: { userId },
      orderBy: { createdAt: "desc" },
    });
  } catch {
    return [];
  }
}

// ============================================================
// Server actions
// ============================================================
async function createSubscription(formData: FormData) {
  "use server";
  const session = await auth();
  if (!session?.user?.id) return;

  const dimension = String(formData.get("dimension") || "");
  const value = String(formData.get("value") || "").trim();
  const channel = String(formData.get("channel") || "");
  const scheduleRaw = String(formData.get("schedule") || "daily");
  const webhookUrl = String(formData.get("webhook_url") || "").trim();
  const email = String(formData.get("email") || "").trim();

  if (!dimension || !value || !channel) return;

  const channelConfig: Record<string, string> = {};
  if ((channel === "feishu" || channel === "wecom") && webhookUrl) {
    channelConfig.webhook_url = webhookUrl;
  }
  if (channel === "email" && email) {
    channelConfig.email = email;
  }

  try {
    await prisma.subscription.create({
      data: {
        userId: session.user.id,
        dimension,
        value,
        channel,
        channelConfig,
        schedule: scheduleRaw === "realtime" ? null : scheduleRaw,
      },
    });
    revalidatePath("/subscriptions");
  } catch (e) {
    console.error("createSubscription failed:", e);
  }
}

async function toggleSubscription(formData: FormData) {
  "use server";
  const session = await auth();
  if (!session?.user?.id) return;

  const id = parseInt(String(formData.get("id") || ""));
  const nextActive = String(formData.get("isActive")) === "true";
  if (Number.isNaN(id)) return;

  try {
    const existing = await prisma.subscription.findUnique({ where: { id } });
    if (!existing || existing.userId !== session.user.id) return;
    await prisma.subscription.update({
      where: { id },
      data: { isActive: nextActive },
    });
    revalidatePath("/subscriptions");
  } catch (e) {
    console.error("toggleSubscription failed:", e);
  }
}

async function deleteSubscription(formData: FormData) {
  "use server";
  const session = await auth();
  if (!session?.user?.id) return;

  const id = parseInt(String(formData.get("id") || ""));
  if (Number.isNaN(id)) return;

  try {
    const existing = await prisma.subscription.findUnique({ where: { id } });
    if (!existing || existing.userId !== session.user.id) return;
    await prisma.subscription.delete({ where: { id } });
    revalidatePath("/subscriptions");
  } catch (e) {
    console.error("deleteSubscription failed:", e);
  }
}

// ============================================================
// Page
// ============================================================
export default async function SubscriptionsPage() {
  const session = await auth().catch(() => null);
  const userId = session?.user?.id;

  if (!userId) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500 mb-4">请先登录以管理订阅</p>
        <a
          href="/login"
          className="inline-block bg-gray-900 text-white px-4 py-2 rounded text-sm hover:bg-gray-700"
        >
          前往登录
        </a>
      </div>
    );
  }

  const subs = await getSubscriptions(userId);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">订阅中心</h2>
          <p className="text-sm text-gray-500 mt-1">
            按维度订阅游戏动态，每日自动推送个性化摘要
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Create form */}
        <div className="lg:col-span-1">
          <h3 className="font-semibold text-lg mb-4">新建订阅</h3>
          <div className="bg-white rounded-lg shadow p-5">
            <form action={createSubscription} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  订阅维度
                </label>
                <select
                  name="dimension"
                  required
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                  defaultValue="genre"
                >
                  {Object.entries(DIMENSION_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v} ({k})
                    </option>
                  ))}
                </select>
                <p className="text-xs text-gray-400 mt-1">
                  选择一个维度来过滤关注的游戏
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  值
                </label>
                <input
                  type="text"
                  name="value"
                  required
                  placeholder="如 app_store / idle / US / roguelike / 123"
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  推送渠道
                </label>
                <select
                  name="channel"
                  required
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                  defaultValue="feishu"
                >
                  {Object.entries(CHANNEL_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Webhook URL（飞书/企业微信）
                </label>
                <input
                  type="url"
                  name="webhook_url"
                  placeholder="https://..."
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  邮箱（Email 渠道）
                </label>
                <input
                  type="email"
                  name="email"
                  placeholder="留空则使用账号邮箱"
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  频率
                </label>
                <select
                  name="schedule"
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                  defaultValue="daily"
                >
                  {Object.entries(SCHEDULE_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>

              <button
                type="submit"
                className="w-full bg-gray-900 text-white px-4 py-2 rounded text-sm hover:bg-gray-700"
              >
                创建订阅
              </button>
            </form>
          </div>
        </div>

        {/* Subscriptions list */}
        <div className="lg:col-span-2">
          <h3 className="font-semibold text-lg mb-4">
            我的订阅 ({subs.length})
          </h3>
          <div className="bg-white rounded-lg shadow overflow-hidden overflow-x-auto">
            {subs.length === 0 ? (
              <div className="p-8 text-center text-gray-400 text-sm">
                暂无订阅。使用左侧表单创建你的第一个订阅。
              </div>
            ) : (
              <table className="w-full text-sm min-w-[600px]">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium text-gray-500">
                      维度
                    </th>
                    <th className="text-left px-4 py-2 font-medium text-gray-500">
                      值
                    </th>
                    <th className="text-left px-4 py-2 font-medium text-gray-500">
                      渠道
                    </th>
                    <th className="text-left px-4 py-2 font-medium text-gray-500">
                      频率
                    </th>
                    <th className="text-left px-4 py-2 font-medium text-gray-500">
                      最近发送
                    </th>
                    <th className="text-center px-4 py-2 font-medium text-gray-500">
                      状态
                    </th>
                    <th className="text-right px-4 py-2 font-medium text-gray-500">
                      操作
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {subs.map((sub) => (
                    <tr key={sub.id} className="hover:bg-gray-50">
                      <td className="px-4 py-2">
                        <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
                          {DIMENSION_LABELS[sub.dimension] || sub.dimension}
                        </span>
                      </td>
                      <td className="px-4 py-2 font-mono text-xs text-gray-700">
                        {sub.value}
                      </td>
                      <td className="px-4 py-2 text-gray-700">
                        {CHANNEL_LABELS[sub.channel] || sub.channel}
                      </td>
                      <td className="px-4 py-2 text-gray-600">
                        {sub.schedule
                          ? SCHEDULE_LABELS[sub.schedule] || sub.schedule
                          : "实时"}
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-500">
                        {sub.lastSentAt
                          ? sub.lastSentAt.toLocaleString("zh-CN")
                          : "未发送"}
                      </td>
                      <td className="text-center px-4 py-2">
                        <form action={toggleSubscription} className="inline">
                          <input type="hidden" name="id" value={sub.id} />
                          <input
                            type="hidden"
                            name="isActive"
                            value={String(!sub.isActive)}
                          />
                          <button
                            type="submit"
                            className={`text-xs px-2 py-0.5 rounded cursor-pointer ${
                              sub.isActive
                                ? "bg-green-100 text-green-700 hover:bg-green-200"
                                : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                            }`}
                            title="点击切换状态"
                          >
                            {sub.isActive ? "启用" : "禁用"}
                          </button>
                        </form>
                      </td>
                      <td className="text-right px-4 py-2">
                        <form action={deleteSubscription} className="inline">
                          <input type="hidden" name="id" value={sub.id} />
                          <button
                            type="submit"
                            className="text-xs text-red-600 hover:text-red-800"
                          >
                            删除
                          </button>
                        </form>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Help block */}
          <div className="mt-6 bg-gray-50 rounded-lg p-4 text-xs text-gray-600 space-y-2">
            <p className="font-medium text-gray-700">维度说明：</p>
            <ul className="space-y-1 ml-4 list-disc">
              <li>
                <code className="bg-white px-1 rounded">platform</code>
                ：平台代码，如 app_store / google_play / steam / taptap
              </li>
              <li>
                <code className="bg-white px-1 rounded">genre</code>
                ：游戏类型，如 idle / merge / puzzle / roguelike
              </li>
              <li>
                <code className="bg-white px-1 rounded">region</code>
                ：地区代码，如 CN / US / JP
              </li>
              <li>
                <code className="bg-white px-1 rounded">keyword</code>
                ：名称或标签中包含的关键词
              </li>
              <li>
                <code className="bg-white px-1 rounded">game</code>
                ：单个游戏 ID（订阅该游戏的所有动态）
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
