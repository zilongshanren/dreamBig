import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl;
    const page = parseInt(searchParams.get("page") || "1");
    const limit = Math.min(parseInt(searchParams.get("limit") || "50"), 100);
    const genre = searchParams.get("genre");
    const platform = searchParams.get("platform");
    const minScore = parseInt(searchParams.get("minScore") || "0");
    const search = searchParams.get("search");
    const sort = searchParams.get("sort") || "updatedAt";
    const order = searchParams.get("order") || "desc";

    const where: any = {};
    if (genre) where.genre = genre;
    if (search) {
      where.OR = [
        { nameZh: { contains: search, mode: "insensitive" } },
        { nameEn: { contains: search, mode: "insensitive" } },
      ];
    }
    if (platform) {
      where.platformListings = { some: { platform } };
    }
    if (minScore > 0) {
      where.potentialScores = { some: { overallScore: { gte: minScore } } };
    }

    const [games, total] = await Promise.all([
      prisma.game.findMany({
        where,
        include: {
          platformListings: {
            select: { platform: true, rating: true, ratingCount: true },
          },
          potentialScores: {
            orderBy: { scoredAt: "desc" },
            take: 1,
          },
        },
        orderBy: { [sort]: order },
        take: limit,
        skip: (page - 1) * limit,
      }),
      prisma.game.count({ where }),
    ]);

    return NextResponse.json({
      games,
      total,
      page,
      totalPages: Math.ceil(total / limit),
    });
  } catch (error) {
    return NextResponse.json({ error: "Failed to fetch games" }, { status: 500 });
  }
}
