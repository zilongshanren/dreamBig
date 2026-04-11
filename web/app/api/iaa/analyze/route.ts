import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

/**
 * POST /api/iaa/analyze
 *
 * Trigger generation of an IAA analysis report for a game.
 *
 * Accepts JSON `{ gameId: number }` OR HTML form `gameId` field (for the
 * "立即生成" button on the detail page).
 *
 * Behavior:
 *  - Validates input; returns 400 if gameId missing/invalid.
 *  - If the game already has a GameReport < 24h old, returns early with
 *    `{ status: "recent" }`.
 *  - Otherwise inserts a row into `scrape_jobs` with
 *    `platform='internal', jobType='report_generation'` as a durable queue
 *    placeholder. The worker/scheduler (to be wired in Wave 3) will pick
 *    these rows up and invoke the Poe-based report generator.
 *
 * TODO(wave3-integration): replace scrape_jobs placeholder with a proper
 * job queue (e.g. Redis stream or a dedicated `report_generation_queue`
 * table) and wire the worker loop to consume it.
 */
export async function POST(req: NextRequest) {
  let gameId: number | null = null;
  let url: string | null = null;

  const contentType = req.headers.get("content-type") ?? "";

  try {
    if (contentType.includes("application/json")) {
      const body = await req.json();
      if (body?.gameId != null) gameId = Number(body.gameId);
      if (typeof body?.url === "string") url = body.url;
    } else {
      // form submission (from the detail page fallback button)
      const form = await req.formData();
      const raw = form.get("gameId");
      if (raw != null) gameId = Number(raw);
      const rawUrl = form.get("url");
      if (typeof rawUrl === "string") url = rawUrl;
    }
  } catch {
    return NextResponse.json(
      { error: "Invalid request body" },
      { status: 400 },
    );
  }

  if ((!gameId || !Number.isFinite(gameId)) && !url) {
    return NextResponse.json(
      { error: "gameId (number) or url (string) required" },
      { status: 400 },
    );
  }

  // If we got only a url, we cannot resolve a gameId in this stub.
  // Queue the job with the url for the worker to resolve later.
  if (!gameId && url) {
    try {
      const job = await prisma.scrapeJob.create({
        data: {
          platform: "internal",
          jobType: "report_generation",
          status: "pending",
          errorMessage: JSON.stringify({ url }),
        },
      });
      return NextResponse.json({ status: "queued", jobId: job.id, url });
    } catch {
      return NextResponse.json(
        { error: "Failed to enqueue job" },
        { status: 500 },
      );
    }
  }

  // Normal path: we have a gameId
  try {
    // Check the game exists
    const game = await prisma.game.findUnique({
      where: { id: gameId! },
      select: { id: true },
    });
    if (!game) {
      return NextResponse.json({ error: "Game not found" }, { status: 404 });
    }

    // Check existing recent report (<24h)
    const existing = await prisma.gameReport.findUnique({
      where: { gameId: gameId! },
    });
    if (
      existing &&
      Date.now() - existing.generatedAt.getTime() < 24 * 3600 * 1000
    ) {
      return NextResponse.json({
        status: "recent",
        message: "已有最新报告",
        gameId,
      });
    }

    // Enqueue via scrape_jobs placeholder. The worker loop
    // (poll_internal_jobs) picks up rows where platform='internal' AND
    // status='pending' every 5 minutes.
    const job = await prisma.scrapeJob.create({
      data: {
        platform: "internal",
        jobType: "report_generation",
        status: "pending",
        errorMessage: JSON.stringify({ gameId }),
      },
    });

    return NextResponse.json({
      status: "queued",
      jobId: job.id,
      gameId,
      message: "报告生成已排队，请稍后刷新",
    });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to enqueue analysis job" },
      { status: 500 },
    );
  }
}
