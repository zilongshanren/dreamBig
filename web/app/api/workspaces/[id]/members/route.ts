import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { getWorkspaceRole } from "@/lib/workspace";

const VALID_ROLES = ["super_admin", "analyst", "publisher", "monetization", "viewer"];

async function requireWorkspaceAdmin(workspaceId: string) {
  const session = await auth();
  if (!session?.user?.id) return { error: "UNAUTHORIZED", status: 401 } as const;
  const role = await getWorkspaceRole(workspaceId);
  if (role !== "super_admin" && session.user.role !== "super_admin") {
    return { error: "FORBIDDEN", status: 403 } as const;
  }
  return { session, role } as const;
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id: workspaceId } = await params;
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "UNAUTHORIZED" }, { status: 401 });
  }
  const role = await getWorkspaceRole(workspaceId);
  if (!role) return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });

  const members = await prisma.workspaceMember.findMany({
    where: { workspaceId },
    include: { user: { select: { id: true, email: true, name: true } } },
    orderBy: { joinedAt: "asc" },
  });
  return NextResponse.json({ members });
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id: workspaceId } = await params;
  const guard = await requireWorkspaceAdmin(workspaceId);
  if ("error" in guard) {
    return NextResponse.json({ error: guard.error }, { status: guard.status });
  }
  const body = await req.json();
  const email = String(body.email ?? "").trim();
  const role = String(body.role ?? "analyst").trim();

  if (!email || !VALID_ROLES.includes(role)) {
    return NextResponse.json({ error: "email + valid role required" }, { status: 400 });
  }
  const user = await prisma.user.findUnique({ where: { email } });
  if (!user) return NextResponse.json({ error: "user not found" }, { status: 404 });

  const member = await prisma.workspaceMember.upsert({
    where: { workspaceId_userId: { workspaceId, userId: user.id } },
    update: { role },
    create: { workspaceId, userId: user.id, role },
  });
  return NextResponse.json({ member });
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id: workspaceId } = await params;
  const guard = await requireWorkspaceAdmin(workspaceId);
  if ("error" in guard) {
    return NextResponse.json({ error: guard.error }, { status: guard.status });
  }
  const url = new URL(req.url);
  const userId = url.searchParams.get("userId");
  if (!userId) return NextResponse.json({ error: "userId required" }, { status: 400 });

  await prisma.workspaceMember.delete({
    where: { workspaceId_userId: { workspaceId, userId } },
  });
  return NextResponse.json({ ok: true });
}
