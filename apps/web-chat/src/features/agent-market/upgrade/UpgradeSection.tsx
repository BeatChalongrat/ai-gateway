"use client";

// Settings → "รุ่นของ agent" (AM2 phase G): the version this install runs,
// a newer one with what it changes (grouped; the instructions as -/+
// lines), "test with the sample data, then upgrade" (a self-test run of the
// new version on this install, polled until it ends), and rolling back to
// the version it replaced. Whether the upgrade took is read from the
// install's version afterwards (./upgrade.ts). Past runs keep the version
// they ran with.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { CircleCheck, TriangleAlert } from "lucide-react";

import { AmError, amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey } from "../i18n";
import { formatDate } from "../format";
import type { UpgradeInfo, VersionChange } from "../types";
import { BTN_PRIMARY_SMALL, BTN_SMALL, ErrorPanel, Spin } from "../ui";
import { runHref, workspaceHref } from "../workspace/context";
import { diffLine, groupChanges, TERMINAL, upgradeOutcome, type UpgradeOutcome } from "./upgrade";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 15 * 60_000;
const SETTLE_MS = 1000;
const SETTLE_TRIES = 6;
const MAX_VALUE = 160;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const GROUP_KEYS: Record<string, AmKey> = {
  manifest: "upg.group.manifest",
  capability: "upg.group.capability",
  setting: "upg.group.setting",
  instructions: "upg.group.instructions",
};

type Phase =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "testing"; runId: string; target: string }
  | { kind: "done"; outcome: UpgradeOutcome; runId: string };

const clip = (s: string | undefined) => (s && s.length > MAX_VALUE ? `${s.slice(0, MAX_VALUE - 1)}…` : s ?? "");

export function UpgradeSection({
  installId,
  onChanged,
  pollMs = POLL_MS,
  settleMs = SETTLE_MS,
}: {
  installId: string;
  onChanged: () => void;
  /** tests only: faster polling */
  pollMs?: number;
  settleMs?: number;
}) {
  const t = useAmT();
  const locale = useAmLocale();
  const [info, setInfo] = useState<UpgradeInfo | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);
  const [rolling, setRolling] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    const r = await amApi.upgradeInfo(installId);
    if (alive.current) {
      setInfo(r);
      setLoadError(null);
    }
    return r;
  }, [installId]);

  useEffect(() => {
    load().catch((err) => alive.current && setLoadError(err));
  }, [load]);

  const upgrade = async () => {
    const target = info?.available;
    if (!target) return;
    setPhase({ kind: "starting" });
    setError(null);
    setNote(null);
    try {
      const { run } = await amApi.upgrade(installId, target.id);
      if (!alive.current) return;
      setPhase({ kind: "testing", runId: run.id, target: target.version });
      let status = "";
      const deadline = Date.now() + POLL_LIMIT_MS;
      while (alive.current && Date.now() < deadline) {
        await sleep(pollMs);
        if (!alive.current) return;
        status = (await amApi.run(run.id))?.run?.status ?? "";
        if (TERMINAL.has(status)) break;
      }
      // the run ends a moment before the install switches: look again for a little while
      let after = await load();
      for (let i = 0; status === "succeeded" && after.current.version !== target.version && i < SETTLE_TRIES; i++) {
        await sleep(settleMs);
        if (!alive.current) return;
        after = await load();
      }
      if (!alive.current) return;
      setPhase({ kind: "done", outcome: upgradeOutcome(target.version, after.current.version), runId: run.id });
      onChanged();
    } catch (err) {
      if (!alive.current) return;
      setPhase({ kind: "idle" });
      setError(errorMessage(err));
    }
  };

  const rollback = async () => {
    const prev = info?.previous;
    if (!prev) return;
    setRolling(true);
    setError(null);
    setNote(null);
    try {
      await amApi.rollback(installId);
      const after = await load();
      if (!alive.current) return;
      setPhase({ kind: "idle" });
      setNote(t("upg.rolledBack", { v: after.current.version }));
      onChanged();
    } catch (err) {
      if (!alive.current) return;
      if (err instanceof AmError && err.status === 409) void load().catch(() => {});
      setError(errorMessage(err));
    } finally {
      if (alive.current) setRolling(false);
    }
  };

  const busy = phase.kind === "starting" || phase.kind === "testing" || rolling;
  const available = info?.available ?? null;
  const previous = info?.previous ?? null;

  return (
    <section className="mt-8" aria-labelledby="am-upgrade-title">
      <h2 id="am-upgrade-title" className="text-sm font-semibold text-text-primary">
        {t("upg.title")}
      </h2>
      <p className="mt-0.5 text-xs text-text-muted">{t("upg.pastRuns")}</p>
      <div className="mt-2.5 rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-upgrade">
        {loadError && !info ? (
          <ErrorPanel message={errorMessage(loadError)} onRetry={() => void load().catch((err) => setLoadError(err))} />
        ) : !info ? (
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <Spin size={13} />
            <span>{t("common.loading")}</span>
          </div>
        ) : (
          <>
            <p className="text-sm text-text-primary" data-testid="am-upgrade-current">
              {t("upg.current", { v: info.current.version })}
              {info.current.released_at ? <span className="text-xs text-text-muted">{` · ${t("upg.released", { when: formatDate(info.current.released_at, locale) })}`}</span> : null}
            </p>

            {phase.kind === "done" ? (
              phase.outcome.kind === "upgraded" ? (
                <p className="mt-3 flex items-start gap-2 rounded-lg border border-status-success/40 bg-status-success/10 px-3 py-2 text-sm text-text-primary" data-testid="am-upgrade-outcome" data-outcome="upgraded">
                  <CircleCheck width={16} height={16} className="mt-0.5 shrink-0 text-status-success" aria-hidden />
                  <span>{t("upg.upgraded", { v: phase.outcome.version })}</span>
                </p>
              ) : (
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-sm text-text-primary" data-testid="am-upgrade-outcome" data-outcome="kept">
                  <TriangleAlert width={16} height={16} className="mt-0.5 shrink-0 text-status-warning" aria-hidden />
                  <span className="min-w-0">
                    {t("upg.kept", { v: phase.outcome.version, tried: phase.outcome.tried })}{" "}
                    <Link href={workspaceHref(installId)} className="underline underline-offset-2 hover:opacity-80">
                      {t("upg.seeWhy")}
                    </Link>
                  </span>
                </div>
              )
            ) : null}

            {available && phase.kind !== "done" ? (
              <div className="mt-3 border-t border-border-subtle pt-3" data-testid="am-upgrade-available">
                <p className="text-sm font-medium text-text-primary">
                  {t("upg.available", { v: available.version })}
                  {available.released_at ? <span className="font-normal text-text-muted">{` · ${t("upg.released", { when: formatDate(available.released_at, locale) })}`}</span> : null}
                </p>
                <Changes changes={info.changes} />
                <p className="mt-2 text-xs text-text-secondary">{t("upg.how")}</p>
                {phase.kind === "testing" || phase.kind === "starting" ? (
                  <div role="status" className="mt-2 flex flex-wrap items-center gap-2 text-sm text-text-secondary" data-testid="am-upgrade-testing">
                    <Spin size={13} />
                    <span>{t("upg.testing", { v: available.version })}</span>
                    {phase.kind === "testing" ? (
                      <Link href={runHref(phase.runId)} className="text-xs underline underline-offset-2 hover:text-text-primary">
                        {t("upg.viewRun")}
                      </Link>
                    ) : null}
                  </div>
                ) : (
                  <button type="button" onClick={() => void upgrade()} disabled={busy} className={`${BTN_PRIMARY_SMALL} mt-2`} data-testid="am-upgrade-start">
                    {t("upg.start")}
                  </button>
                )}
              </div>
            ) : !available ? (
              <p className="mt-1 text-xs text-text-muted" data-testid="am-upgrade-latest">
                {t("upg.latest")}
              </p>
            ) : null}

            {previous ? (
              <div className="mt-3 border-t border-border-subtle pt-3">
                <button type="button" onClick={() => void rollback()} disabled={busy} className={BTN_SMALL} data-testid="am-upgrade-rollback">
                  {rolling ? <Spin size={12} /> : null}
                  <span>{t("upg.rollback", { v: previous.version })}</span>
                </button>
              </div>
            ) : null}

            {note ? (
              <p role="status" className="mt-2 text-xs text-text-secondary" data-testid="am-upgrade-note">
                {note}
              </p>
            ) : null}
            {error ? (
              <div className="mt-3">
                <ErrorPanel message={error} />
              </div>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}

function Changes({ changes }: { changes: VersionChange[] | null | undefined }) {
  const t = useAmT();
  const groups = groupChanges(changes);
  if (!groups.length) return <p className="mt-1.5 text-xs text-text-muted">{t("upg.noChanges")}</p>;
  return (
    <div className="mt-2 flex flex-col gap-2.5" data-testid="am-upgrade-changes">
      {groups.map((g) => (
        <div key={g.what} data-testid="am-upgrade-group" data-what={g.what}>
          <div className="text-xs font-medium text-text-secondary">{GROUP_KEYS[g.what] ? t(GROUP_KEYS[g.what]!) : g.what}</div>
          {g.what === "instructions" ? (
            <pre className="scrollbar-thin mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-bg-base px-2.5 py-2 font-mono text-[11px] leading-relaxed" data-testid="am-upgrade-lines">
              {g.items.flatMap((c, i) => {
                const lines =
                  c.before && c.after
                    ? [
                        { sign: "-" as const, text: c.before },
                        { sign: "+" as const, text: c.after },
                      ]
                    : [diffLine(c)];
                return lines.map((l, j) => (
                  <span key={`${i}-${j}`} className={l.sign === "+" ? "block text-status-success" : "block text-status-danger"}>
                    {`${l.sign} ${l.text}`}
                  </span>
                ));
              })}
            </pre>
          ) : (
            <ul className="mt-1 flex flex-col gap-0.5 text-xs text-text-primary">
              {g.items.map((c, i) => (
                <li key={`${c.key}-${i}`} className="min-w-0 break-words">
                  {g.what === "capability" ? (
                    <span>
                      {c.after ? `+ ${c.after}` : `− ${c.before ?? ""}`}
                      <span className="text-text-muted">{` · ${c.key === "required" ? t("upg.required") : t("upg.optional")}`}</span>
                    </span>
                  ) : (
                    <span>
                      <span className="font-mono text-text-secondary">{c.key}</span>
                      {`: ${clip(c.before) || t("upg.none")} → ${clip(c.after) || t("upg.none")}`}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
