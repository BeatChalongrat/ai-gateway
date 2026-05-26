// Convenience hook so components don't have to know about the
// underlying Zustand store. Returns the most-used fields plus the
// helpers the chat layout needs to gate routes.

"use client";

import { useAuthStore } from "@/stores/authStore";
import { redirectToPortalLogin } from "@/lib/auth";

export function useAuth() {
  const user = useAuthStore((s) => s.user);
  const ready = useAuthStore((s) => s.ready);
  return {
    user,
    isAuthed: Boolean(user),
    ready,
    checkAuth: useAuthStore((s) => s.checkAuth),
    redirectToPortalLogin,
  };
}
