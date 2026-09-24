"use client";

// FIX132 C — "ข้อมูลของคุณ" (Your data), on both Settings surfaces.
//
// Three things, the way the vendors put them on one settings panel:
//   - Export all my data: one ZIP built in the background; this section
//     shows pending → ready (with the download link and until when) →
//     expired / failed, polling while it is being built.
//   - When you delete a chat: gone now, permanently removed within N days,
//     backups roll off M days later — N and M from GET /account/data-policy,
//     never hard-coded, so the sentence is the sweep's real period.
//   - Delete my account: a dialog that says what happens now and later, and
//     is only armed once the account's own email is typed (no
//     window.prompt). The gateway answers in Thai when it refuses (only
//     owner of a team with members) and the reason is shown as it comes.
//
// Every visible string comes from lib/i18n/dict.ts (settings.data.* keys).

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useAuth } from "@/hooks/useAuth";
import { useDataPolicy } from "@/hooks/useDataPolicy";
import { deleteAccount } from "@/lib/accountDeletion";
import { api, ApiError } from "@/lib/api";
import { redirectToPortalLogin } from "@/lib/auth";
import { chatDeletionNotice, DELETION_NOTICE_KEYS, type FullExport } from "@/lib/dataPolicy";
import { useT } from "@/lib/i18n";

import { Row, Section } from "./DeviceSettings";

/** How often a running export is re-checked. */
export const EXPORT_POLL_MS = 3000;

function isRunning(e: FullExport | null): boolean {
  return e?.status === "pending" || e?.status === "processing";
}

export function YourDataSection() {
  const t = useT();
  const policy = useDataPolicy();
  const chatNotice = chatDeletionNotice(policy, DELETION_NOTICE_KEYS.settings);
  const vars = {
    days: String(policy.deleted_data_purge_days),
    backup: String(policy.backup_retention_days),
  };

  return (
    <Section title={t("settings.data.section")} hint={t("settings.data.hint")}>
      <ExportRow
        hint={t("settings.data.export.hint", {
          mb: String(policy.export_max_file_mb),
          hours: String(policy.export_link_hours),
        })}
      />
      <div data-testid="your-data-delete-chat">
        <p className="text-sm text-text-primary">{t("settings.data.deleteChat")}</p>
        <p className="text-xs text-text-muted">{t(chatNotice.key, chatNotice.vars)}</p>
      </div>
      <DeleteAccountRow hint={t("settings.data.deleteAccount.hint", vars)} vars={vars} />
    </Section>
  );
}

function ExportRow({ hint }: { hint: string }) {
  const t = useT();
  const [latest, setLatest] = useState<FullExport | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { exports } = await api.listFullExports();
      setLatest(exports[0] ?? null);
    } catch {
      /* the list is a convenience; the start button still works */
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refresh]);

  // Poll while the export is being built.
  useEffect(() => {
    if (!isRunning(latest)) return;
    timer.current = setTimeout(() => void refresh(), EXPORT_POLL_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [latest, refresh]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const { export: row } = await api.startFullExport();
      setLatest(row);
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      setError(
        code === "EXPORT_DAILY_LIMIT"
          ? t("settings.data.export.limit")
          : code === "EXPORT_DISABLED"
            ? t("settings.data.export.disabled")
            : t("settings.data.export.error"),
      );
    } finally {
      setStarting(false);
    }
  }

  let status: React.ReactNode = null;
  if (isRunning(latest)) {
    status = <span data-testid="export-pending">{t("settings.data.export.pending")}</span>;
  } else if (latest?.status === "completed" && latest.expires_at) {
    const until = new Date(latest.expires_at).toLocaleString();
    status = (
      <span className="flex flex-wrap items-center gap-2" data-testid="export-ready">
        {t("settings.data.export.ready", { time: until })}
        <a
          href={api.exportDownloadUrl(latest.id)}
          className="focus-ring rounded-lg border border-border bg-bg-surface px-3 py-1 text-sm text-text-primary transition-colors hover:bg-bg-elevated"
          data-testid="export-download"
        >
          {t("settings.data.export.download")}
        </a>
      </span>
    );
  } else if (latest?.status === "failed") {
    status = <span data-testid="export-failed">{t("settings.data.export.failed")}</span>;
  } else if (latest?.status === "expired") {
    status = <span data-testid="export-expired">{t("settings.data.export.expired")}</span>;
  }

  return (
    <div className="flex flex-col gap-2" data-testid="your-data-export">
      <Row label={t("settings.data.export")} hint={hint}>
        <button
          type="button"
          onClick={() => void start()}
          disabled={starting || isRunning(latest)}
          className="focus-ring shrink-0 rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-text-primary transition-colors hover:bg-bg-elevated disabled:opacity-50"
          data-testid="export-start"
        >
          {t("settings.data.export.start")}
        </button>
      </Row>
      {status ? <p className="text-xs text-text-secondary">{status}</p> : null}
      {error ? (
        <p className="text-xs text-status-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function DeleteAccountRow({ hint, vars }: { hint: string; vars: Record<string, string> }) {
  const t = useT();
  const { user } = useAuth();
  const email = user?.email ?? "";
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const armed = email !== "" && typed.trim().toLowerCase() === email.trim().toLowerCase();

  function close() {
    if (busy || done) return;
    setOpen(false);
    setTyped("");
    setError(null);
  }

  async function confirm() {
    if (!armed || busy) return;
    setBusy(true);
    setError(null);
    const res = await deleteAccount(typed.trim());
    if (res.ok) {
      setDone(true);
      // The gateway has already ended every session; send the person to the
      // sign-in page (desktop: the host's re-auth signal) after they read it.
      setTimeout(() => redirectToPortalLogin(), 2500);
      return;
    }
    setBusy(false);
    setError(res.message || t("settings.data.deleteAccount.error"));
  }

  return (
    <>
      <Row label={t("settings.data.deleteAccount")} hint={hint}>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="focus-ring shrink-0 rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-status-danger transition-colors hover:bg-bg-elevated"
          data-testid="delete-account-open"
        >
          {t("settings.data.deleteAccount.button")}
        </button>
      </Row>
      {open ? (
        <Modal
          open={open}
          onClose={close}
          title={t("settings.data.deleteAccount.title")}
          description={t("settings.data.deleteAccount.body", vars)}
          footer={
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={close} disabled={busy || done}>
                {t("common.cancel")}
              </Button>
              <Button
                variant="danger"
                onClick={() => void confirm()}
                disabled={!armed || busy || done}
                loading={busy}
                data-testid="delete-account-confirm"
              >
                {t("settings.data.deleteAccount.confirm")}
              </Button>
            </div>
          }
        >
          {done ? (
            <p className="text-sm text-text-primary" role="status" data-testid="delete-account-done">
              {t("settings.data.deleteAccount.done")}
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              <Input
                label={t("settings.data.deleteAccount.typeEmail", { email })}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                data-testid="delete-account-email"
              />
              {error ? (
                <p className="text-sm text-status-danger" role="alert" data-testid="delete-account-error">
                  {error}
                </p>
              ) : null}
            </div>
          )}
        </Modal>
      ) : null}
    </>
  );
}
