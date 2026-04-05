import { NextResponse } from "next/server";
import { auth, hasPermission } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

export async function GET() {
  const session = await auth();
  if (
    !session?.user ||
    !hasPermission(session.user.role, "manage_users")
  ) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
  const users = await prisma.user.findMany({
    select: {
      id: true,
      email: true,
      name: true,
      role: true,
      isActive: true,
      createdAt: true,
      lastLoginAt: true,
    },
    orderBy: { createdAt: "desc" },
  });
  return NextResponse.json(users);
}
