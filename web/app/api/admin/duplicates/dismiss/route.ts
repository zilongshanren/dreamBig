import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { auth, requirePermission } from "@/lib/auth";
import { getCurrentWorkspaceId } from "@/lib/workspace";

/**
 * POST /api/admin/duplicates/dismiss
 *
 * Body (JSON or form): { gameId1: number, gameId2: number }
 *
 * Marks a pair of games as "not duplicate" by appending the counterpart's
 * id into each game's `metadata.dedup_dismissed` array. The duplicates
 * page query filters out rows where either side already lists the other.
 */
export async function POST(req: NextRequest) {
  try {
    await requirePermission("manage_users");
  } catch {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }

  let gameId1: number | null = null;
  let gameId2: number | null = null;

  const contentType = req.headers.get("content-type") ?? "";
  const isForm = !contentType.includes("application/json");

  try {
    if (isForm) {
      const form = await req.formData();
      gameId1 = Number(form.get("gameId1"));
      gameId2 = Number(form.get("gameId2"));
    } else {
      const body = await req.json();
      gameId1 = Number(body.gameId1);
      gameId2 = Number(body.gameId2);
    }
  } catch {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  if (
    !Number.isFinite(gameId1) ||
    !Number.isFinite(gameId2) ||
    gameId1 === gameId2
  ) {
    return NextResponse.json(
      { error: "Invalid gameId1 / gameId2" },
      { status: 400 },
    );
  }

  try {
    // Use raw SQL to append to the dedup_dismissed JSONB array without
    // fetching + mutating, and without duplicating ids.
    await prisma.$transaction([
      prisma.$executeRawUnsafe(
        `UPDATE games
         SET metadata = jsonb_set(
           COALESCE(metadata, '{}'::jsonb),
           '{dedup_dismissed}',
           (
             CASE
               WHEN COALESCE((metadata->'dedup_dismissed')::jsonb, '[]'::jsonb) @> to_jsonb($2::int)
                 THEN COALESCE((metadata->'dedup_dismissed')::jsonb, '[]'::jsonb)
               ELSE COALESCE((metadata->'dedup_dismissed')::jsonb, '[]'::jsonb) || to_jsonb($2::int)
             END
           ),
           true
         )
         WHERE id = $1`,
        gameId1,
        gameId2,
      ),
      prisma.$executeRawUnsafe(
        `UPDATE games
         SET metadata = jsonb_set(
           COALESCE(metadata, '{}'::jsonb),
           '{dedup_dismissed}',
           (
             CASE
               WHEN COALESCE((metadata->'dedup_dismissed')::jsonb, '[]'::jsonb) @> to_jsonb($2::int)
                 THEN COALESCE((metadata->'dedup_dismissed')::jsonb, '[]'::jsonb)
               ELSE COALESCE((metadata->'dedup_dismissed')::jsonb, '[]'::jsonb) || to_jsonb($2::int)
             END
           ),
           true
         )
         WHERE id = $1`,
        gameId2,
        gameId1,
      ),
    ]);

    // Audit log (best effort, raw SQL to bypass possibly-stale Prisma
    // client types for audit_logs.workspace_id)
    try {
      const session = await auth();
      const workspaceId = await getCurrentWorkspaceId();
      await prisma.$executeRawUnsafe(
        `INSERT INTO audit_logs (user_id, workspace_id, action, resource, diff)
         VALUES ($1, $2, $3, $4, $5::jsonb)`,
        session?.user?.id ?? null,
        workspaceId,
        "dismiss_duplicate_pair",
        `game:${gameId1}:${gameId2}`,
        JSON.stringify({ gameId1, gameId2 }),
      );
    } catch {
      // ignore audit failures
    }

    if (isForm) {
      return NextResponse.redirect(
        new URL("/admin/duplicates", req.url),
        303,
      );
    }
    return NextResponse.json({ success: true });
  } catch (error) {
    if (isForm) {
      return NextResponse.redirect(
        new URL("/admin/duplicates", req.url),
        303,
      );
    }
    return NextResponse.json(
      { error: "Failed to dismiss pair", detail: String(error) },
      { status: 500 },
    );
  }
}
