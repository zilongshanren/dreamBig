import NextAuth, { type DefaultSession } from "next-auth";
import type { JWT as _JWT } from "next-auth/jwt"; // imported so the module augmentation below resolves
import Credentials from "next-auth/providers/credentials";
import { PrismaAdapter } from "@auth/prisma-adapter";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";

// Silence "unused import" — _JWT only exists to make the module visible to augmentation.
export type __NextAuthJWT = _JWT;

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      role: string;
    } & DefaultSession["user"];
  }

  interface User {
    role?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    id?: string;
    role?: string;
  }
}

export const { handlers, signIn, signOut, auth } = NextAuth({
  adapter: PrismaAdapter(prisma),
  session: { strategy: "jwt" }, // JWT works better with credentials
  pages: { signIn: "/login" },
  // Running behind Caddy reverse proxy — trust the forwarded Host header.
  // Required in production mode; NextAuth v5 defaults to strict host binding.
  trustHost: true,
  providers: [
    Credentials({
      credentials: {
        email: { label: "Email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) return null;
        const user = await prisma.user.findUnique({
          where: { email: String(credentials.email) },
        });
        if (!user || !user.passwordHash || !user.isActive) return null;
        const ok = await bcrypt.compare(
          String(credentials.password),
          user.passwordHash,
        );
        if (!ok) return null;

        // Update lastLoginAt (fire and forget)
        prisma.user
          .update({
            where: { id: user.id },
            data: { lastLoginAt: new Date() },
          })
          .catch(() => {});

        return {
          id: user.id,
          email: user.email,
          name: user.name,
          image: user.image,
          role: user.role,
        };
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.id = user.id;
        token.role = user.role;
      }
      return token;
    },
    async session({ session, token }) {
      if (token && session.user) {
        session.user.id = token.id as string;
        session.user.role = (token.role as string) || "viewer";
      }
      return session;
    },
  },
});

// Permission helpers
export const ROLE_PERMISSIONS = {
  super_admin: ["*"] as const,
  analyst: ["view_all", "create_alert", "export_report", "tag_game"] as const,
  publisher: ["view_all", "tag_game", "export_report"] as const,
  monetization: ["view_all", "export_report", "update_threshold"] as const,
  viewer: ["view_all"] as const,
};

export type Permission =
  | "view_all"
  | "create_alert"
  | "update_threshold"
  | "export_report"
  | "tag_game"
  | "configure_push"
  | "manage_users";

export function hasPermission(
  role: string | undefined,
  perm: Permission,
): boolean {
  if (!role) return false;
  const perms = ROLE_PERMISSIONS[role as keyof typeof ROLE_PERMISSIONS];
  if (!perms) return false;
  return (
    (perms as readonly string[]).includes("*") ||
    (perms as readonly string[]).includes(perm)
  );
}

export async function requireAuth() {
  const session = await auth();
  if (!session?.user) throw new Error("UNAUTHORIZED");
  return session;
}

export async function requireRole(role: string) {
  const session = await requireAuth();
  if (session.user.role !== role && session.user.role !== "super_admin") {
    throw new Error("FORBIDDEN");
  }
  return session;
}

export async function requirePermission(perm: Permission) {
  const session = await requireAuth();
  if (!hasPermission(session.user.role, perm)) {
    throw new Error("FORBIDDEN");
  }
  return session;
}
