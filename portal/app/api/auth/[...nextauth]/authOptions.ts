// NextAuth options live in their own module so the sibling route.ts can
// stay strict about App Router's allowed named exports (GET/POST only).
// The /auth/oauth-complete page imports ``authOptions`` from here when
// it needs the shared session type.

import { type NextAuthOptions } from "next-auth";
import type { JWT } from "next-auth/jwt";
import GitHubProvider from "next-auth/providers/github";
import GoogleProvider from "next-auth/providers/google";

export type PortalTokens = {
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
  // Exactly one of these must be set. ``id_token`` for Google
  // (OpenID Connect), ``access_token`` for GitHub (no OIDC).
  id_token?: string | null;
  access_token?: string | null;
}): Promise<PortalTokens | null> {
  try {
    // The legacy POST /oauth/callback (which trusted caller-supplied
    // oauth_id/email) was removed for security. We now POST a
    // provider-issued token to /oauth/verify; the backend verifies it
    // server-side against the provider's JWKS (Google) or userinfo
    // endpoint (GitHub) before minting portal tokens.
    const res = await fetch(`${BACKEND}/portal/auth/oauth/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: args.provider,
        id_token: args.id_token ?? null,
        access_token: args.access_token ?? null,
      }),
      cache: "no-store",
    });
    if (!res.ok) {
      console.error("oauth/verify failed:", res.status, await res.text());
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
    console.error("oauth/verify error:", err);
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
      if (!account) return false;
      // Forward whichever provider-issued token the backend needs to
      // verify the identity. Google gives us an OIDC id_token here;
      // GitHub doesn't issue id_tokens, so we forward the access_token
      // which the backend exchanges against the GitHub userinfo API.
      let id_token: string | null = null;
      let access_token: string | null = null;
      if (account.provider === "google") {
        id_token = (account.id_token as string | undefined) ?? null;
        if (!id_token) {
          console.error("Google sign-in: missing id_token on account");
          return false;
        }
      } else if (account.provider === "github") {
        access_token = (account.access_token as string | undefined) ?? null;
        if (!access_token) {
          console.error("GitHub sign-in: missing access_token on account");
          return false;
        }
      } else {
        return false;
      }
      const tokens = await exchangeWithBackend({
        provider: account.provider,
        id_token,
        access_token,
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
    async session({ session }) {
      // Phase 11 E3: the portal tokens are deliberately NOT copied onto
      // the session. The session object is client-readable (any script
      // can GET /api/auth/session), which used to expose the raw
      // access + refresh tokens to JS during the OAuth completion
      // window. The tokens now live only inside the encrypted HttpOnly
      // NextAuth JWT; the server-side cookie-bridge route
      // (app/api/oauth/cookie-bridge) reads them via getToken() and
      // exchanges them for the portal's HttpOnly session cookies.
      return session;
    },
  },
};
