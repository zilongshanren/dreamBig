import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { MobileNav } from "@/components/mobile-nav";

export const metadata: Metadata = {
  title: "DreamBig - 游戏榜单监控",
  description: "多平台游戏榜单监控与 IAA 爆品发现平台",
};

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: "📊" },
  { href: "/games", label: "游戏库", icon: "🎮" },
  { href: "/rankings", label: "排行榜", icon: "🏆" },
  { href: "/trending", label: "趋势", icon: "📈" },
  { href: "/alerts", label: "告警", icon: "🔔" },
  { href: "/admin", label: "管理", icon: "⚙️" },
];

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="h-full">
      <body className="h-full bg-gray-50 text-gray-900 antialiased" style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif' }}>
        <div className="flex h-full">
          {/* Desktop Sidebar */}
          <aside className="hidden md:flex w-56 bg-gray-900 text-white flex-col shrink-0">
            <div className="p-4 border-b border-gray-700">
              <h1 className="text-xl font-bold tracking-tight">DreamBig</h1>
              <p className="text-xs text-gray-400 mt-1">IAA 爆品发现平台</p>
            </div>
            <nav className="flex-1 py-4">
              {NAV_ITEMS.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-300 hover:bg-gray-800 hover:text-white transition-colors"
                >
                  <span>{item.icon}</span>
                  <span>{item.label}</span>
                </Link>
              ))}
            </nav>
            <div className="p-4 border-t border-gray-700 text-xs text-gray-500">
              v0.1.0
            </div>
          </aside>

          {/* Mobile Header + Nav */}
          <MobileNav items={NAV_ITEMS} />

          {/* Main content */}
          <main className="flex-1 overflow-auto pt-14 md:pt-0">
            <div className="p-4 md:p-6 max-w-7xl mx-auto">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
