import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { getCurrentWorkspaceId } from "@/lib/workspace";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const workspaceId = await getCurrentWorkspaceId();
    const body = await req.json();

    // Verify the alert belongs to the current workspace before updating
    const existing = await prisma.alert.findFirst({
      where: { id: parseInt(id), workspaceId },
      select: { id: true },
    });
    if (!existing) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }

    const alert = await prisma.alert.update({
      where: { id: parseInt(id) },
      data: {
        ...(body.name !== undefined && { name: body.name }),
        ...(body.conditions !== undefined && { conditions: body.conditions }),
        ...(body.isActive !== undefined && { isActive: body.isActive }),
        ...(body.webhookUrl !== undefined && { webhookUrl: body.webhookUrl }),
        ...(body.cooldownHours !== undefined && {
          cooldownHours: body.cooldownHours,
        }),
      },
    });

    return NextResponse.json(alert);
  } catch (error) {
    return NextResponse.json({ error: "Failed to update alert" }, { status: 500 });
  }
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const workspaceId = await getCurrentWorkspaceId();

    const existing = await prisma.alert.findFirst({
      where: { id: parseInt(id), workspaceId },
      select: { id: true },
    });
    if (!existing) {
      return NextResponse.json({ error: "Not found" }, { status: 404 });
    }

    await prisma.alertEvent.deleteMany({
      where: { alertId: parseInt(id) },
    });
    await prisma.alert.delete({ where: { id: parseInt(id) } });

    return NextResponse.json({ success: true });
  } catch (error) {
    return NextResponse.json({ error: "Failed to delete alert" }, { status: 500 });
  }
}
