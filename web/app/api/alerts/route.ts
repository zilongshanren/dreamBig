import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET() {
  try {
    const alerts = await prisma.alert.findMany({
      orderBy: { createdAt: "desc" },
      include: {
        _count: { select: { alertEvents: true } },
        alertEvents: {
          orderBy: { triggeredAt: "desc" },
          take: 5,
          include: { game: { select: { id: true, nameZh: true, nameEn: true } } },
        },
      },
    });

    return NextResponse.json(alerts);
  } catch (error) {
    return NextResponse.json({ error: "Failed to fetch alerts" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();

    const alert = await prisma.alert.create({
      data: {
        name: body.name,
        conditions: body.conditions,
        notifyChannel: body.notifyChannel || "feishu",
        webhookUrl: body.webhookUrl,
        cooldownHours: body.cooldownHours || 24,
      },
    });

    return NextResponse.json(alert, { status: 201 });
  } catch (error) {
    return NextResponse.json({ error: "Failed to create alert" }, { status: 500 });
  }
}
