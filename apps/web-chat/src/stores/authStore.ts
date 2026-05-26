// Auth state — derived entirely from the Portal access token.
//
// Phase 1.5: chat-edge does NOT mint tokens; we read the Portal's
// JWT from cookie/URL and decode the payload for display. The
// signature is verified server-side by chat-edge on every API call.

"use client";

import { create } from "zustand";

import { decodePortalJwt, getPortalToken } from "@/lib/auth";

interface AuthUser {
  user_id: string;
  tenant_id: string;
  email: string | null;
  role: string;
}

interface AuthState {
  user: AuthUser | null;
  ready: boolean;
  /** Reads the Portal token from cookie/URL and updates ``user``. */
  checkAuth: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  ready: false,

  checkAuth() {
    const token = getPortalToken();
    if (!token) {
      set({ user: null, ready: true });
      return;
    }
    const claims = decodePortalJwt(token);
    if (!claims || !claims.sub) {
      set({ user: null, ready: true });
      return;
    }
    set({
      ready: true,
      user: {
        user_id: claims.sub,
        tenant_id: claims.tenant_id ?? claims.sub,
        email: claims.email ?? null,
        role: claims.tenant_role ?? "member",
      },
    });
  },
}));
