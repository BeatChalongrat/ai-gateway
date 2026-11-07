"use client";

import { useT } from "@/lib/i18n";

// Public read-only conversation view. Routed at /shared/{token}.
// No auth — anyone with the link can read. No sidebar, no input,
// no file download (server-side the file blob endpoint is auth
// gated; the public view shows file badges as metadata only).

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { AssistantAvatar } from "@/components/chat/AssistantAvatar";
import { Markdown } from "@/components/chat/Markdown";
import { Spinner } from "@/components/ui/Spinner";
import { fetchPublicShared, type PublicSharedView } from "@/lib/api";
import { formatBytes, iconEmojiFor, iconKindFor, isImageMime } from "@/lib/fileUtil";

export default function SharedConversationPage() {
  const t = useT();
  const params = useParams();
  const token =
    typeof params?.token === "string" ? params.token : null;

  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<PublicSharedView | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    void (async () => {
      try {
        const v = await fetchPublicShared(token);
        if (cancelled) return;
        if (!v) {
          setMissing(true);
        } else {
          setView(v);
        }
      } catch {
        if (!cancelled) setMissing(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg-base text-text-secondary">
        <Spinner size={24} />
      </div>
    );
  }
  if (missing || !view) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg-base px-4 text-center text-text-secondary">
        <div className="max-w-md">
          <div className="mx-auto mb-4 h-12 w-12 rounded-2xl bg-gradient-to-br from-accent to-accent-subtle opacity-50" />
          <h1 className="text-lg font-semibold text-text-primary">
            {t("shared.unavailable.title")}
          </h1>
          <p className="mt-2 text-sm text-text-muted">
            {t("shared.unavailable.body")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg-base text-text-primary">
      <header className="border-b border-border bg-bg-base backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3 md:px-8">
          <div className="flex items-center gap-2">
            <div className="h-6 w-6 rounded-md bg-gradient-to-br from-accent to-accent-subtle" />
            <span className="text-sm font-semibold tracking-tight">
              SRAN Chat
            </span>
            <span className="text-[11px] text-text-muted">· {t("shared.badge")}</span>
          </div>
          {view.expires_at ? (
            <span className="text-[10px] text-text-muted">
              {t("share.expiresAt", { date: new Date(view.expires_at).toLocaleString() })}
            </span>
          ) : null}
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6 md:px-8">
        <h1 className="truncate text-lg font-semibold tracking-tight">
          {view.conversation.title}
        </h1>
        <div className="mt-1 text-[11px] text-text-muted">
          {new Date(view.conversation.created_at).toLocaleString()}
        </div>

        <div className="mt-6 flex flex-col gap-4">
          {view.messages.map((m) => {
            const isUser = m.role === "user";
            return (
              <div
                key={m.id}
                className={`flex w-full gap-3 ${
                  isUser ? "justify-end" : "justify-start"
                }`}
              >
                {!isUser ? (
                  <div className="pt-1">
                    <AssistantAvatar modelId={m.model_id ?? null} size={26} />
                  </div>
                ) : null}
                <div
                  className={`flex max-w-[80%] flex-col gap-1 ${
                    isUser ? "items-end" : "items-start"
                  }`}
                >
                  <div
                    className={
                      isUser
                        ? "rounded-2xl rounded-br-sm bg-bubble-user px-4 py-2.5 text-sm leading-relaxed shadow-sm"
                        : "prose-chat w-full text-sm"
                    }
                  >
                    {isUser ? (
                      <span className="whitespace-pre-wrap break-words">
                        {m.content}
                      </span>
                    ) : (
                      <Markdown>{m.content || ""}</Markdown>
                    )}
                  </div>
                  {m.files && m.files.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {m.files.map((f) => (
                        <span
                          key={f.id}
                          className="inline-flex max-w-[260px] items-center gap-2 rounded-md border border-border bg-bg-secondary px-2 py-1.5 text-[11px] text-text-secondary"
                          title={`${f.filename} (${formatBytes(f.file_size_bytes)})`}
                        >
                          <span aria-hidden className="text-base leading-none">
                            {isImageMime(f.content_type)
                              ? "🖼️"
                              : iconEmojiFor(iconKindFor(f.filename, f.content_type))}
                          </span>
                          <span className="min-w-0 flex-1 truncate font-medium text-text-primary">
                            {f.filename}
                          </span>
                          <span className="shrink-0 text-text-muted">
                            {formatBytes(f.file_size_bytes)}
                          </span>
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </main>

      <footer className="mx-auto max-w-3xl px-4 pb-8 pt-2 text-center text-[10px] text-text-muted">
        {t("shared.poweredBy")} SRAN Chat
      </footer>
    </div>
  );
}
