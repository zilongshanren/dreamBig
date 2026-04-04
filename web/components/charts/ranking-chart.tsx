"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

const PLATFORM_COLORS: Record<string, string> = {
  google_play: "#34A853",
  app_store: "#007AFF",
  taptap: "#15C5CE",
  steam: "#1B2838",
  wechat_mini: "#07C160",
  poki: "#FF6B35",
  crazygames: "#6C5CE7",
};

const PLATFORM_LABELS: Record<string, string> = {
  google_play: "Google Play",
  app_store: "App Store",
  taptap: "TapTap",
  steam: "Steam",
  wechat_mini: "微信小游戏",
  poki: "Poki",
  crazygames: "CrazyGames",
};

interface RankingChartProps {
  data: { date: string; platform: string; rank: number; chart: string }[];
}

export function RankingChart({ data }: RankingChartProps) {
  // Group data by date, creating one entry per date with platform as keys
  const platforms = [...new Set(data.map((d) => d.platform))];
  const dateMap = new Map<string, Record<string, string | number>>();

  for (const d of data) {
    const entry = dateMap.get(d.date) || { date: d.date };
    entry[d.platform] = d.rank;
    dateMap.set(d.date, entry);
  }

  const chartData = [...dateMap.values()].sort((a, b) =>
    String(a.date).localeCompare(String(b.date))
  );

  return (
    <ResponsiveContainer width="100%" height={250}>
      <LineChart data={chartData}>
        <XAxis
          dataKey="date"
          tick={{ fontSize: 10, fill: "#9ca3af" }}
          tickFormatter={(v) => v.slice(5)} // Show MM-DD
        />
        <YAxis
          reversed
          tick={{ fontSize: 10, fill: "#9ca3af" }}
          domain={["auto", "auto"]}
          label={{
            value: "排名",
            angle: -90,
            position: "insideLeft",
            style: { fontSize: 11, fill: "#9ca3af" },
          }}
        />
        <Tooltip
          contentStyle={{
            fontSize: 12,
            borderRadius: 8,
            border: "1px solid #e5e7eb",
          }}
          formatter={(value, name) => [
            `#${value}`,
            PLATFORM_LABELS[String(name)] || String(name),
          ]}
        />
        <Legend
          formatter={(value) => PLATFORM_LABELS[value] || value}
          wrapperStyle={{ fontSize: 11 }}
        />
        {platforms.map((platform) => (
          <Line
            key={platform}
            type="monotone"
            dataKey={platform}
            stroke={PLATFORM_COLORS[platform] || "#888"}
            strokeWidth={2}
            dot={{ r: 2 }}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
