"use client";

import { useState } from "react";

import { useChatStore } from "@/stores/chatStore";

interface Props {
  convId: string;
  messageId: string;
  current?: "thumbs_up" | "thumbs_down" | null;
}

export function FeedbackButtons({ convId, messageId, current }: Props) {
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
    <div className="flex items-center gap-1">
      <button
        type="button"
        aria-label="Helpful"
        onClick={() => send("thumbs_up")}
        className={`rounded p-1.5 transition-colors ${
          current === "thumbs_up"
            ? "bg-bg-elevated text-text-primary"
            : "text-text-muted hover:bg-bg-elevated hover:text-text-secondary"
        }`}
      >
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M7 11v9M2 13a2 2 0 0 1 2-2h3l3-7a2 2 0 0 1 2 2v5h5a2 2 0 0 1 2 2l-2 6a2 2 0 0 1-2 1H4a2 2 0 0 1-2-2z"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <button
        type="button"
        aria-label="Not helpful"
        onClick={() => send("thumbs_down")}
        className={`rounded p-1.5 transition-colors ${
          current === "thumbs_down"
            ? "bg-status-danger/20 text-status-danger"
            : "text-text-muted hover:bg-bg-elevated hover:text-text-secondary"
        }`}
      >
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M17 13V4M22 11a2 2 0 0 0-2-2h-3l-3 7a2 2 0 0 0 2-2v-5h-5a2 2 0 0 0-2-2l2-6a2 2 0 0 0 2-1h13a2 2 0 0 0 2 2z"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}
