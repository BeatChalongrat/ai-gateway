"use client";

// ปัญหาที่แจ้ง — what people reported from a run: open ones first, then the
// closed ones with who closed them and their note. An admin may follow a
// report to its run (opening it takes a reason); an agent publisher reads the
// report only — they get no run link.

import Link from "next/link";
import { useEffect, useState } from "react";

import Badge from "../../../components/ui/Badge";
import { useI18n } from "../../../lib/i18n";
import { amAdmin, amErrorMessage, type ProblemReport } from "../../../lib/agentMarketApi";
import { LoadError, Loading, code, formatDateTime, linkStyle, muted, secondary, stepLabel } from "./shared";

export default function ProblemsSection({ admin }: { admin: boolean }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<ProblemReport[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let alive = true;
    amAdmin.problems("all").then(
      (r) => {
        if (!alive) return;
        setRows(r?.problems ?? []);
        setError(null);
      },
      (err) => {
        if (alive) setError(err);
      },
    );
    return () => {
      alive = false;
    };
  }, [n]);

  if (error && !rows) return <LoadError error={error} onRetry={() => setN((x) => x + 1)} />;
  if (!rows) return <Loading />;
  if (!rows.length) return <p style={{ ...secondary, padding: "2rem 0", textAlign: "center" }}>{t("am.problems.none")}</p>;
  const sorted = [...rows].sort((a, b) =>
    a.status === b.status ? Date.parse(b.created_at) - Date.parse(a.created_at) : a.status === "open" ? -1 : b.status === "open" ? 1 : 0,
  );
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.75rem" }} data-testid="am-problems">
      {sorted.map((p) => (
        <ProblemRow key={p.id} p={p} admin={admin} reload={() => setN((x) => x + 1)} />
      ))}
    </ul>
  );
}

function ProblemRow({ p, admin, reload }: { p: ProblemReport; admin: boolean; reload: () => void }) {
  const { t, locale } = useI18n();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = p.status === "open";

  const close = () => {
    setBusy(true);
    setError(null);
    amAdmin.closeProblem(p.id, note.trim()).then(
      () => {
        setBusy(false);
        reload();
      },
      (err) => {
        setBusy(false);
        setError(amErrorMessage(err, t("am.failed")));
      },
    );
  };

  const where = [
    p.slug ? `${p.slug} ${p.version}`.trim() : "",
    p.run_status ? t("am.problems.runStatus", { status: p.run_status, reason: p.run_reason || "—" }) : "",
    p.page,
  ].filter(Boolean);

  return (
    <li
      className="sran-card"
      style={{
        padding: "0.9rem 1.1rem",
        borderColor: open ? "color-mix(in srgb, var(--accent-warning) 45%, transparent)" : undefined,
        background: open ? undefined : "var(--bg-surface-2)",
      }}
      data-testid="am-problem"
      data-status={p.status}
    >
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" }}>
        <span style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--text-primary)" }}>{`#${p.ref}`}</span>
        <Badge variant={open ? "warning" : "neutral"}>{open ? t("am.problems.open") : t("am.problems.closed")}</Badge>
        <span style={muted}>{`${p.email} · ${formatDateTime(p.created_at, locale)}`}</span>
      </div>
      <p style={{ marginTop: "0.4rem", marginBottom: 0, whiteSpace: "pre-wrap", fontSize: "0.875rem", color: "var(--text-primary)" }}>{p.text}</p>
      {where.length ? <p style={{ ...secondary, fontSize: "0.75rem", marginTop: "0.3rem" }}>{where.join(" · ")}</p> : null}
      {p.step ? (
        <p style={{ ...secondary, fontSize: "0.75rem", marginTop: "0.2rem" }}>
          {t(p.run_status === "succeeded" ? "am.problems.lastStep" : "am.problems.step", { step: stepLabel(p.step, t) })}
          {p.step_error ? (
            <code style={{ ...code, marginLeft: "0.35rem" }} data-testid="am-problem-step-error">
              {p.step_error}
            </code>
          ) : null}
        </p>
      ) : null}
      {admin && p.run_id ? (
        <p style={{ margin: "0.3rem 0 0", fontSize: "0.75rem" }}>
          <Link href={`/agent-market/runs/${encodeURIComponent(p.run_id)}`} style={linkStyle} data-testid="am-problem-run">
            {t("am.problems.openRun", { id: p.run_id.slice(0, 8) })}
          </Link>
        </p>
      ) : null}
      {open ? (
        <div style={{ marginTop: "0.75rem", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.5rem" }}>
          <input
            className="sran-input"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("am.problems.notePlaceholder")}
            aria-label={t("am.problems.note")}
            style={{ flex: 1, minWidth: "14rem" }}
          />
          <button type="button" className="sran-btn sran-btn-secondary sran-btn-sm" onClick={close} disabled={busy} data-testid="am-problem-close">
            {busy ? t("ui.modal.processing") : t("am.problems.close")}
          </button>
        </div>
      ) : (
        <p style={{ ...muted, marginTop: "0.3rem" }}>
          {p.admin_note
            ? t("am.problems.closedNote", { who: p.closed_by || "—", note: p.admin_note })
            : p.closed_by
              ? t("am.problems.closedBy", { who: p.closed_by })
              : null}
          {p.closed_at ? ` · ${formatDateTime(p.closed_at, locale)}` : null}
        </p>
      )}
      {error ? (
        <p role="alert" style={{ fontSize: "0.75rem", color: "var(--accent-danger)", margin: "0.3rem 0 0" }}>
          {error}
        </p>
      ) : null}
    </li>
  );
}
