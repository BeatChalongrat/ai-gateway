// Auth state — resolved from the gateway via the HttpOnly session cookie.
//
// HttpOnly cookie migration: chat can't read the token anymore, so we
// learn who the user is by calling /portal/me (cookie-authenticated)
// rather than decoding a JWT in JS. chat-edge still verifies the cookie
// on every protected API hit.

"use client";

import { create } from "zustand";

import { checkSession, type SessionUser } from "@/lib/auth";

/** FIX130 C3.1 — what the last session check found ("unknown" before one
 *  finished). Layouts send only "rejected" to the login page; "unreachable"
 *  gets a retry screen and never the revoking logout. */
export type AuthStatus = "unknown" | "ok" | "rejected" | "unreachable";

interface AuthState {
  user: SessionUser | null;
  ready: boolean;
  status: AuthStatus;
  /** Resolve the session from /portal/me and update ``user``. Concurrent
   *  callers (AppProviders and the route layout both check at mount) share
   *  one request — and so at most one refresh. */
  checkAuth: () => Promise<void>;
}

let inFlight: Promise<void> | null = null;

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  ready: false,
  status: "unknown",

  checkAuth() {
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        const r = await checkSession();
        if (r.kind === "ok") set({ user: r.user, ready: true, status: "ok" });
        // An unreachable check keeps a user this page already knew: one
        // slow answer is not a signed-out person.
        else if (r.kind === "unreachable") set((s) => ({ ready: true, status: s.user ? "ok" : "unreachable" }));
        else set({ user: null, ready: true, status: "rejected" });
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  },
}));
