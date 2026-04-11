import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { getCurrentWorkspaceId } from "@/lib/workspace";

const VALID_DIMENSIONS = ["platform", "genre", "region", "keyword", "game"];
const VALID_CHANNELS = ["feishu", "wecom", "email"];
const VALID_SCHEDULES = ["daily", "weekly", "realtime"];

export async function GET() {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  try {
    const workspaceId = await getCurrentWorkspaceId();
    const subs = await prisma.subscription.findMany({
      where: { userId: session.user.id, workspaceId },
      orderBy: { createdAt: "desc" },
    });
    return NextResponse.json(subs);
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch subscriptions" },
      { status: 500 },
    );
  }
}

export async function POST(req: NextRequest) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  if (!VALID_DIMENSIONS.includes(body.dimension)) {
    return NextResponse.json({ error: "invalid_dimension" }, { status: 400 });
  }
  if (!VALID_CHANNELS.includes(body.channel)) {
    return NextResponse.json({ error: "invalid_channel" }, { status: 400 });
  }
  const schedule = body.schedule || "daily";
  if (!VALID_SCHEDULES.includes(schedule)) {
    return NextResponse.json({ error: "invalid_schedule" }, { status: 400 });
  }

  const value = String(body.value || "").trim();
  if (!value) {
    return NextResponse.json({ error: "missing_value" }, { status: 400 });
  }

  try {
    const workspaceId = await getCurrentWorkspaceId();
    const sub = await prisma.subscription.create({
      data: {
        userId: session.user.id,
        workspaceId,
        dimension: body.dimension,
        value,
        channel: body.channel,
        channelConfig: body.channelConfig || {},
        schedule: schedule === "realtime" ? null : schedule,
      },
    });
    return NextResponse.json(sub, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to create subscription" },
      { status: 500 },
    );
  }
}
