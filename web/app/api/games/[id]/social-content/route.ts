import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

/**
 * GET /api/games/[id]/social-content
 *
 * Returns up to 20 SocialContentSample rows for the given game, ordered by
 * viewCount DESC. BigInts (viewCount / likeCount) are serialized as strings
 * for safe JSON transport.
 *
 * Returns an empty list (HTTP 200) on query failure so the UI can render
 * an empty state rather than treat this as an error.
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const gameId = parseInt(id);
  if (!gameId) return NextResponse.json([], { status: 400 });

  try {
    const items = await prisma.socialContentSample.findMany({
      where: { gameId },
      orderBy: { viewCount: "desc" },
      take: 20,
      select: {
        id: true,
        platform: true,
        contentType: true,
        title: true,
        authorName: true,
        hashtags: true,
        viewCount: true,
        likeCount: true,
        hookPhrase: true,
        url: true,
        postedAt: true,
      },
    });

    // Serialize BigInts to strings
    const serialized = items.map((item) => ({
      ...item,
      viewCount: item.viewCount.toString(),
      likeCount: item.likeCount?.toString() ?? null,
      postedAt: item.postedAt.toISOString(),
    }));

    return NextResponse.json(serialized);
  } catch (e) {
    console.error("Social content query failed", e);
    return NextResponse.json([], { status: 200 });
  }
}
