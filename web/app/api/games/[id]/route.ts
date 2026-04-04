import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const gameId = parseInt(id);

    const game = await prisma.game.findUnique({
      where: { id: gameId },
      include: {
        platformListings: {
          include: {
            rankingSnapshots: {
              orderBy: { snapshotDate: "desc" },
              take: 60,
            },
          },
        },
        potentialScores: {
          orderBy: { scoredAt: "desc" },
          take: 30,
        },
        socialSignals: {
          orderBy: { signalDate: "desc" },
          take: 30,
        },
        adIntelligence: {
          orderBy: { signalDate: "desc" },
          take: 14,
        },
        gameTags: true,
      },
    });

    if (!game) {
      return NextResponse.json({ error: "Game not found" }, { status: 404 });
    }

    return NextResponse.json(game);
  } catch (error) {
    return NextResponse.json({ error: "Failed to fetch game" }, { status: 500 });
  }
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const gameId = parseInt(id);
    const body = await req.json();

    // Support adding/removing tags
    if (body.addTag) {
      await prisma.gameTag.upsert({
        where: { gameId_tag: { gameId, tag: body.addTag } },
        create: { gameId, tag: body.addTag, note: body.note },
        update: { note: body.note },
      });
    }

    if (body.removeTag) {
      await prisma.gameTag.deleteMany({
        where: { gameId, tag: body.removeTag },
      });
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    return NextResponse.json({ error: "Failed to update game" }, { status: 500 });
  }
}
