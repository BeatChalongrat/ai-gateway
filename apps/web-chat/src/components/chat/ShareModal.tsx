"use client";

import { useEffect, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { tOutside, useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import { api, ApiError } from "@/lib/api";
import type { ShareExpiry, ShareLink } from "@/lib/types";

interface Props {
  open: boolean;
  conversationId: string | null;
  onClose: () => void;
}

// Labels resolve through the dict at render time (T4) so a locale
// switch re-labels the radios without a remount.
const EXPIRY_OPTIONS: { value: ShareExpiry; labelKey: DictKey }[] = [
  { value: "1h", labelKey: "share.expiry.1h" },
  { value: "24h", labelKey: "share.expiry.24h" },
  { value: "7d", labelKey: "share.expiry.7d" },
  { value: "never", labelKey: "share.expiry.never" },
];

export function ShareModal({ open, conversationId, onClose }: Props) {
  const t = useT();
  const [link, setLink] = useState<ShareLink | null>(null);
  const [expiry, setExpiry] = useState<ShareExpiry>("7d");
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  // Reset state on close so reopening starts clean.
  useEffect(() => {
    if (!open) {
      setLink(null);
      setConfirmRevoke(false);
      setLoading(false);
      setBusy(false);
      setExpiry("7d");
    }
  }, [open]);

  // Pre-fill existing share on open.
  useEffect(() => {
    if (!open || !conversationId) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const cur = await api.getShare(conversationId);
        if (!cancelled) setLink(cur);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          // no existing share — that's fine
        } else if (!cancelled) {
          toast(tOutside("share.toast.loadFailed"));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, conversationId]);

  async function create() {
    if (!conversationId) return;
    setBusy(true);
    try {
      const fresh = await api.createShare(conversationId, expiry);
      setLink(fresh);
      toast(tOutside("share.toast.created"));
    } catch {
      toast(tOutside("share.toast.createFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!conversationId) return;
    setBusy(true);
    try {
      await api.revokeShare(conversationId);
      setLink(null);
      setConfirmRevoke(false);
      toast(tOutside("share.toast.revoked"));
    } catch {
      toast(tOutside("share.toast.revokeFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard?.writeText(url);
      toast(tOutside("share.toast.linkCopied"));
    } catch {
      toast(tOutside("toast.copyFailed"));
    }
  }

  // Compute the absolute share URL the user actually pastes — the
  // server returns a relative /shared/{token} path; we anchor it to
  // the current origin.
  const absoluteUrl =
    link && typeof window !== "undefined"
      ? `${window.location.origin}${link.share_url}`
      : "";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("share.title")}
      description={t("share.description")}
      maxWidth={500}
    >
      {loading ? (
        <div className="py-6 text-center text-xs text-text-muted">{t("common.loading")}</div>
      ) : link ? (
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-text-secondary">
              {t("share.urlLabel")}
            </span>
            <div className="flex gap-2">
              <input
                readOnly
                value={absoluteUrl}
                className="flex-1 rounded-md border border-border bg-bg-base/60 px-3 py-2 text-xs text-text-primary"
                onFocus={(e) => e.currentTarget.select()}
              />
              <button
                type="button"
                onClick={() => void copy(absoluteUrl)}
                className="focus-ring rounded-md bg-text-primary px-3 py-1.5 text-xs font-medium text-bg-base hover:opacity-90"
              >
                {t("common.copy")}
              </button>
            </div>
          </label>
          <div className="text-[11px] text-text-muted">
            {link.expires_at
              ? t("share.expiresAt", { date: new Date(link.expires_at).toLocaleString() })
              : t("share.noExpiry")}
          </div>
          {confirmRevoke ? (
            <div className="rounded-md border border-status-danger/30 bg-status-danger/10 p-3 text-[11px] text-text-secondary">
              <p>{t("share.revokeConfirm")}</p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={revoke}
                  disabled={busy}
                  className="focus-ring rounded bg-status-danger px-2 py-1 text-[11px] font-medium text-white hover:bg-status-danger disabled:opacity-50"
                >
                  {t("share.revoke")}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmRevoke(false)}
                  className="focus-ring rounded border border-border px-2 py-1 text-[11px] text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                >
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => setConfirmRevoke(true)}
                className="focus-ring rounded-md border border-status-danger/40 px-3 py-1.5 text-xs text-status-danger hover:bg-status-danger/10"
              >
                {t("share.revokeLink")}
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-medium text-text-secondary">
              {t("share.expiryLegend")}
            </legend>
            <div className="grid grid-cols-2 gap-2">
              {EXPIRY_OPTIONS.map((o) => (
                <label
                  key={o.value}
                  className={`focus-ring flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-xs ${
                    expiry === o.value
                      ? "border-border-strong bg-bg-elevated text-text-primary"
                      : "border-border text-text-secondary hover:bg-bg-elevated/60"
                  }`}
                >
                  <input
                    type="radio"
                    name="share-expiry"
                    value={o.value}
                    checked={expiry === o.value}
                    onChange={() => setExpiry(o.value)}
                    className="accent-[var(--text-primary)]"
                  />
                  {t(o.labelKey)}
                </label>
              ))}
            </div>
          </fieldset>
          <button
            type="button"
            onClick={create}
            disabled={busy}
            className="focus-ring self-end rounded-md bg-text-primary px-4 py-1.5 text-xs font-medium text-bg-base hover:opacity-90 disabled:opacity-50"
          >
            {busy ? t("share.creating") : t("share.createLink")}
          </button>
        </div>
      )}
    </Modal>
  );
}
