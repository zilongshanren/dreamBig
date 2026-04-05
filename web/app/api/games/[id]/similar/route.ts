import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

/**
 * GET /api/games/[id]/similar
 *
 * Returns up to 12 games most similar to the input game, using pgvector
 * cosine distance (<=> operator) against the `game_embeddings` table.
 *
 * Returns an empty list (HTTP 200) if the game has no embedding yet —
 * callers should render an empty state rather than treat this as an error.
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const gameId = parseInt(id, 10);
  if (!Number.isFinite(gameId) || gameId <= 0) {
    return NextResponse.json([], { status: 400 });
  }

  try {
    // Cosine distance: smaller = more similar. similarity = 1 - distance.
    // Guard the query with an EXISTS check so we return [] cleanly if the
    // input game has no embedding.
    const rows = await prisma.$queryRaw<
      Array<{
        id: number;
        name_zh: string | null;
        name_en: string | null;
        thumbnail_url: string | null;
        genre: string | null;
        similarity: number;
      }>
    >`
      SELECT g.id,
             g.name_zh,
             g.name_en,
             g.thumbnail_url,
             g.genre,
             1 - (ge.embedding <=> (
               SELECT embedding FROM game_embeddings WHERE game_id = ${gameId}
             )) AS similarity
      FROM game_embeddings ge
      JOIN games g ON g.id = ge.game_id
      WHERE ge.game_id != ${gameId}
        AND EXISTS (
          SELECT 1 FROM game_embeddings WHERE game_id = ${gameId}
        )
      ORDER BY ge.embedding <=> (
        SELECT embedding FROM game_embeddings WHERE game_id = ${gameId}
      )
      LIMIT 12
    `;

    // Coerce similarity to plain number (pg driver may return as string)
    const normalized = rows.map((r) => ({
      ...r,
      similarity: typeof r.similarity === "number" ? r.similarity : parseFloat(String(r.similarity)),
    }));

    return NextResponse.json(normalized);
  } catch (e) {
    console.error("Similar query failed", e);
    return NextResponse.json([], { status: 200 });
  }
}
