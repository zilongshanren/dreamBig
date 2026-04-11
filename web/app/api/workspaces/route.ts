import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { listUserWorkspaces } from "@/lib/workspace";

export async function GET() {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "UNAUTHORIZED" }, { status: 401 });
  }
  const workspaces = await listUserWorkspaces();
  return NextResponse.json({ workspaces });
}

export async function POST(req: NextRequest) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "UNAUTHORIZED" }, { status: 401 });
  }
  // Only super_admin can create workspaces
  if (session.user.role !== "super_admin") {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }

  const body = await req.json();
  const name = String(body.name ?? "").trim();
  const slug = String(body.slug ?? "").trim().toLowerCase();
  const description = body.description ? String(body.description) : null;

  if (!name || !slug || !/^[a-z0-9-]+$/.test(slug)) {
    return NextResponse.json(
      { error: "name and slug (lowercase alphanum + dashes) required" },
      { status: 400 },
    );
  }

  // Use slug as id for human-readable workspace IDs
  try {
    const ws = await prisma.workspace.create({
      data: {
        id: slug,
        name,
        slug,
        description,
        isDefault: false,
        members: {
          create: {
            userId: session.user.id,
            role: "super_admin",
          },
        },
      },
    });
    return NextResponse.json({ workspace: ws });
  } catch (e) {
    return NextResponse.json(
      { error: "create failed", detail: String(e) },
      { status: 500 },
    );
  }
}
