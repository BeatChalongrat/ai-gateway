"use client";

// Registration lives at the SRAN Portal. Bounce immediately.

import { useEffect } from "react";

import { Spinner } from "@/components/ui/Spinner";
import { useT } from "@/lib/i18n";

const PORTAL_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PORTAL_URL) ||
  "http://localhost:3001";

export default function RegisterPage() {
  const t = useT();
  useEffect(() => {
    const ret = `${window.location.origin}/chat`;
    window.location.replace(
      `${PORTAL_URL}/signup?redirect=${encodeURIComponent(ret)}`,
    );
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-bg-base px-4 text-text-secondary">
      <Spinner size={24} />
      <p className="text-sm">{t("auth.redirectSignup")}</p>
    </main>
  );
}
