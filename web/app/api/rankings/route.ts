import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl;
    const platform = searchParams.get("platform");
    const chartType = searchParams.get("chart");
    const region = searchParams.get("region");
    const limit = Math.min(parseInt(searchParams.get("limit") || "200"), 500);

    const where: any = {};
    if (platform) where.platformListing = { platform };
    if (chartType) where.chartType = chartType;
    if (region) where.region = region;

    // Get the latest snapshot date
    const latest = await prisma.rankingSnapshot.findFirst({
      orderBy: { snapshotDate: "desc" },
      select: { snapshotDate: true },
      where,
    });

    if (!latest) {
      return NextResponse.json({ rankings: [], date: null });
    }

    const rankings = await prisma.rankingSnapshot.findMany({
      where: { ...where, snapshotDate: latest.snapshotDate },
      orderBy: { rankPosition: "asc" },
      take: limit,
      include: {
        platformListing: {
          include: {
            game: {
              select: {
                id: true,
                nameZh: true,
                nameEn: true,
                genre: true,
                iaaSuitability: true,
              },
            },
          },
        },
      },
    });

    return NextResponse.json({
      rankings,
      date: latest.snapshotDate.toISOString().split("T")[0],
    });
  } catch (error) {
    return NextResponse.json({ error: "Failed to fetch rankings" }, { status: 500 });
  }
}
