"use client";

// AM21 part 4 — the finished file's pages as pictures, on the files card, before a person sends it.
// The pictures are of the delivered file itself (the runner converts the stored file and rasterises
// its pages), made on demand the first time and kept; this strip asks, waits while they are made, and
// shows them. A thumbnail opens large in place.

import { useEffect, useRef, useState } from "react";

import { Spinner } from "@/components/ui/Spinner";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { PagesStatus } from "../types";
import { cls } from "../ui";

const POLL_MS = 2000;
const POLL_MAX = 40;

export function PagesPreview({ artifactId, name }: { artifactId: string; name: string }) {
  const t = useAmT();
  const [status, setStatus] = useState<PagesStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [urls, setUrls] = useState<string[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const made = useRef<string[]>([]);

  useEffect(() => {
    let alive = true;
    let polls = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const show = async (s: PagesStatus) => {
      const n = Math.min(s.available ?? 0, 12);
      const got: string[] = [];
      for (let i = 1; i <= n; i++) {
        got.push(await amApi.artifactPage(artifactId, i));
      }
      if (!alive) {
        got.forEach((u) => URL.revokeObjectURL(u));
        return;
      }
      made.current = got;
      setUrls(got);
    };
    const poll = async () => {
      try {
        const s = await amApi.artifactPagesStatus(artifactId);
        if (!alive) return;
        setStatus(s);
        if (s.status === "ready") await show(s);
        else if (s.status === "pending" && polls++ < POLL_MAX) timer = setTimeout(poll, POLL_MS);
      } catch (err) {
        if (alive) setError(errorMessage(err));
      }
    };
    amApi.artifactPages(artifactId).then(
      async (s) => {
        if (!alive) return;
        setStatus(s);
        if (s.status === "ready") await show(s);
        else if (s.status === "pending") timer = setTimeout(poll, POLL_MS);
      },
      (err) => alive && setError(errorMessage(err)),
    );
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      made.current.forEach((u) => URL.revokeObjectURL(u));
      made.current = [];
    };
  }, [artifactId]);

  if (error) {
    return (
      <p role="alert" className="mt-2 text-[11px] text-status-danger" data-testid="am-pages-error">
        {error}
      </p>
    );
  }
  if (!status || (status.status === "pending" && !urls.length)) {
    return (
      <p role="status" className="mt-2 flex items-center gap-2 text-[11px] text-text-muted" data-testid="am-pages-pending">
        <Spinner size={12} />
        <span>{t("outputs.previewPending")}</span>
      </p>
    );
  }
  if (status.status === "unavailable" || status.status === "none") {
    return (
      <p role="status" className="mt-2 text-[11px] text-text-muted" data-testid="am-pages-unavailable">
        {t("outputs.previewUnavailable", { reason: status.reason ?? "" })}
      </p>
    );
  }
  const total = status.pages ?? urls.length;
  return (
    <div className="mt-2" data-testid="am-pages">
      <p className="text-[11px] text-text-muted">
        {urls.length < total ? t("outputs.previewMore", { n: String(urls.length), total: String(total) }) : t("outputs.previewOf")}
      </p>
      {open !== null && urls[open] ? (
        <figure className="mt-1.5">
          <button type="button" onClick={() => setOpen(null)} className="focus-ring block w-full" aria-label={t("outputs.previewClose")}>
            <img src={urls[open]} alt={t("outputs.previewPage", { n: String(open + 1), total: String(total) })} className="w-full rounded-md border border-border-subtle bg-white" />
          </button>
          <figcaption className="mt-1 text-[11px] text-text-muted">{t("outputs.previewPage", { n: String(open + 1), total: String(total) })}</figcaption>
        </figure>
      ) : null}
      <ul className="mt-1.5 flex gap-2 overflow-x-auto pb-1" data-testid="am-pages-strip">
        {urls.map((u, i) => (
          <li key={u} className="shrink-0">
            <button
              type="button"
              onClick={() => setOpen(open === i ? null : i)}
              className={cls("focus-ring block rounded-md border bg-white", open === i ? "border-accent" : "border-border-subtle")}
              aria-label={t("outputs.previewPage", { n: String(i + 1), total: String(total) })}
              aria-pressed={open === i}
            >
              <img src={u} alt={`${name} · ${t("outputs.previewPage", { n: String(i + 1), total: String(total) })}`} className="h-28 w-auto rounded-md" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
