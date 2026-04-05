import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

/**
 * GET /api/experiments
 *
 * List experiments with optional filters.
 * Query params: gameId, status
 */
export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const gameId = searchParams.get("gameId");
    const status = searchParams.get("status");

    const where: Record<string, unknown> = {};
    if (gameId) where.gameId = parseInt(gameId);
    if (status) where.status = status;

    const experiments = await prisma.experiment.findMany({
      where,
      orderBy: [{ priority: "asc" }, { createdAt: "desc" }],
      take: 100,
      include: {
        game: { select: { nameZh: true, nameEn: true } },
      },
    });

    return NextResponse.json(experiments);
  } catch (error) {
    console.error("GET /api/experiments failed:", error);
    return NextResponse.json(
      { error: "Failed to fetch experiments" },
      { status: 500 },
    );
  }
}

/**
 * POST /api/experiments
 *
 * Create a new experiment.
 * Required: gameId, name, hypothesis, successMetric
 * Optional: variantA, variantB, sampleSize, priority, expectedLift, status, notes
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();

    // Validate required fields
    if (!body.gameId || !body.name || !body.hypothesis || !body.successMetric) {
      return NextResponse.json(
        {
          error:
            "Missing required fields: gameId, name, hypothesis, successMetric",
        },
        { status: 400 },
      );
    }

    const gameId = parseInt(String(body.gameId));
    if (!Number.isFinite(gameId)) {
      return NextResponse.json(
        { error: "gameId must be a valid number" },
        { status: 400 },
      );
    }

    const exp = await prisma.experiment.create({
      data: {
        gameId,
        name: String(body.name),
        hypothesis: String(body.hypothesis),
        variantA: body.variantA ?? {},
        variantB: body.variantB ?? {},
        successMetric: String(body.successMetric),
        sampleSize:
          body.sampleSize != null ? parseInt(String(body.sampleSize)) : null,
        priority:
          body.priority != null ? parseInt(String(body.priority)) : 3,
        expectedLift:
          body.expectedLift !== undefined && body.expectedLift !== null
            ? Number(body.expectedLift)
            : null,
        status: body.status ? String(body.status) : "draft",
        notes: body.notes ? String(body.notes) : null,
      },
    });

    return NextResponse.json(exp, { status: 201 });
  } catch (error) {
    console.error("POST /api/experiments failed:", error);
    return NextResponse.json(
      { error: "Failed to create experiment" },
      { status: 500 },
    );
  }
}
