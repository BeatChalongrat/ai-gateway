"use client";

// Citation chips rendered under an assistant message that answered with
// the web_search tool. Each chip links to the source and shows its
// domain + favicon. Data comes from message.search_sources (live via
// the message_sources SSE event, or hydrated from the API on reload).

import { ExternalLink } from "lucide-react";

import type { SearchSource } from "@/lib/types";

export function SearchCitations({ sources }: { sources: SearchSource[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {sources.map((s, i) => (
        <a
          key={`${s.url}-${i}`}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          title={s.title || s.url}
          className="focus-ring inline-flex items-center gap-1.5 rounded-full border border-border bg-bg-secondary px-2.5 py-1 text-xs text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
        >
          {s.domain ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(s.domain)}&sz=16`}
              width={14}
              height={14}
              className="rounded-sm"
              alt=""
              aria-hidden
              loading="lazy"
            />
          ) : null}
          <span className="max-w-[160px] truncate">{s.domain || "source"}</span>
          <ExternalLink size={10} aria-hidden />
        </a>
      ))}
    </div>
  );
}
