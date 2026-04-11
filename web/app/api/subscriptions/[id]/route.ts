import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { getCurrentWorkspaceId } from "@/lib/workspace";

const VALID_CHANNELS = ["feishu", "wecom", "email"];
const VALID_SCHEDULES = ["daily", "weekly", "realtime"];

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const subId = parseInt(id);
  if (Number.isNaN(subId)) {
    return NextResponse.json({ error: "invalid_id" }, { status: 400 });
  }

  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  if (body.channel !== undefined && !VALID_CHANNELS.includes(body.channel)) {
    return NextResponse.json({ error: "invalid_channel" }, { status: 400 });
  }
  if (body.schedule !== undefined && body.schedule !== null) {
    if (!VALID_SCHEDULES.includes(body.schedule)) {
      return NextResponse.json({ error: "invalid_schedule" }, { status: 400 });
    }
  }

  try {
    const workspaceId = await getCurrentWorkspaceId();
    const existing = await prisma.subscription.findUnique({
      where: { id: subId },
    });
    if (
      !existing ||
      existing.userId !== session.user.id ||
      existing.workspaceId !== workspaceId
    ) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }

    const updated = await prisma.subscription.update({
      where: { id: subId },
      data: {
        ...(typeof body.isActive === "boolean" && { isActive: body.isActive }),
        ...(typeof body.schedule === "string" && {
          schedule: body.schedule === "realtime" ? null : body.schedule,
        }),
        ...(typeof body.channel === "string" && { channel: body.channel }),
        ...(body.channelConfig !== undefined && {
          channelConfig: body.channelConfig,
        }),
        ...(typeof body.value === "string" && { value: body.value }),
      },
    });
    return NextResponse.json(updated);
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to update subscription" },
      { status: 500 },
    );
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const { id } = await params;
  const subId = parseInt(id);
  if (Number.isNaN(subId)) {
    return NextResponse.json({ error: "invalid_id" }, { status: 400 });
  }

  try {
    const workspaceId = await getCurrentWorkspaceId();
    const existing = await prisma.subscription.findUnique({
      where: { id: subId },
    });
    if (
      !existing ||
      existing.userId !== session.user.id ||
      existing.workspaceId !== workspaceId
    ) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }

    await prisma.subscription.delete({ where: { id: subId } });
    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to delete subscription" },
      { status: 500 },
    );
  }
}
