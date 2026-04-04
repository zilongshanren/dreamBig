import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET() {
  try {
    const [stats, recentJobs, platformCoverage] = await Promise.all([
      Promise.all([
        prisma.game.count(),
        prisma.platformListing.count(),
        prisma.rankingSnapshot.count(),
        prisma.socialSignal.count(),
        prisma.adIntelligence.count(),
        prisma.potentialScore.count(),
      ]).then(([games, listings, snapshots, signals, ads, scores]) => ({
        games,
        listings,
        snapshots,
        signals,
        ads,
        scores,
      })),
      prisma.scrapeJob.findMany({
        orderBy: { startedAt: "desc" },
        take: 50,
      }),
      prisma.platformListing.groupBy({
        by: ["platform"],
        _count: true,
      }),
    ]);

    return NextResponse.json({ stats, recentJobs, platformCoverage });
  } catch (error) {
    return NextResponse.json({ error: "Failed to fetch admin data" }, { status: 500 });
  }
}
