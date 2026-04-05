import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * POST /api/feishu/verify
 *
 * Feishu URL verification endpoint. When you first register the webhook URL
 * in the Feishu developer console, Feishu sends a challenge payload:
 *   { type: "url_verification", challenge: "..." }
 * We must echo back the challenge to prove ownership of the URL.
 */
export async function POST(req: NextRequest) {
  const body = await req.json();

  if (body.type === "url_verification") {
    return NextResponse.json({ challenge: body.challenge });
  }

  return NextResponse.json({ ok: true });
}
