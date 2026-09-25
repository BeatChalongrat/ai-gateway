"use client";

// The wizard's right column: what the agent could read from the sample file,
// and what the agent asks for (its permissions, in plain words).

import { useAmT } from "../i18n";
import type { Manifest } from "../types";
import { Card, CheckLine } from "../ui";
import type { PreviewState } from "./steps";

const MAX_COLS = 6;

export function PreviewPanel({ preview }: { preview: PreviewState }) {
  const t = useAmT();
  const ready = preview.status === "ready" ? preview : null;
  const headers = ready ? ready.preview.headers.slice(0, MAX_COLS) : [];
  const rows = ready ? ready.preview.rows.slice(0, 5) : [];
  return (
    <Card>
      <h2 className="text-sm font-semibold text-text-primary">{t("preview.title")}</h2>
      {ready && headers.length ? (
        <div className="scrollbar-thin mt-2.5 overflow-x-auto rounded-lg border border-border-subtle" data-testid="am-preview-table">
          <table className="w-full border-collapse text-left text-[11px] tabular-nums">
            <thead className="bg-bg-elevated">
              <tr>
                {headers.map((h, i) => (
                  <th key={`${h}-${i}`} scope="col" className="max-w-[9rem] truncate whitespace-nowrap px-2 py-1.5 font-medium text-text-secondary">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-t border-border-subtle">
                  {headers.map((_, ci) => (
                    <td key={ci} className="max-w-[9rem] truncate whitespace-nowrap px-2 py-1.5 text-text-primary">
                      {r[ci]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-1.5 text-xs text-text-muted">{t("preview.empty")}</p>
      )}
    </Card>
  );
}

export function AsksPanel({ manifest }: { manifest: Manifest }) {
  const t = useAmT();
  return (
    <Card>
      <h2 className="text-sm font-semibold text-text-primary">{t("asks.title")}</h2>
      <ul className="mt-2.5 flex flex-col gap-2">
        <CheckLine>{t("asks.read")}</CheckLine>
        <CheckLine>{t("asks.compute")}</CheckLine>
        <CheckLine>{t("asks.write")}</CheckLine>
        {manifest.cannot ? <CheckLine ok={false}>{manifest.cannot}</CheckLine> : null}
      </ul>
    </Card>
  );
}
