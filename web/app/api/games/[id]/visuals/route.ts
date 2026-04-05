import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const gameId = parseInt(id);
  if (!gameId) return NextResponse.json([], { status: 400 });

  try {
    const analyses = await prisma.gameAssetAnalysis.findMany({
      where: { gameId },
      orderBy: { analyzedAt: "desc" },
    });
    // Serialize decimals
    const serialized = analyses.map((a) => ({
      ...a,
      confidence: a.confidence ? Number(a.confidence) : null,
      costUsd: a.costUsd ? Number(a.costUsd) : null,
      analyzedAt: a.analyzedAt.toISOString(),
    }));
    return NextResponse.json(serialized);
  } catch (e) {
    console.error("Visual analysis query failed", e);
    return NextResponse.json([], { status: 200 });
  }
}
