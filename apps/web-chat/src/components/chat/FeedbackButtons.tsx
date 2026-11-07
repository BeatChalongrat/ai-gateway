"use client";

import { useState } from "react";

import { useT } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";

interface Props {
  convId: string;
  messageId: string;
  current?: "thumbs_up" | "thumbs_down" | null;
}

// Lucide-style stroke icons sized + weighted to match the rest of the
// assistant action row (18px, stroke-1.5, rounded joins). The path
// data is copied straight from the icon set so the thumbs read as a
// matched pair instead of one mirrored hand-drawn from the other.
export function FeedbackButtons({ convId, messageId, current }: Props) {
  const t = useT();
  const setFeedback = useChatStore((s) => s.setFeedback);
  const [busy, setBusy] = useState(false);

  async function send(rating: "thumbs_up" | "thumbs_down") {
    if (busy) return;
    setBusy(true);
    try {
      await setFeedback(convId, messageId, rating);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-0.5">
      <button
        type="button"
        aria-label={t("a11y.helpful")}
        onClick={() => send("thumbs_up")}
        className={`focus-ring tap-44 rounded p-1.5 transition-colors ${
          current === "thumbs_up"
            ? "bg-bg-elevated text-text-primary"
            : "text-text-muted hover:bg-bg-elevated hover:text-text-secondary"
        }`}
      >
        <svg
          width={18}
          height={18}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M7 10v12" />
          <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3.13 3.13 0 0 1 3 3.88Z" />
        </svg>
      </button>
      <button
        type="button"
        aria-label={t("a11y.notHelpful")}
        onClick={() => send("thumbs_down")}
        className={`focus-ring tap-44 rounded p-1.5 transition-colors ${
          current === "thumbs_down"
            ? "bg-bg-elevated text-text-primary"
            : "text-text-muted hover:bg-bg-elevated hover:text-text-secondary"
        }`}
      >
        <svg
          width={18}
          height={18}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M17 14V2" />
          <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3.13 3.13 0 0 1-3-3.88Z" />
        </svg>
      </button>
    </div>
  );
}
