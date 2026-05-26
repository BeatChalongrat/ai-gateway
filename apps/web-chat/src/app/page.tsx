"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/hooks/useAuth";
import { Spinner } from "@/components/ui/Spinner";

export default function HomeGate() {
  const router = useRouter();
  const { ready, isAuthed } = useAuth();

  useEffect(() => {
    if (!ready) return;
    router.replace(isAuthed ? "/chat" : "/login");
  }, [ready, isAuthed, router]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner size={28} />
    </div>
  );
}
