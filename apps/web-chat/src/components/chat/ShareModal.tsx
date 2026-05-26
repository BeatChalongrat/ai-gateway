"use client";

import { useEffect, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { toast } from "@/components/ui/Toast";
import { api, ApiError } from "@/lib/api";
import type { ShareExpiry, ShareLink } from "@/lib/types";

interface Props {
  open: boolean;
  conversationId: string | null;
  onClose: () => void;
}

const EXPIRY_OPTIONS: { value: ShareExpiry; label: string }[] = [
  { value: "1h", label: "1 hour" },
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
  { value: "never", label: "Never expires" },
];

export function ShareModal({ open, conversationId, onClose }: Props) {
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
          toast("Couldn't load share status.");
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
      toast("Share link created.");
    } catch {
      toast("Couldn't create share link.");
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
      toast("Share link revoked.");
    } catch {
      toast("Couldn't revoke share link.");
    } finally {
      setBusy(false);
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard?.writeText(url);
      toast("Link copied.");
    } catch {
      toast("Couldn't copy.");
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
      title="Share conversation"
      description="Anyone with the link can view a read-only copy."
      maxWidth={500}
    >
      {loading ? (
        <div className="py-6 text-center text-xs text-text-muted">Loading…</div>
      ) : link ? (
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-text-secondary">
              Share URL
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
                className="focus-ring rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover"
              >
                Copy
              </button>
            </div>
          </label>
          <div className="text-[11px] text-text-muted">
            {link.expires_at ? (
              <>Expires {new Date(link.expires_at).toLocaleString()}</>
            ) : (
              <>Does not expire.</>
            )}
          </div>
          {confirmRevoke ? (
            <div className="rounded-md border border-status-danger/30 bg-status-danger/10 p-3 text-[11px] text-text-secondary">
              <p>Revoke this link? The URL will stop working immediately.</p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={revoke}
                  disabled={busy}
                  className="focus-ring rounded bg-status-danger/85 px-2 py-1 text-[11px] font-medium text-white hover:bg-status-danger disabled:opacity-50"
                >
                  Revoke
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmRevoke(false)}
                  className="focus-ring rounded border border-border px-2 py-1 text-[11px] text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                >
                  Cancel
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
                Revoke link
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-medium text-text-secondary">
              Link expires after
            </legend>
            <div className="grid grid-cols-2 gap-2">
              {EXPIRY_OPTIONS.map((o) => (
                <label
                  key={o.value}
                  className={`focus-ring flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-xs ${
                    expiry === o.value
                      ? "border-accent bg-bg-elevated text-text-primary"
                      : "border-border text-text-secondary hover:bg-bg-elevated/60"
                  }`}
                >
                  <input
                    type="radio"
                    name="share-expiry"
                    value={o.value}
                    checked={expiry === o.value}
                    onChange={() => setExpiry(o.value)}
                    className="accent-accent"
                  />
                  {o.label}
                </label>
              ))}
            </div>
          </fieldset>
          <button
            type="button"
            onClick={create}
            disabled={busy}
            className="focus-ring self-end rounded-md bg-accent px-4 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {busy ? "Creating…" : "Create link"}
          </button>
        </div>
      )}
    </Modal>
  );
}
