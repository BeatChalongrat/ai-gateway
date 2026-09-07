"use client";

// Archived chats (Phase 10 Task 1).
//
// Archive is the reversible half of "clear this out of my sidebar":
// DELETE soft-deletes and hides the thread everywhere, archive only
// removes it from the sidebar list. So this page is a plain reader —
// open a chat as usual, or put it back.
//
// The list rides the SAME paginated endpoint as the sidebar with
// ?archived=1, so ordering and shape need no second implementation.

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Archive, ArchiveRestore } from "lucide-react";

import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";
import { useT } from "@/lib/i18n";
import { useChatStore } from "@/stores/chatStore";

export default function ArchivePage() {
  const t = useT();
  const router = useRouter();
  const archived = useChatStore((s) => s.archivedConversations);
  const loaded = useChatStore((s) => s.archivedLoaded);
  const loading = useChatStore((s) => s.archivedLoading);
  const load = useChatStore((s) => s.loadArchivedConversations);
  const setArchived = useChatStore((s) => s.setConversationArchived);

  // Always re-fetch on mount: the user may have archived something in
  // this session after the last load, and a stale list here reads as
  // "my chat vanished".
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function unarchive(id: string) {
    // FIX84 row 6 — one click, one outcome. The store raises the failure
    // banner itself and rolls the row back; the success toast belongs
    // only to the case the server accepted.
    const ok = await setArchived(id, false);
    if (ok) toast(t("archive.toast.unarchived"));
  }

  return (
    <div className="h-full overflow-y-auto bg-bg-base">
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M15 18l-6-6 6-6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {t("projects.back")}
        </Link>

        <div className="mt-6 flex items-center gap-2">
          <Archive width={20} height={20} aria-hidden className="text-text-muted" />
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            {t("archive.title")}
          </h1>
        </div>
        <p className="mt-1 text-sm text-text-muted">{t("archive.hint")}</p>

        <div className="mt-6 flex flex-col gap-2">
          {!loaded && loading ? (
            <>
              <Skeleton className="h-14 w-full rounded-lg" />
              <Skeleton className="h-14 w-full rounded-lg" />
              <Skeleton className="h-14 w-full rounded-lg" />
            </>
          ) : null}

          {loaded && archived.length === 0 ? (
            <p
              className="rounded-lg border border-border bg-bg-secondary px-4 py-6 text-center text-sm text-text-muted"
              data-testid="archive-empty"
            >
              {t("archive.empty")}
            </p>
          ) : null}

          {archived.map((c) => (
            <div
              key={c.id}
              data-testid="archived-row"
              className="flex items-center gap-3 rounded-lg border border-border bg-bg-secondary px-4 py-3"
            >
              <button
                type="button"
                onClick={() => router.push(`/chat/${c.id}`)}
                className="focus-ring min-w-0 flex-1 text-left"
              >
                <span className="block truncate text-sm text-text-primary">
                  {c.title}
                </span>
                <span className="block truncate text-xs text-text-muted">
                  {c.model_id}
                </span>
              </button>
              <button
                type="button"
                onClick={() => void unarchive(c.id)}
                aria-label={t("conv.unarchive")}
                className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
              >
                <ArchiveRestore width={14} height={14} aria-hidden />
                <span className="hidden sm:inline">{t("conv.unarchive")}</span>
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
