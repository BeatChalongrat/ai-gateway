// NextAuth options live in their own module so the sibling route.ts can
// stay strict about App Router's allowed named exports (GET/POST only).
// The /auth/oauth-complete page imports ``authOptions`` from here when
// it needs the shared session type.

import { type NextAuthOptions, type Session } from "next-auth";
import type { JWT } from "next-auth/jwt";
import GitHubProvider from "next-auth/providers/github";
import GoogleProvider from "next-auth/providers/google";

type PortalTokens = {
  portal_access_token?: string;
  portal_refresh_token?: string;
  portal_user?: unknown;
};

const BACKEND = process.env.BACKEND_URL || "http://localhost:8080";

const providers: NextAuthOptions["providers"] = [];

if (process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET) {
  providers.push(
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    })
  );
}

if (process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET) {
  providers.push(
    GitHubProvider({
      clientId: process.env.GITHUB_CLIENT_ID,
      clientSecret: process.env.GITHUB_CLIENT_SECRET,
    })
  );
}

async function exchangeWithBackend(args: {
  provider: string;
  oauth_id: string;
  email: string;
  display_name?: string | null;
  avatar_url?: string | null;
}): Promise<PortalTokens | null> {
  try {
    const res = await fetch(`${BACKEND}/portal/auth/oauth/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: args.provider,
        oauth_id: args.oauth_id,
        email: args.email,
        display_name: args.display_name ?? null,
        avatar_url: args.avatar_url ?? null,
      }),
      cache: "no-store",
    });
    if (!res.ok) {
      console.error("oauth/callback failed:", res.status, await res.text());
      return null;
    }
    const data = (await res.json()) as {
      access_token: string;
      refresh_token: string;
      user?: unknown;
    };
    return {
      portal_access_token: data.access_token,
      portal_refresh_token: data.refresh_token,
      portal_user: data.user,
    };
  } catch (err) {
    console.error("oauth/callback error:", err);
    return null;
  }
}

export const authOptions: NextAuthOptions = {
  providers,
  session: { strategy: "jwt" },
  pages: {
    signIn: "/login",
  },
  callbacks: {
    async signIn({ user, account }) {
      if (!account || !user?.email) return false;
      const tokens = await exchangeWithBackend({
        provider: account.provider,
        oauth_id: account.providerAccountId,
        email: user.email,
        display_name: user.name ?? null,
        avatar_url: user.image ?? null,
      });
      if (!tokens) return false;
      // Smuggle the portal tokens forward via the ``user`` object so
      // the jwt callback can read them on the initial sign-in.
      (user as typeof user & PortalTokens).portal_access_token =
        tokens.portal_access_token;
      (user as typeof user & PortalTokens).portal_refresh_token =
        tokens.portal_refresh_token;
      return true;
    },
    async jwt({ token, user }) {
      if (user) {
        const u = user as typeof user & PortalTokens;
        if (u.portal_access_token) token.portal_access_token = u.portal_access_token;
        if (u.portal_refresh_token) token.portal_refresh_token = u.portal_refresh_token;
      }
      return token as JWT & PortalTokens;
    },
    async session({ session, token }) {
      const t = token as JWT & PortalTokens;
      (session as Session & PortalTokens).portal_access_token = t.portal_access_token;
      (session as Session & PortalTokens).portal_refresh_token = t.portal_refresh_token;
      return session;
    },
  },
};
