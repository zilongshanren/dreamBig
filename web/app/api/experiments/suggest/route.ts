import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

/**
 * POST /api/experiments/suggest
 *
 * Enqueue an LLM-backed experiment suggestion job for a given game. The
 * worker (wired by the integration agent) consumes scrape_jobs rows where
 * jobType='experiment_suggest' and calls ExperimentAdvisor.
 *
 * Body: { gameId: number }
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const gameIdRaw = body?.gameId;
    const gameId = gameIdRaw != null ? parseInt(String(gameIdRaw)) : NaN;

    if (!Number.isFinite(gameId)) {
      return NextResponse.json(
        { error: "gameId (number) required" },
        { status: 400 },
      );
    }

    // Verify the game exists before enqueueing
    const game = await prisma.game.findUnique({
      where: { id: gameId },
      select: { id: true },
    });
    if (!game) {
      return NextResponse.json({ error: "Game not found" }, { status: 404 });
    }

    // Queue via scrape_jobs placeholder (same pattern as /api/iaa/analyze)
    const job = await prisma.scrapeJob.create({
      data: {
        platform: "internal",
        jobType: "experiment_suggest",
        status: "pending",
        errorMessage: JSON.stringify({ gameId }),
      },
    });

    return NextResponse.json({
      status: "queued",
      jobId: job.id,
      gameId,
      message: "实验建议生成已排队，请稍后查看",
    });
  } catch (error) {
    console.error("POST /api/experiments/suggest failed:", error);
    return NextResponse.json(
      { error: "Failed to enqueue suggestion job" },
      { status: 500 },
    );
  }
}
