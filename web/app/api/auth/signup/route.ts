import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(req: NextRequest) {
  let body: { email?: string; name?: string; password?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }

  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  const name = typeof body.name === "string" ? body.name.trim().slice(0, 100) : "";
  const password = typeof body.password === "string" ? body.password : "";

  if (!email || !EMAIL_RE.test(email)) {
    return NextResponse.json({ error: "invalid_email" }, { status: 400 });
  }
  if (!password || password.length < 8) {
    return NextResponse.json({ error: "invalid_password" }, { status: 400 });
  }

  // Bootstrap exception: the very first user is always allowed — that's how
  // super_admin gets created on a fresh install. After that, public signup
  // is gated on ALLOW_SIGNUP env var (defaults to "false" for security).
  const userCount = await prisma.user.count();
  if (userCount > 0 && process.env.ALLOW_SIGNUP !== "true") {
    return NextResponse.json(
      { error: "signup_disabled", message: "Public signup is disabled. Contact an administrator to create an account." },
      { status: 403 },
    );
  }

  const existing = await prisma.user.findUnique({ where: { email } });
  if (existing) {
    return NextResponse.json({ error: "email_exists" }, { status: 409 });
  }

  // First user becomes super_admin
  const role = userCount === 0 ? "super_admin" : "viewer";

  const passwordHash = await bcrypt.hash(password, 10);
  const user = await prisma.user.create({
    data: {
      email,
      name: name || null,
      passwordHash,
      role,
    },
  });

  return NextResponse.json({
    id: user.id,
    email: user.email,
    role: user.role,
  });
}
