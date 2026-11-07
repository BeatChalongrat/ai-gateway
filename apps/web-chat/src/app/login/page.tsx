"use client";

// Login is handled by the SRAN Portal (port 3001). This page exists
// only so direct hits / stale links don't 404 — it bounces immediately.

import { useEffect } from "react";

import { Spinner } from "@/components/ui/Spinner";
import { useT } from "@/lib/i18n";

const PORTAL_URL =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PORTAL_URL) ||
  "http://localhost:3001";

export default function LoginPage() {
  const t = useT();
  useEffect(() => {
    const ret = `${window.location.origin}/chat`;
    window.location.replace(
      `${PORTAL_URL}/login?redirect=${encodeURIComponent(ret)}`,
    );
  }, []);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-bg-base px-4 text-text-secondary">
      <Spinner size={24} />
      <p className="text-sm">{t("auth.redirectSignin")}</p>
    </main>
  );
}
