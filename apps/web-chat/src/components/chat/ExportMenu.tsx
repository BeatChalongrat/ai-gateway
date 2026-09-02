"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { tOutside, useT } from "@/lib/i18n";
import { toast } from "@/components/ui/Toast";

interface Props {
  conversationId: string;
  onShare?: () => void;
}

// Conversation header "..." menu — owns the export options and a
// "Share" hand-off (the parent owns the share modal so the menu
// can also drive the same modal from outside the header).
export function ExportMenu({ conversationId, onShare }: Props) {
  const t = useT();
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
      toast(tOutside("export.toast.failed"));
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
      toast(tOutside("toast.copiedClipboard"));
    } catch {
      toast(tOutside("toast.copyFailed"));
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t("a11y.conversationActions")}
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
          <MenuButton onClick={() => exportFile("markdown")} icon={<DocIcon />}>
            {t("export.markdown")}
          </MenuButton>
          <MenuButton onClick={() => exportFile("json")} icon={<BracketsIcon />}>
            {t("export.json")}
          </MenuButton>
          <MenuButton onClick={copyMarkdown} icon={<ClipboardIcon />}>
            {t("export.copy")}
          </MenuButton>
          {onShare ? (
            <>
              <div className="border-t border-border" />
              <MenuButton
                onClick={() => {
                  setOpen(false);
                  onShare();
                }}
                icon={<LinkIcon />}
              >
                {t("export.share")}
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
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
    >
      <span aria-hidden className="shrink-0 text-text-tertiary">
        {icon}
      </span>
      <span>{children}</span>
    </button>
  );
}

// ─── Menu icons ──────────────────────────────────────────────
// All 16px stroke SVGs, currentColor, consistent 1.5 stroke
// weight — replaces the 📝📋📎🔗 emoji which read as placeholder
// next to the rest of the line-style UI.

function DocIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
      <path d="M8 13h8M8 17h6" />
    </svg>
  );
}

function BracketsIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M8 4H6a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2h2" />
      <path d="M16 4h2a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2h-2" />
    </svg>
  );
}

function ClipboardIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="8" y="3" width="8" height="4" rx="1" />
      <path d="M16 5h2a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2" />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.72-1.71" />
    </svg>
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
