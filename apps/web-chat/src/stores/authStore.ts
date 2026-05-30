// Auth state — resolved from the gateway via the HttpOnly session cookie.
//
// HttpOnly cookie migration: chat can't read the token anymore, so we
// learn who the user is by calling /portal/me (cookie-authenticated)
// rather than decoding a JWT in JS. chat-edge still verifies the cookie
// on every protected API hit.

"use client";

import { create } from "zustand";

import { fetchSessionUser, type SessionUser } from "@/lib/auth";

interface AuthState {
  user: SessionUser | null;
  ready: boolean;
  /** Resolve the session from /portal/me and update ``user``. */
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  ready: false,

  async checkAuth() {
    const user = await fetchSessionUser();
    set({ user, ready: true });
  },
}));
