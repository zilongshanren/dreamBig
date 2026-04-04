import Link from "next/link";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

async function getAlerts() {
  try {
    return await prisma.alert.findMany({
      orderBy: { createdAt: "desc" },
      include: {
        _count: { select: { alertEvents: true } },
      },
    });
  } catch {
    return [];
  }
}

async function getRecentEvents() {
  try {
    return await prisma.alertEvent.findMany({
      orderBy: { triggeredAt: "desc" },
      take: 50,
      include: {
        game: true,
        alert: true,
      },
    });
  } catch {
    return [];
  }
}

export default async function AlertsPage() {
  const [alerts, events] = await Promise.all([
    getAlerts(),
    getRecentEvents(),
  ]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">告警管理</h2>
        <Link
          href="/api/alerts/create"
          className="bg-gray-900 text-white px-4 py-1.5 rounded text-sm hover:bg-gray-700"
        >
          + 新建规则
        </Link>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Alert Rules */}
        <div className="lg:col-span-1">
          <h3 className="font-semibold text-lg mb-4">告警规则</h3>
          {alerts.length === 0 ? (
            <div className="bg-white rounded-lg shadow p-4">
              <p className="text-gray-400 text-sm">
                暂无告警规则。创建你的第一个规则来开始监控。
              </p>
              <div className="mt-4 p-3 bg-gray-50 rounded text-xs text-gray-500">
                <p className="font-medium mb-2">示例规则：</p>
                <p>名称: 高潜力休闲游戏</p>
                <p>条件: 评分 &ge; 70, 类型 = puzzle/idle/merge</p>
                <p>通知: 飞书 Webhook</p>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {alerts.map((alert) => {
                const conds =
                  typeof alert.conditions === "string"
                    ? JSON.parse(alert.conditions)
                    : alert.conditions;

                return (
                  <div
                    key={alert.id}
                    className="bg-white rounded-lg shadow p-4"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="font-medium">{alert.name}</h4>
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          alert.isActive
                            ? "bg-green-100 text-green-700"
                            : "bg-gray-100 text-gray-500"
                        }`}
                      >
                        {alert.isActive ? "启用" : "禁用"}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 space-y-1">
                      {(conds as any).min_score && (
                        <p>最低分: {(conds as any).min_score}</p>
                      )}
                      {(conds as any).genres && (
                        <p>类型: {(conds as any).genres.join(", ")}</p>
                      )}
                      <p>
                        已触发: {(alert as any)._count.alertEvents} 次
                      </p>
                      <p>冷却: {alert.cooldownHours}h</p>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Alert Events Timeline */}
        <div className="lg:col-span-2">
          <h3 className="font-semibold text-lg mb-4">告警历史</h3>
          <div className="bg-white rounded-lg shadow overflow-hidden overflow-x-auto">
            <table className="w-full text-sm min-w-[500px]">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-left px-4 py-2 font-medium text-gray-500">
                    时间
                  </th>
                  <th className="text-left px-4 py-2 font-medium text-gray-500">
                    规则
                  </th>
                  <th className="text-left px-4 py-2 font-medium text-gray-500">
                    游戏
                  </th>
                  <th className="text-center px-4 py-2 font-medium text-gray-500">
                    评分
                  </th>
                  <th className="text-center px-4 py-2 font-medium text-gray-500">
                    状态
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {events.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-4 py-8 text-center text-gray-400"
                    >
                      暂无告警记录
                    </td>
                  </tr>
                ) : (
                  events.map((e) => (
                    <tr key={e.id} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-xs text-gray-500">
                        {e.triggeredAt.toLocaleString("zh-CN")}
                      </td>
                      <td className="px-4 py-2">
                        <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
                          {e.alert.name}
                        </span>
                      </td>
                      <td className="px-4 py-2">
                        <Link
                          href={`/games/${e.gameId}`}
                          className="text-blue-600 hover:underline"
                        >
                          {e.game.nameZh || e.game.nameEn || "Unknown"}
                        </Link>
                      </td>
                      <td className="text-center px-4 py-2">
                        <span
                          className={`font-bold ${
                            (e.score || 0) >= 75
                              ? "text-green-600"
                              : "text-yellow-600"
                          }`}
                        >
                          {e.score}
                        </span>
                      </td>
                      <td className="text-center px-4 py-2">
                        <span
                          className={`text-xs px-2 py-0.5 rounded ${
                            e.acknowledged
                              ? "bg-gray-100 text-gray-500"
                              : "bg-orange-100 text-orange-700"
                          }`}
                        >
                          {e.acknowledged ? "已处理" : "待处理"}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
