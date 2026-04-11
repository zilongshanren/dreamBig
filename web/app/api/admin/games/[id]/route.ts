import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { auth, requirePermission } from "@/lib/auth";
import { getCurrentWorkspaceId } from "@/lib/workspace";

const ALLOWED_GRADES = new Set(["S", "A", "B", "C", "D"]);

type PatchBody = {
  nameZh?: string | null;
  nameEn?: string | null;
  developer?: string | null;
  genre?: string | null;
  iaaSuitability?: number;
  iaaGrade?: string | null;
  gameplayTags?: string[];
  positioning?: string | null;
  coreLoop?: string | null;
};

/**
 * GET /api/admin/games/[id]
 * Returns full game master data with platform listings and latest score.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    await requirePermission("manage_users");
  } catch {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }

  const { id } = await params;
  const gameId = parseInt(id, 10);
  if (!Number.isFinite(gameId)) {
    return NextResponse.json({ error: "Invalid id" }, { status: 400 });
  }

  try {
    const game = await prisma.game.findUnique({
      where: { id: gameId },
      include: {
        platformListings: true,
        potentialScores: {
          orderBy: { scoredAt: "desc" },
          take: 1,
        },
      },
    });
    if (!game) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    return NextResponse.json({
      game,
      latestScore: game.potentialScores[0] ?? null,
    });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch game", detail: String(error) },
      { status: 500 },
    );
  }
}

/**
 * PATCH /api/admin/games/[id]
 * Updates editable fields and writes an audit_log row.
 */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    await requirePermission("manage_users");
  } catch {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }

  const { id } = await params;
  const gameId = parseInt(id, 10);
  if (!Number.isFinite(gameId)) {
    return NextResponse.json({ error: "Invalid id" }, { status: 400 });
  }

  let body: PatchBody;
  try {
    body = (await req.json()) as PatchBody;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  // Validate iaaSuitability
  if (body.iaaSuitability != null) {
    const n = Number(body.iaaSuitability);
    if (!Number.isFinite(n) || n < 0 || n > 100) {
      return NextResponse.json(
        { error: "iaaSuitability must be a number in [0, 100]" },
        { status: 400 },
      );
    }
    body.iaaSuitability = Math.round(n);
  }

  // Validate iaaGrade
  if (body.iaaGrade != null && body.iaaGrade !== "") {
    if (!ALLOWED_GRADES.has(body.iaaGrade)) {
      return NextResponse.json(
        { error: "iaaGrade must be one of S/A/B/C/D" },
        { status: 400 },
      );
    }
  } else if (body.iaaGrade === "") {
    body.iaaGrade = null;
  }

  try {
    const existing = await prisma.game.findUnique({
      where: { id: gameId },
    });
    if (!existing) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }

    // Compute diff for audit (JSON-safe plain object)
    const diff: Record<string, unknown> = {};
    const editableKeys: Array<keyof PatchBody> = [
      "nameZh",
      "nameEn",
      "developer",
      "genre",
      "iaaSuitability",
      "iaaGrade",
      "gameplayTags",
      "positioning",
      "coreLoop",
    ];
    for (const k of editableKeys) {
      if (!(k in body)) continue;
      const before = (existing as unknown as Record<string, unknown>)[k] ?? null;
      const after =
        (body as unknown as Record<string, unknown>)[k] ?? null;
      const beforeStr = JSON.stringify(before);
      const afterStr = JSON.stringify(after);
      if (beforeStr !== afterStr) {
        diff[k] = { before: before ?? null, after: after ?? null };
      }
    }

    const updated = await prisma.game.update({
      where: { id: gameId },
      data: {
        ...(body.nameZh !== undefined ? { nameZh: body.nameZh } : {}),
        ...(body.nameEn !== undefined ? { nameEn: body.nameEn } : {}),
        ...(body.developer !== undefined ? { developer: body.developer } : {}),
        ...(body.genre !== undefined ? { genre: body.genre } : {}),
        ...(body.iaaSuitability !== undefined
          ? { iaaSuitability: body.iaaSuitability }
          : {}),
        ...(body.iaaGrade !== undefined ? { iaaGrade: body.iaaGrade } : {}),
        ...(body.gameplayTags !== undefined
          ? { gameplayTags: body.gameplayTags }
          : {}),
        ...(body.positioning !== undefined
          ? { positioning: body.positioning }
          : {}),
        ...(body.coreLoop !== undefined ? { coreLoop: body.coreLoop } : {}),
      },
    });

    // Audit log (best effort, raw SQL to bypass possibly-stale Prisma
    // client types for audit_logs.workspace_id)
    try {
      if (Object.keys(diff).length > 0) {
        const session = await auth();
        const workspaceId = await getCurrentWorkspaceId();
        await prisma.$executeRawUnsafe(
          `INSERT INTO audit_logs (user_id, workspace_id, action, resource, diff)
           VALUES ($1, $2, $3, $4, $5::jsonb)`,
          session?.user?.id ?? null,
          workspaceId,
          "update_game",
          `game:${gameId}`,
          JSON.stringify(diff),
        );
      }
    } catch {
      // ignore audit failures
    }

    return NextResponse.json({ success: true, game: updated });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to update game", detail: String(error) },
      { status: 500 },
    );
  }
}
