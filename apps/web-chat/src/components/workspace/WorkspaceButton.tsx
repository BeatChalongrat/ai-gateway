"use client";

// Compact "Workspace (N)" chip pinned to the conversation header.
// Mirrors ConversationFiles' on-mount probe — does a single
// /generated-files fetch so the chip only renders when the
// conversation has at least one AI-generated artefact.
//
// Click → opens the WorkspacePanel slide-over.

import { useEffect, useState } from "react";

import { FolderOpen } from "lucide-react";

import { api } from "@/lib/api";

interface Props {
  conversationId: string;
  onOpen: () => void;
}

export function WorkspaceButton({ conversationId, onOpen }: Props) {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await api.listGeneratedFiles(conversationId);
        if (!cancelled) setCount(res.files?.length ?? 0);
      } catch {
        if (!cancelled) setCount(0);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  if (count === 0) return null;

  return (
    <button
      type="button"
      onClick={onOpen}
      className="focus-ring inline-flex items-center gap-1 rounded-full border border-border bg-bg-secondary px-2 py-0.5 text-[11px] text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
      aria-label={`Workspace (${count} generated)`}
      title={`Workspace (${count} generated file${count === 1 ? "" : "s"})`}
    >
      <FolderOpen className="h-3.5 w-3.5" />
      <span>
        {count} generated
      </span>
    </button>
  );
}
