import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requirePermission } from "@/lib/auth";
import { auth } from "@/lib/auth";
import { getCurrentWorkspaceId } from "@/lib/workspace";

/**
 * POST /api/admin/jobs/[id]/retry
 *
 * Enqueues a new ScrapeJob row with the same platform/jobType as the
 * original, marks the new row as `pending`, and copies the original
 * errorMessage context as JSON (so the worker can re-use it). The
 * retryCount on the NEW row is incremented from the original.
 *
 * Accepts both JSON and form POST. Form POST redirects back to
 * /admin/jobs so the page refreshes.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    await requirePermission("manage_users");
  } catch {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }

  const { id } = await params;
  const jobId = parseInt(id, 10);
  if (!Number.isFinite(jobId)) {
    return NextResponse.json({ error: "Invalid job id" }, { status: 400 });
  }

  const contentType = req.headers.get("content-type") ?? "";
  const isForm = !contentType.includes("application/json");

  try {
    const original = await prisma.scrapeJob.findUnique({
      where: { id: jobId },
    });
    if (!original) {
      return NextResponse.json({ error: "Job not found" }, { status: 404 });
    }

    const newJob = await prisma.scrapeJob.create({
      data: {
        platform: original.platform,
        jobType: original.jobType,
        status: "pending",
        itemsScraped: 0,
        errorMessage: JSON.stringify({
          retriedFromJobId: original.id,
          originalError: original.errorMessage ?? null,
        }),
        retryCount: (original.retryCount ?? 0) + 1,
      },
    });

    // Audit log (best effort, raw SQL so we don't depend on the
    // possibly-stale Prisma client type for audit_logs.workspace_id)
    try {
      const session = await auth();
      const workspaceId = await getCurrentWorkspaceId();
      const diffJson = JSON.stringify({
        originalId: original.id,
        newJobId: newJob.id,
        platform: original.platform,
        jobType: original.jobType,
      });
      await prisma.$executeRawUnsafe(
        `INSERT INTO audit_logs (user_id, workspace_id, action, resource, diff)
         VALUES ($1, $2, $3, $4, $5::jsonb)`,
        session?.user?.id ?? null,
        workspaceId,
        "retry_scrape_job",
        `scrape_job:${jobId}`,
        diffJson,
      );
    } catch {
      // ignore audit failures
    }

    if (isForm) {
      return NextResponse.redirect(new URL("/admin/jobs", req.url), 303);
    }
    return NextResponse.json({ success: true, newJobId: newJob.id });
  } catch (error) {
    if (isForm) {
      // Still redirect so the user sees the page; error will show if any
      return NextResponse.redirect(new URL("/admin/jobs", req.url), 303);
    }
    return NextResponse.json(
      { error: "Failed to retry job", detail: String(error) },
      { status: 500 },
    );
  }
}
