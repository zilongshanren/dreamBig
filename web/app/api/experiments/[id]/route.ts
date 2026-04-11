import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { getCurrentWorkspaceId } from "@/lib/workspace";

const UPDATABLE_FIELDS = [
  "name",
  "hypothesis",
  "variantA",
  "variantB",
  "successMetric",
  "sampleSize",
  "priority",
  "status",
  "expectedLift",
  "actualLift",
  "notes",
  "startedAt",
  "completedAt",
] as const;

/**
 * PATCH /api/experiments/[id]
 *
 * Partial update of an experiment. Only the provided fields are updated.
 */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const expId = parseInt(id);
    if (!Number.isFinite(expId)) {
      return NextResponse.json(
        { error: "Invalid experiment id" },
        { status: 400 },
      );
    }

    const body = await req.json();
    const updateData: Record<string, unknown> = {};

    for (const field of UPDATABLE_FIELDS) {
      if (body[field] !== undefined) {
        if (field === "sampleSize" || field === "priority") {
          updateData[field] =
            body[field] === null ? null : parseInt(String(body[field]));
        } else if (field === "expectedLift" || field === "actualLift") {
          updateData[field] =
            body[field] === null ? null : Number(body[field]);
        } else if (field === "startedAt" || field === "completedAt") {
          updateData[field] =
            body[field] === null ? null : new Date(body[field]);
        } else {
          updateData[field] = body[field];
        }
      }
    }

    const workspaceId = await getCurrentWorkspaceId();
    const existing = await prisma.experiment.findFirst({
      where: { id: expId, workspaceId },
      select: { id: true },
    });
    if (!existing) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }

    const exp = await prisma.experiment.update({
      where: { id: expId },
      data: updateData,
    });
    return NextResponse.json(exp);
  } catch (error) {
    console.error("PATCH /api/experiments/[id] failed:", error);
    return NextResponse.json(
      { error: "Failed to update experiment" },
      { status: 500 },
    );
  }
}

/**
 * DELETE /api/experiments/[id]
 */
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const expId = parseInt(id);
    if (!Number.isFinite(expId)) {
      return NextResponse.json(
        { error: "Invalid experiment id" },
        { status: 400 },
      );
    }
    const workspaceId = await getCurrentWorkspaceId();
    const existing = await prisma.experiment.findFirst({
      where: { id: expId, workspaceId },
      select: { id: true },
    });
    if (!existing) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }
    await prisma.experiment.delete({ where: { id: expId } });
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("DELETE /api/experiments/[id] failed:", error);
    return NextResponse.json(
      { error: "Failed to delete experiment" },
      { status: 500 },
    );
  }
}
