import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { auth, requirePermission } from "@/lib/auth";
import { getCurrentWorkspaceId } from "@/lib/workspace";

/**
 * POST /api/admin/duplicates/merge
 *
 * Body (JSON or form): { mergeFromId: number, mergeIntoId: number }
 *
 * Reparents all related rows from mergeFromId to mergeIntoId inside a
 * transaction, then deletes mergeFromId. Writes an audit_log row on
 * success. For records with (gameId, ...) unique constraints that would
 * conflict (e.g. potential_scores), we delete the mergeFromId side.
 */
export async function POST(req: NextRequest) {
  try {
    await requirePermission("manage_users");
  } catch {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }

  let mergeFromId: number | null = null;
  let mergeIntoId: number | null = null;

  const contentType = req.headers.get("content-type") ?? "";
  const isForm = !contentType.includes("application/json");

  try {
    if (isForm) {
      const form = await req.formData();
      mergeFromId = Number(form.get("mergeFromId"));
      mergeIntoId = Number(form.get("mergeIntoId"));
    } else {
      const body = await req.json();
      mergeFromId = Number(body.mergeFromId);
      mergeIntoId = Number(body.mergeIntoId);
    }
  } catch {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  if (
    !Number.isFinite(mergeFromId) ||
    !Number.isFinite(mergeIntoId) ||
    mergeFromId === mergeIntoId
  ) {
    return NextResponse.json(
      { error: "Invalid mergeFromId / mergeIntoId" },
      { status: 400 },
    );
  }

  try {
    const [from, into] = await Promise.all([
      prisma.game.findUnique({ where: { id: mergeFromId } }),
      prisma.game.findUnique({ where: { id: mergeIntoId } }),
    ]);
    if (!from || !into) {
      return NextResponse.json({ error: "Game not found" }, { status: 404 });
    }

    await prisma.$transaction(async (tx) => {
      // 1. Reparent platform listings
      await tx.platformListing.updateMany({
        where: { gameId: mergeFromId! },
        data: { gameId: mergeIntoId! },
      });
      // 2. Social signals — has @@unique([gameId, platform, signalDate])
      //    Delete conflicting rows on the target side first, then reparent.
      await tx.$executeRawUnsafe(
        `DELETE FROM social_signals
         WHERE game_id = $1
           AND (platform, signal_date) IN (
             SELECT platform, signal_date FROM social_signals WHERE game_id = $2
           )`,
        mergeIntoId,
        mergeFromId,
      );
      await tx.socialSignal.updateMany({
        where: { gameId: mergeFromId! },
        data: { gameId: mergeIntoId! },
      });
      // 3. Ad intelligence — has @@unique([gameId, source, signalDate])
      await tx.$executeRawUnsafe(
        `DELETE FROM ad_intelligence
         WHERE game_id = $1
           AND (source, signal_date) IN (
             SELECT source, signal_date FROM ad_intelligence WHERE game_id = $2
           )`,
        mergeIntoId,
        mergeFromId,
      );
      await tx.adIntelligence.updateMany({
        where: { gameId: mergeFromId! },
        data: { gameId: mergeIntoId! },
      });
      // 4. Potential scores — has @@unique([gameId, scoredAt]); just drop
      //    the mergeFromId side to avoid conflicts.
      await tx.potentialScore.deleteMany({
        where: { gameId: mergeFromId! },
      });
      // 5. Alert events
      await tx.alertEvent.updateMany({
        where: { gameId: mergeFromId! },
        data: { gameId: mergeIntoId! },
      });
      // 6. Delete rows that are easier to recompute than merge
      await tx.gameTag.deleteMany({ where: { gameId: mergeFromId! } });
      await tx.reviewTopicSummary.deleteMany({
        where: { gameId: mergeFromId! },
      });
      await tx.gameReport.deleteMany({ where: { gameId: mergeFromId! } });
      await tx.gameEmbedding.deleteMany({ where: { gameId: mergeFromId! } });
      await tx.socialContentSample.deleteMany({
        where: { gameId: mergeFromId! },
      });
      await tx.experiment.deleteMany({ where: { gameId: mergeFromId! } });
      await tx.gameAssetAnalysis.deleteMany({
        where: { gameId: mergeFromId! },
      });
      // 7. Delete the source game
      await tx.game.delete({ where: { id: mergeFromId! } });
    });

    // Audit log (best effort, raw SQL to bypass possibly-stale Prisma
    // client types for audit_logs.workspace_id)
    try {
      const session = await auth();
      const workspaceId = await getCurrentWorkspaceId();
      const diffJson = JSON.stringify({
        mergeFromId,
        mergeIntoId,
        from: {
          nameZh: from.nameZh,
          nameEn: from.nameEn,
          developer: from.developer,
        },
        into: {
          nameZh: into.nameZh,
          nameEn: into.nameEn,
          developer: into.developer,
        },
      });
      await prisma.$executeRawUnsafe(
        `INSERT INTO audit_logs (user_id, workspace_id, action, resource, diff)
         VALUES ($1, $2, $3, $4, $5::jsonb)`,
        session?.user?.id ?? null,
        workspaceId,
        "merge_duplicate_games",
        `game:${mergeIntoId}`,
        diffJson,
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
    return NextResponse.json({ success: true, mergedInto: mergeIntoId });
  } catch (error) {
    if (isForm) {
      return NextResponse.redirect(
        new URL("/admin/duplicates", req.url),
        303,
      );
    }
    return NextResponse.json(
      { error: "Failed to merge games", detail: String(error) },
      { status: 500 },
    );
  }
}
