"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { toast } from "@/components/ui/Toast";

interface Props {
  conversationId: string;
  onShare?: () => void;
}

// Conversation header "..." menu — owns the export options and a
// "Share" hand-off (the parent owns the share modal so the menu
// can also drive the same modal from outside the header).
export function ExportMenu({ conversationId, onShare }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  async function exportFile(format: "markdown" | "json") {
    setOpen(false);
    try {
      const text = await api.exportText(conversationId, format);
      const ext = format === "markdown" ? "md" : "json";
      const mime =
        format === "markdown" ? "text/markdown" : "application/json";
      downloadText(text, `conversation_${conversationId.slice(0, 8)}.${ext}`, mime);
    } catch {
      toast("Export failed. Try again.");
    }
  }

  async function copyMarkdown() {
    setOpen(false);
    try {
      const text = await api.exportText(conversationId, "markdown");
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      toast("Copied to clipboard");
    } catch {
      toast("Couldn't copy.");
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Conversation actions"
        title="Conversation actions"
        className="focus-ring rounded-md p-1.5 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary"
      >
        <svg viewBox="0 0 16 16" width={14} height={14} aria-hidden>
          <circle cx="3" cy="8" r="1.5" fill="currentColor" />
          <circle cx="8" cy="8" r="1.5" fill="currentColor" />
          <circle cx="13" cy="8" r="1.5" fill="currentColor" />
        </svg>
      </button>
      {open ? (
        <div className="popover-appear absolute right-0 top-9 z-30 w-56 overflow-hidden rounded-lg border border-border bg-bg-surface text-xs shadow-2xl">
          <MenuButton onClick={() => exportFile("markdown")} icon="📝">
            Export as Markdown
          </MenuButton>
          <MenuButton onClick={() => exportFile("json")} icon="📋">
            Export as JSON
          </MenuButton>
          <MenuButton onClick={copyMarkdown} icon="📎">
            Copy to clipboard
          </MenuButton>
          {onShare ? (
            <>
              <div className="border-t border-border/60" />
              <MenuButton
                onClick={() => {
                  setOpen(false);
                  onShare();
                }}
                icon="🔗"
              >
                Share…
              </MenuButton>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function MenuButton({
  onClick,
  icon,
  children,
}: {
  onClick: () => void;
  icon: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-2 px-3 py-2 text-left text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
    >
      <span aria-hidden className="text-sm">
        {icon}
      </span>
      <span>{children}</span>
    </button>
  );
}

function downloadText(text: string, filename: string, mime: string) {
  const blob = new Blob([text], { type: mime + ";charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a tick to consume the URL before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
