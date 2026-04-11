import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requirePermission } from "@/lib/auth";

/**
 * GET /api/admin/jobs
 * List recent scrape_jobs with optional filters.
 * Query params: platform, status, jobType, limit (default 200, max 500)
 */
export async function GET(req: NextRequest) {
  try {
    await requirePermission("manage_users");
  } catch {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }

  const { searchParams } = new URL(req.url);
  const platform = searchParams.get("platform");
  const status = searchParams.get("status");
  const jobType = searchParams.get("jobType");
  const limitRaw = parseInt(searchParams.get("limit") ?? "200", 10);
  const limit = Number.isFinite(limitRaw)
    ? Math.min(Math.max(limitRaw, 1), 500)
    : 200;

  try {
    const jobs = await prisma.scrapeJob.findMany({
      where: {
        ...(platform && platform !== "all" ? { platform } : {}),
        ...(status && status !== "all" ? { status } : {}),
        ...(jobType && jobType !== "all" ? { jobType } : {}),
      },
      orderBy: { startedAt: "desc" },
      take: limit,
    });
    return NextResponse.json({ jobs });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch jobs", detail: String(error) },
      { status: 500 },
    );
  }
}
