import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET() {
  try {
    const today = new Date(new Date().toISOString().split("T")[0]);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);

    const [gameCount, highPotentialCount, todayAlerts, scraperHealth] =
      await Promise.all([
        prisma.game.count(),
        prisma.potentialScore.count({
          where: { scoredAt: today, overallScore: { gte: 75 } },
        }),
        prisma.alertEvent.count({
          where: { triggeredAt: { gte: yesterday } },
        }),
        prisma.scrapeJob.findMany({
          orderBy: { startedAt: "desc" },
          take: 10,
          distinct: ["platform"],
          select: {
            platform: true,
            status: true,
            itemsScraped: true,
            startedAt: true,
          },
        }),
      ]);

    return NextResponse.json({
      gameCount,
      highPotentialCount,
      todayAlerts,
      scraperHealth,
    });
  } catch (error) {
    return NextResponse.json({ error: "Failed to fetch dashboard data" }, { status: 500 });
  }
}
