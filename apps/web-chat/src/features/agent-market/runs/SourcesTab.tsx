"use client";

// AM12 — where a run on the web went: every search it made and every address
// it asked for, in order, with what came of each. An address that was not
// opened, or was opened and not kept, says why in words. A page that was
// saved can be looked at as the picture that was taken of it — the picture,
// never the page itself: a saved page is evidence to read, not something a
// browser is given to run. A picture deleted at the end of its keeping time
// says so; the line that the page was opened stays.

import { useState } from "react";

import { amApi } from "../api";
import { formatDateTime } from "../format";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import type { WebCall } from "../types";
import { Pill } from "../ui";
import { EvidencePicture } from "../workspace/Comparison";

const COLUMNS: readonly AmKey[] = ["sources.col.what", "sources.col.where", "sources.col.outcome", "sources.col.when"];

export function SourcesTab({ runId, calls }: { runId: string; calls: WebCall[] }) {
  const t = useAmT();
  const locale = useAmLocale();
  const [shown, setShown] = useState<string | null>(null);
  if (!calls.length) {
    return <p className="rounded-xl border border-border-subtle px-4 py-6 text-center text-sm text-text-muted">{t("sources.empty")}</p>;
  }
  return (
    <div className="scrollbar-thin overflow-x-auto rounded-xl border border-border-subtle" data-testid="am-run-sources">
      <table className="w-full min-w-[640px] border-collapse text-left text-sm">
        <thead className="bg-bg-elevated text-xs text-text-secondary">
          <tr>
            {COLUMNS.map((key) => (
              <th key={key} scope="col" className="px-3 py-2 font-medium">
                {t(key)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {calls.map((c, i) => {
            const search = c.kind === "search";
            const captured = !search && c.outcome === "captured";
            const kept = captured && !c.purged_at;
            const key = `${c.kind}:${c.source}:${i}`;
            return (
              <tr key={key} className="border-t border-border-subtle align-top" data-testid="am-source-row" data-outcome={c.outcome}>
                <td className="whitespace-nowrap px-3 py-2 text-text-secondary">{search ? t("sources.search") : t("sources.fetch")}</td>
                <td className="px-3 py-2">
                  <span className="break-all text-text-primary">{search ? c.url || c.provider : c.host}</span>
                  {!search && c.url ? <span className="block break-all text-xs text-text-muted">{c.url}</span> : null}
                </td>
                <td className="px-3 py-2">
                  {search ? (
                    <span className="text-text-primary">{t("sources.results", { n: String(c.results) })}</span>
                  ) : captured ? (
                    <div className="flex flex-col items-start gap-1.5">
                      <Pill tone={c.purged_at ? "neutral" : "success"}>
                        {c.purged_at ? t("sources.purged") : c.used ? t("sources.capturedUsed") : t("sources.captured")}
                      </Pill>
                      {kept ? (
                        shown === key ? (
                          <EvidencePicture
                            runId={runId}
                            name={c.source}
                            label={t("sources.pictureOf", { host: c.host })}
                            t={t}
                            load={() => amApi.sourcePicture(runId, c.source)}
                          />
                        ) : (
                          <button
                            type="button"
                            className="text-xs text-text-secondary underline underline-offset-2 hover:text-text-primary"
                            onClick={() => setShown(key)}
                            data-testid="am-source-picture"
                          >
                            {t("sources.picture")}
                          </button>
                        )
                      ) : null}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-1">
                      <span>
                        <Pill tone="warning">{t("sources.notOpened")}</Pill>
                      </span>
                      {/* the reason in words; a reason nobody wrote words for is not shown as its code */}
                      {c.words ? <span className="text-xs text-text-secondary">{c.words}</span> : null}
                    </div>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-xs text-text-muted">{formatDateTime(c.started_at, locale)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
