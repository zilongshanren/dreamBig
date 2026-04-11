/**
 * Workspace selection and access control helpers.
 *
 * Workspace model:
 * - "Business action" tables (alerts, subscriptions, experiments, gameTags, auditLogs)
 *   carry workspaceId and are isolated per tenant.
 * - Game data (games, platform_listings, ranking_snapshots, reviews, reports) is global.
 *
 * Selection priority (server-side):
 *   1) ?workspace=<id> in URL (used by switcher form POSTs)
 *   2) cookie "ws" set by switcher
 *   3) user.lastWorkspaceId on the User row
 *   4) any workspace the user is a member of
 *   5) the system "default" workspace
 */
import { cookies } from "next/headers";
import { auth } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

export const DEFAULT_WORKSPACE_ID = "default";
export const WORKSPACE_COOKIE = "ws";

export type WorkspaceSummary = {
  id: string;
  name: string;
  slug: string;
  isDefault: boolean;
  role: string;
};

/** Resolve the active workspace for the current request. */
export async function getCurrentWorkspaceId(): Promise<string> {
  const session = await auth();
  if (!session?.user?.id) return DEFAULT_WORKSPACE_ID;

  const cookieStore = await cookies();
  const cookieWs = cookieStore.get(WORKSPACE_COOKIE)?.value;
  if (cookieWs) {
    // Verify the user has access; otherwise drop through to fallback
    const ok = await prisma.workspaceMember.findUnique({
      where: { workspaceId_userId: { workspaceId: cookieWs, userId: session.user.id } },
    });
    if (ok) return cookieWs;
  }

  // Fall back to the user's last selection
  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
    select: { lastWorkspaceId: true },
  });
  if (user?.lastWorkspaceId) {
    const ok = await prisma.workspaceMember.findUnique({
      where: {
        workspaceId_userId: { workspaceId: user.lastWorkspaceId, userId: session.user.id },
      },
    });
    if (ok) return user.lastWorkspaceId;
  }

  // Pick any workspace the user is a member of
  const member = await prisma.workspaceMember.findFirst({
    where: { userId: session.user.id },
    orderBy: { joinedAt: "asc" },
  });
  if (member) return member.workspaceId;

  return DEFAULT_WORKSPACE_ID;
}

/** List all workspaces the current user belongs to. */
export async function listUserWorkspaces(): Promise<WorkspaceSummary[]> {
  const session = await auth();
  if (!session?.user?.id) return [];

  const rows = await prisma.workspaceMember.findMany({
    where: { userId: session.user.id },
    include: { workspace: true },
    orderBy: { joinedAt: "asc" },
  });

  return rows.map((m) => ({
    id: m.workspace.id,
    name: m.workspace.name,
    slug: m.workspace.slug,
    isDefault: m.workspace.isDefault,
    role: m.role,
  }));
}

/**
 * Switch the active workspace for the current user.
 * Validates membership, updates last_workspace_id, sets cookie.
 */
export async function switchWorkspace(workspaceId: string): Promise<boolean> {
  const session = await auth();
  if (!session?.user?.id) return false;

  const member = await prisma.workspaceMember.findUnique({
    where: { workspaceId_userId: { workspaceId, userId: session.user.id } },
  });
  if (!member) return false;

  await prisma.user.update({
    where: { id: session.user.id },
    data: { lastWorkspaceId: workspaceId },
  });

  const cookieStore = await cookies();
  cookieStore.set(WORKSPACE_COOKIE, workspaceId, {
    path: "/",
    httpOnly: false,
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 365,
  });
  return true;
}

/** Workspace-scoped role check (overrides global role for that workspace). */
export async function getWorkspaceRole(workspaceId: string): Promise<string | null> {
  const session = await auth();
  if (!session?.user?.id) return null;
  const m = await prisma.workspaceMember.findUnique({
    where: { workspaceId_userId: { workspaceId, userId: session.user.id } },
  });
  return m?.role ?? null;
}

/**
 * Throws if the current user has no access to the given workspace.
 * Returns the resolved (workspace, role) tuple.
 */
export async function requireWorkspaceAccess(workspaceId?: string) {
  const session = await auth();
  if (!session?.user?.id) throw new Error("UNAUTHORIZED");
  const wsId = workspaceId ?? (await getCurrentWorkspaceId());
  const role = await getWorkspaceRole(wsId);
  if (!role) throw new Error("FORBIDDEN");
  return { workspaceId: wsId, role, userId: session.user.id };
}
