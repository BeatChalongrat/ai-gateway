"use client";

// FIX132 — "เปิดไฟล์" for a knowledge-base document, wherever it is offered
// (the knowledge page's rows, a citation's popover, the answer's sources).
// The bytes come through the authenticated client; a PDF opens in a new tab
// on the web, anything else — and everything inside the desktop app, whose
// new tabs go to the system browser — is saved. A failure stays on screen
// next to the button that caused it.

import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import { HOSTED_MODE } from "@/lib/auth";
import type { DictKey } from "@/lib/i18n/dict";
import { openKnowledgeFile } from "@/lib/knowledge";

function openBlankTab(): Window | null {
  const w = window.open("", "_blank");
  if (w) w.opener = null;
  return w;
}

export function useOpenKnowledgeFile(): {
  open: (kbId: string, docId: string, filename: string) => Promise<void>;
  busy: boolean;
  error: DictKey | null;
} {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<DictKey | null>(null);
  const open = useCallback(async (kbId: string, docId: string, filename: string) => {
    setBusy(true);
    setError(null);
    try {
      await openKnowledgeFile(api, kbId, docId, filename, { hosted: HOSTED_MODE, openTab: openBlankTab });
    } catch (err) {
      setError((err as { status?: number } | null)?.status === 404 ? "kb.open.gone" : "kb.open.failed");
    } finally {
      setBusy(false);
    }
  }, []);
  return { open, busy, error };
}
