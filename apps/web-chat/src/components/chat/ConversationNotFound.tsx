"use client";

// FIX132 F17 — a /chat/<id> link whose id is not a conversation. NIGHT3
// j5-02: "/chat/undefined" showed the welcome screen with a composer, and a
// send ended in a red "invalid conversation id" card, an English toast and
// a regenerate button that could never work. Now: say there is no such
// chat, and offer a new one. (ChatGPT shows an error page for a bad chat
// link too; there is nothing to send into here.)

import { MessageSquarePlus } from "lucide-react";

import { useT } from "@/lib/i18n";

export function ConversationNotFound({ onNewChat }: { onNewChat: () => void }) {
  const t = useT();
  return (
    <div className="flex flex-1 items-center justify-center px-4 py-10">
      <div role="alert" data-testid="conversation-not-found" className="max-w-sm text-center">
        <h1 className="text-lg font-semibold text-text-primary">{t("chat.notFound.title")}</h1>
        <p className="mt-2 text-sm text-text-secondary">{t("chat.notFound.body")}</p>
        <button
          type="button"
          onClick={onNewChat}
          className="focus-ring mt-5 inline-flex items-center gap-2 rounded-lg border border-border bg-bg-surface px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
        >
          <MessageSquarePlus className="h-4 w-4" aria-hidden />
          {t("chat.notFound.newChat")}
        </button>
      </div>
    </div>
  );
}
