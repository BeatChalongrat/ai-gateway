"use client";

/**
 * Invite UI — Pattern-2 shareable-link + optional email fan-out.
 *
 * Two visually distinct blocks rendered top-to-bottom:
 *
 *   Block 1 — shareable invite link (always visible for owner).
 *     Fresh state: "Create invite link" primary CTA.
 *     Active state: URL field + Copy + Regenerate + Revoke controls.
 *
 *   Block 2 — send via email (optional, below separator).
 *     Chip-style multi-email input + "Send invitations" button.
 *     Disabled when no active link OR Resend isn't configured.
 *
 * For non-owners the parent renders a different card and this
 * component is never mounted.
 */

import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Mail,
  RefreshCw,
  Send,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { api, PortalApiError } from "@/lib/api";
import { formatDate, relativeTime } from "@/lib/format";
import type { TenantInvite } from "@/lib/types";

interface Props {
  tenantName: string;
  // Lets the parent know when an invite was created/revoked so it
  // can refresh any derived counts in the surrounding Team page.
  onInviteChanged?: () => void;
}

interface GetInviteResponse {
  invite: TenantInvite | null;
}

interface PostInviteResponse {
  invite: TenantInvite;
}

interface PublicSettings {
  resend_configured?: boolean;
}

interface EmailRecipient {
  // id keeps React keys stable across re-orders so an invalid chip's
  // red border doesn't flicker on surrounding typing. Using crypto
  // randomUUID() would be cleaner but breaks under older browsers
  // that target the portal — Date.now() + index is good enough.
  id: string;
  value: string;
  status: "valid" | "invalid" | "pending";
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function makeRecipient(raw: string): EmailRecipient {
  const value = raw.trim();
  const status: EmailRecipient["status"] = EMAIL_RE.test(value)
    ? "valid"
    : "invalid";
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    value,
    status,
  };
}

export function InviteSection({ tenantName, onInviteChanged }: Props) {
  const { toast } = useToast();

  const [invite, setInvite] = useState<TenantInvite | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKind, setBusyKind] = useState<
    "create" | "regenerate" | "revoke" | null
  >(null);
  const [copied, setCopied] = useState(false);

  const [regenOpen, setRegenOpen] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);

  // Role selector. Member is the only live option today — admin is
  // visible but disabled so users see the roadmap.
  const [role, setRole] = useState<"member" | "admin">("member");

  // Block 2 state
  const [resendConfigured, setResendConfigured] = useState<boolean | null>(
    null
  );
  const [recipients, setRecipients] = useState<EmailRecipient[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [a, b] = await Promise.all([
          api.get<GetInviteResponse>("/portal/team/invite"),
          api
            .get<PublicSettings>("/portal/auth/public-settings", { auth: false })
            .catch(() => ({ resend_configured: false }) as PublicSettings),
        ]);
        if (!alive) return;
        setInvite(a.invite);
        setResendConfigured(Boolean(b.resend_configured));
      } catch (err) {
        if (!alive) return;
        // An owner can still create a link even if the initial GET
        // blew up; surface the error as a toast and render the fresh-
        // state UI so the flow isn't stuck.
        const msg =
          err instanceof PortalApiError
            ? err.message
            : "Failed to load invite link.";
        toast(msg, "error");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCreate() {
    setBusyKind("create");
    try {
      const r = await api.post<PostInviteResponse>("/portal/team/invite", {
        role,
      });
      setInvite(r.invite);
      toast("Invite link created", "success");
      onInviteChanged?.();
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Failed to create invite.";
      toast(msg, "error");
    } finally {
      setBusyKind(null);
    }
  }

  async function handleRegenerate() {
    setBusyKind("regenerate");
    try {
      const r = await api.post<PostInviteResponse>("/portal/team/invite", {
        role,
        force: true,
      });
      setInvite(r.invite);
      toast("Invite link regenerated", "success");
      onInviteChanged?.();
    } catch (err) {
      const msg =
        err instanceof PortalApiError
          ? err.message
          : "Failed to regenerate invite.";
      toast(msg, "error");
    } finally {
      setBusyKind(null);
      setRegenOpen(false);
    }
  }

  async function handleRevoke() {
    setBusyKind("revoke");
    try {
      await api.delete("/portal/team/invite");
      setInvite(null);
      setRecipients([]);
      setDraft("");
      toast("Invite link revoked", "success");
      onInviteChanged?.();
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Failed to revoke invite.";
      toast(msg, "error");
    } finally {
      setBusyKind(null);
      setRevokeOpen(false);
    }
  }

  function handleCopy() {
    if (!invite?.invite_url) return;
    navigator.clipboard.writeText(invite.invite_url).then(() => {
      setCopied(true);
      toast("Invite link copied", "success");
      setTimeout(() => setCopied(false), 2000);
    });
  }

  // ---- Block 2 — chip input handlers ---------------------------------------
  function commitDraft() {
    const v = draft.trim();
    if (!v) return;
    setRecipients((prev) => [...prev, makeRecipient(v)]);
    setDraft("");
  }

  function onDraftKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitDraft();
    } else if (e.key === "Backspace" && draft === "" && recipients.length > 0) {
      setRecipients((prev) => prev.slice(0, -1));
    }
  }

  function onDraftChange(v: string) {
    // Paste-friendly: if the string contains a comma or space, split
    // on the fly so "a@x.com, b@x.com" becomes two chips.
    if (/[,;\s]/.test(v)) {
      const parts = v.split(/[,;\s]+/).filter(Boolean);
      setRecipients((prev) => [...prev, ...parts.map(makeRecipient)]);
      setDraft("");
      return;
    }
    setDraft(v);
  }

  function removeRecipient(id: string) {
    setRecipients((prev) => prev.filter((r) => r.id !== id));
  }

  const validCount = recipients.filter((r) => r.status === "valid").length;
  const canSend =
    Boolean(invite) &&
    resendConfigured === true &&
    validCount > 0 &&
    !sending;

  async function handleSend(e: FormEvent) {
    e.preventDefault();
    if (draft.trim()) commitDraft();
    if (!invite || recipients.length === 0) return;
    setSending(true);
    try {
      const valid = recipients.filter((r) => r.status === "valid");
      const r = await api.post<{
        sent: number;
        total: number;
        results: { email: string; delivered: boolean }[];
      }>("/portal/team/invite/send-email", {
        recipients: valid.map((v) => v.value),
      });
      if (r.sent === r.total) {
        toast(
          `Sent to ${r.sent} recipient${r.sent === 1 ? "" : "s"}`,
          "success"
        );
        setRecipients([]);
      } else {
        const failedEmails = new Set(
          r.results.filter((x) => !x.delivered).map((x) => x.email.toLowerCase())
        );
        toast(
          `Sent to ${r.sent} of ${r.total}. Some emails failed.`,
          "error"
        );
        // Keep the failed chips red + visible; drop the delivered ones.
        setRecipients((prev) =>
          prev
            .map((p) =>
              failedEmails.has(p.value.toLowerCase())
                ? { ...p, status: "invalid" as const }
                : p
            )
            .filter((p) => p.status !== "valid")
        );
      }
    } catch (err) {
      const msg =
        err instanceof PortalApiError
          ? err.message
          : "Failed to send invitations.";
      toast(msg, "error");
    } finally {
      setSending(false);
    }
  }

  const daysRemaining = useMemo(() => {
    if (!invite) return 0;
    const ms = new Date(invite.expires_at).getTime() - Date.now();
    return Math.max(0, Math.ceil(ms / (1000 * 60 * 60 * 24)));
  }, [invite]);

  if (loading) {
    return (
      <Card padding="md">
        <div
          style={{
            height: 120,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--text-tertiary)",
            fontSize: 13,
          }}
        >
          Loading invite link…
        </div>
      </Card>
    );
  }

  return (
    <>
      {/* Block 1 — shareable link */}
      <Card padding="md">
        <div className="flex items-start justify-between gap-4 mb-4 flex-wrap">
          <div className="flex items-start gap-3 min-w-0">
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: "rgba(99,102,241,0.12)",
                color: "var(--accent-primary)",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <UserPlus size={18} />
            </div>
            <div>
              <h2
                className="font-heading"
                style={{
                  fontSize: 16,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  margin: "0 0 2px",
                }}
              >
                Invite teammates to {tenantName}
              </h2>
              <p
                style={{
                  fontSize: 12.5,
                  color: "var(--text-tertiary)",
                  margin: 0,
                  maxWidth: 560,
                }}
              >
                {invite
                  ? `Share the link below in Slack, chat, email, or anywhere your teammates hang out.`
                  : "Create a shareable link your teammates can use to join."}
              </p>
            </div>
          </div>
          <RoleSelector value={role} onChange={setRole} disabled={Boolean(invite)} />
        </div>

        {invite ? (
          <>
            <div
              className="flex flex-col md:flex-row gap-2 items-stretch md:items-center"
              style={{ marginBottom: 10 }}
            >
              <input
                readOnly
                value={invite.invite_url ?? ""}
                onFocus={(e) => e.currentTarget.select()}
                style={{
                  flex: 1,
                  minWidth: 0,
                  height: 40,
                  padding: "0 12px",
                  borderRadius: 8,
                  border: "1px solid var(--border-default)",
                  background: "var(--bg-elevated)",
                  color: "var(--text-primary)",
                  fontFamily: "var(--ff-mono)",
                  fontSize: 12.5,
                  outline: "none",
                }}
              />
              <button
                type="button"
                onClick={handleCopy}
                className="btn-premium"
                style={{
                  height: 40,
                  padding: "0 16px",
                  borderRadius: 8,
                  border: "1px solid var(--accent-primary)",
                  background: copied
                    ? "var(--accent-success)"
                    : "var(--accent-primary)",
                  color: "#FFFFFF",
                  fontSize: 13,
                  fontWeight: 500,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  transition: "background 150ms",
                  flexShrink: 0,
                }}
              >
                {copied ? (
                  <>
                    <Check size={14} /> Copied!
                  </>
                ) : (
                  <>
                    <Copy size={14} /> Copy link
                  </>
                )}
              </button>
            </div>

            <p
              style={{
                fontSize: 12,
                color: "var(--text-tertiary)",
                margin: "0 0 14px",
              }}
            >
              Anyone with this link can join {tenantName} as {invite.role || "member"}.
              Expires in {daysRemaining} day{daysRemaining === 1 ? "" : "s"} · Created{" "}
              {relativeTime(invite.created_at)}
            </p>

            <div className="flex items-center gap-2 flex-wrap">
              <button
                type="button"
                onClick={() => setRegenOpen(true)}
                disabled={busyKind !== null}
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg"
                style={{
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-default)",
                  color: "var(--text-primary)",
                  fontSize: 12.5,
                  fontWeight: 500,
                  cursor: busyKind !== null ? "not-allowed" : "pointer",
                  opacity: busyKind !== null ? 0.6 : 1,
                }}
              >
                <RefreshCw size={13} />
                Regenerate link
              </button>
              <button
                type="button"
                onClick={() => setRevokeOpen(true)}
                disabled={busyKind !== null}
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg"
                style={{
                  background: "var(--bg-surface)",
                  border: "1px solid rgba(248,113,113,0.3)",
                  color: "var(--accent-danger)",
                  fontSize: 12.5,
                  fontWeight: 500,
                  cursor: busyKind !== null ? "not-allowed" : "pointer",
                  opacity: busyKind !== null ? 0.6 : 1,
                }}
              >
                <Trash2 size={13} />
                Revoke
              </button>
            </div>
          </>
        ) : (
          <div style={{ paddingTop: 6, paddingBottom: 2 }}>
            <Button
              type="button"
              onClick={handleCreate}
              loading={busyKind === "create"}
            >
              <UserPlus size={14} className="mr-2" />
              Create invite link
            </Button>
          </div>
        )}
      </Card>

      {/* Block 2 — email sender */}
      <div
        style={{
          position: "relative",
          padding: "4px 0",
          marginTop: 4,
          marginBottom: 4,
          textAlign: "center",
        }}
      >
        <span
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            top: "50%",
            height: 1,
            background: "var(--border-subtle)",
            zIndex: 0,
          }}
        />
        <span
          style={{
            position: "relative",
            zIndex: 1,
            background: "var(--bg-base)",
            padding: "0 14px",
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.14em",
            color: "var(--text-tertiary)",
          }}
        >
          Or send via email
        </span>
      </div>

      <Card padding="md">
        <form
          onSubmit={handleSend}
          style={{ display: "flex", flexDirection: "column", gap: 12 }}
        >
          <label
            style={{
              fontSize: 11,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--text-tertiary)",
              marginBottom: 2,
            }}
          >
            Recipients
          </label>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 6,
              minHeight: 42,
              padding: "6px 8px",
              borderRadius: 8,
              border: "1px solid var(--border-default)",
              background: "var(--bg-surface)",
              alignItems: "center",
            }}
            onClick={(e) => {
              const input = e.currentTarget.querySelector("input");
              if (input) (input as HTMLInputElement).focus();
            }}
          >
            {recipients.map((r) => (
              <Chip key={r.id} recipient={r} onRemove={() => removeRecipient(r.id)} />
            ))}
            <input
              type="text"
              placeholder={
                recipients.length === 0
                  ? "bob@company.com, carol@company.com"
                  : ""
              }
              value={draft}
              onChange={(e) => onDraftChange(e.target.value)}
              onKeyDown={onDraftKeyDown}
              onBlur={() => draft.trim() && commitDraft()}
              style={{
                flex: 1,
                minWidth: 120,
                border: "none",
                background: "transparent",
                color: "var(--text-primary)",
                fontSize: 13,
                outline: "none",
                padding: "6px 4px",
              }}
            />
          </div>
          <p
            style={{
              fontSize: 11.5,
              color: "var(--text-tertiary)",
              margin: 0,
            }}
          >
            Press Enter or comma to add multiple emails.
          </p>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Button type="submit" loading={sending} disabled={!canSend}>
              <Send size={14} className="mr-2" />
              Send invitations
            </Button>
            {resendConfigured === false ? (
              <span
                style={{
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                }}
              >
                Email sending not configured. Use the link above instead.
              </span>
            ) : !invite ? (
              <span
                style={{
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                }}
              >
                Create an invite link above first.
              </span>
            ) : (
              <span
                style={{
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                }}
              >
                We&apos;ll email each recipient the link with a short note from you.
              </span>
            )}
          </div>
        </form>
      </Card>

      <ConfirmDialog
        open={regenOpen}
        onClose={() => setRegenOpen(false)}
        onConfirm={handleRegenerate}
        title="Regenerate invite link?"
        confirmLabel="Regenerate"
        loading={busyKind === "regenerate"}
        message={
          <>
            The current link will stop working immediately. Anyone you&apos;ve
            already shared it with will need the new link. Continue?
          </>
        }
      />

      <ConfirmDialog
        open={revokeOpen}
        onClose={() => setRevokeOpen(false)}
        onConfirm={handleRevoke}
        title="Revoke invite link?"
        confirmLabel="Revoke"
        loading={busyKind === "revoke"}
        message={
          <>
            No one will be able to join with this link. You can create a new
            one anytime.
          </>
        }
      />
    </>
  );
}

function Chip({
  recipient,
  onRemove,
}: {
  recipient: EmailRecipient;
  onRemove: () => void;
}) {
  const invalid = recipient.status === "invalid";
  return (
    <span
      title={invalid ? "Invalid email" : recipient.value}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 4px 4px 10px",
        borderRadius: 999,
        background: invalid
          ? "rgba(248,113,113,0.12)"
          : "rgba(99,102,241,0.1)",
        border: `1px solid ${
          invalid ? "rgba(248,113,113,0.35)" : "rgba(99,102,241,0.3)"
        }`,
        color: invalid ? "var(--accent-danger)" : "var(--text-primary)",
        fontSize: 12.5,
        fontWeight: 500,
      }}
    >
      <Mail size={11} style={{ flexShrink: 0, opacity: 0.75 }} />
      <span
        style={{
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          maxWidth: 220,
        }}
      >
        {recipient.value}
      </span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${recipient.value}`}
        style={{
          width: 18,
          height: 18,
          borderRadius: "50%",
          border: "none",
          background: "transparent",
          color: "inherit",
          opacity: 0.7,
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <X size={11} />
      </button>
    </span>
  );
}

function RoleSelector({
  value,
  onChange,
  disabled,
}: {
  value: "member" | "admin";
  onChange: (v: "member" | "admin") => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: "relative", flexShrink: 0 }}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--text-tertiary)",
          marginRight: 8,
        }}
      >
        Role:
      </span>
      <button
        type="button"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        title={disabled ? "Revoke and regenerate to change role" : undefined}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 10px",
          borderRadius: 8,
          border: "1px solid var(--border-default)",
          background: "var(--bg-surface)",
          color: "var(--text-primary)",
          fontSize: 13,
          fontWeight: 500,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.6 : 1,
          textTransform: "capitalize",
        }}
      >
        {value}
        <ChevronDown size={13} />
      </button>
      {open ? (
        <div
          role="menu"
          onMouseLeave={() => setOpen(false)}
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            zIndex: 5,
            background: "var(--bg-surface)",
            border: "1px solid var(--border-default)",
            borderRadius: 10,
            boxShadow:
              "0 14px 36px -14px rgba(10,18,48,0.35), 0 4px 10px rgba(10,18,48,0.1)",
            minWidth: 180,
            padding: 6,
          }}
        >
          <MenuOption
            label="Member"
            hint="Default — read-only access"
            active={value === "member"}
            onClick={() => {
              onChange("member");
              setOpen(false);
            }}
          />
          <MenuOption
            label="Admin"
            hint="Coming soon"
            disabled
          />
        </div>
      ) : null}
    </div>
  );
}

function MenuOption({
  label,
  hint,
  active,
  disabled,
  onClick,
}: {
  label: string;
  hint?: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        padding: "7px 10px",
        borderRadius: 7,
        background: active ? "rgba(99,102,241,0.1)" : "transparent",
        border: "none",
        color: "var(--text-primary)",
        fontSize: 12.5,
        fontWeight: 500,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
        width: "100%",
        textAlign: "left",
      }}
      onMouseEnter={(e) => {
        if (!disabled && !active)
          e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = "transparent";
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        {label}
        {active ? <Check size={12} /> : null}
      </span>
      {hint ? (
        <span
          style={{
            fontSize: 10.5,
            color: "var(--text-tertiary)",
          }}
        >
          {hint}
        </span>
      ) : null}
    </button>
  );
}

export function MemberOnlyInviteCard({
  ownerName,
  ownerEmail,
  ownerAvatarUrl,
  ownerId,
}: {
  ownerName: string | null;
  ownerEmail: string;
  ownerAvatarUrl?: string | null;
  ownerId: string;
}) {
  // Lazy import to avoid circular-dep risk in the Avatar module when
  // this is tree-shaken from the owner path. Cheap enough.
  const { Avatar } = require("@/components/Avatar");
  return (
    <Card padding="md">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <Avatar
          url={ownerAvatarUrl}
          name={ownerName}
          email={ownerEmail}
          id={ownerId}
          size={44}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--text-primary)",
              marginBottom: 2,
            }}
          >
            Only {ownerName || ownerEmail} can invite teammates.
          </div>
          <div
            style={{
              fontSize: 12.5,
              color: "var(--text-tertiary)",
            }}
          >
            Ask them if you want to add someone.
          </div>
        </div>
      </div>
    </Card>
  );
}
