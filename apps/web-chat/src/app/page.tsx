"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/hooks/useAuth";
import { Spinner } from "@/components/ui/Spinner";
import { useAuthStore } from "@/stores/authStore";

export default function HomeGate() {
  const router = useRouter();
  const { ready, isAuthed } = useAuth();
  const status = useAuthStore((s) => s.status);

  useEffect(() => {
    if (!ready) return;
    // FIX130 C3.1 — only a session the gateway REFUSED goes to the login
    // page. An unanswered check goes to /chat, whose layout checks again
    // and shows the retry screen (and never signs the device out).
    router.replace(isAuthed || status === "unreachable" ? "/chat" : "/login");
  }, [ready, isAuthed, status, router]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner size={28} />
    </div>
  );
}
