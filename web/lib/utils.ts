import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatNumber(num: number | null | undefined): string {
  if (num == null) return "-";
  if (num >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(1)}B`;
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return num.toString();
}

export function getRankChangeColor(change: number | null): string {
  if (change == null) return "text-gray-400";
  if (change > 0) return "text-green-500";
  if (change < 0) return "text-red-500";
  return "text-gray-400";
}

export function getRankChangeIcon(change: number | null): string {
  if (change == null) return "NEW";
  if (change > 0) return `▲${change}`;
  if (change < 0) return `▼${Math.abs(change)}`;
  return "—";
}

export function getScoreColor(score: number): string {
  if (score >= 75) return "text-green-600";
  if (score >= 50) return "text-yellow-600";
  return "text-gray-500";
}

export function getScoreBgColor(score: number): string {
  if (score >= 75) return "bg-green-100 text-green-800";
  if (score >= 50) return "bg-yellow-100 text-yellow-800";
  return "bg-gray-100 text-gray-600";
}

export const PLATFORM_LABELS: Record<string, string> = {
  google_play: "Google Play",
  app_store: "App Store",
  taptap: "TapTap",
  steam: "Steam",
  wechat_mini: "微信小游戏",
  poki: "Poki",
  crazygames: "CrazyGames",
  "4399": "4399",
};

export const PLATFORM_COLORS: Record<string, string> = {
  google_play: "#34A853",
  app_store: "#007AFF",
  taptap: "#15C5CE",
  steam: "#1B2838",
  wechat_mini: "#07C160",
  poki: "#FF6B35",
  crazygames: "#6C5CE7",
  "4399": "#FF4444",
};
