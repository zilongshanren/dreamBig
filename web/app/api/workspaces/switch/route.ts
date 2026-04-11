import { NextRequest, NextResponse } from "next/server";
import { switchWorkspace } from "@/lib/workspace";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const workspaceId = String(body.workspaceId ?? "").trim();
  if (!workspaceId) {
    return NextResponse.json({ error: "workspaceId required" }, { status: 400 });
  }
  const ok = await switchWorkspace(workspaceId);
  if (!ok) {
    return NextResponse.json({ error: "FORBIDDEN" }, { status: 403 });
  }
  return NextResponse.json({ ok: true });
}
