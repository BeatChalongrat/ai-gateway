"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Clock, Copy, Globe, Trash2, Upload } from "lucide-react";

import { Avatar } from "@/components/Avatar";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Input } from "@/components/Input";
import { PageSpinner } from "@/components/Spinner";
import { useToast } from "@/components/Toast";
import { AvatarCropModal } from "./_crop/AvatarCropModal";
import { api, PortalApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { PortalUser } from "@/lib/types";
import PageHeader from "../_components/PageHeader";

type InlineMsg = { type: "success" | "error"; text: string } | null;

export default function SettingsPage() {
  const { toast } = useToast();
  const [user, setUser] = useState<PortalUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idCopied, setIdCopied] = useState(false);

  const [displayName, setDisplayName] = useState("");
  const [language, setLanguage] = useState<string>("en");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileMsg, setProfileMsg] = useState<InlineMsg>(null);

  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const [avatarMsg, setAvatarMsg] = useState<InlineMsg>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [cropSource, setCropSource] = useState<string | null>(null);

  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const me = await api.get<PortalUser>("/portal/me");
        setUser(me);
        setDisplayName(me.display_name ?? "");
        setLanguage(me.preferred_language ?? "en");
      } catch (err) {
        if (err instanceof PortalApiError) setError(err.message);
        else setError("Failed to load settings.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  function noop(e: FormEvent) {
    e.preventDefault();
  }

  async function handleProfileSubmit(e: FormEvent) {
    e.preventDefault();
    if (!user) return;
    setSavingProfile(true);
    setProfileMsg(null);
    try {
      const updated = await api.patch<PortalUser>("/portal/me", {
        display_name: displayName,
        preferred_language: language,
      });
      setUser((prev) => (prev ? { ...prev, ...updated } : updated));
      setDisplayName(updated.display_name ?? "");
      setLanguage(updated.preferred_language ?? "en");
      setProfileMsg({ type: "success", text: "Profile updated." });
      toast("Profile updated", "success");
      setTimeout(() => setProfileMsg(null), 3000);
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Failed to update profile.";
      setProfileMsg({ type: "error", text: msg });
      toast(msg, "error");
    } finally {
      setSavingProfile(false);
    }
  }

  function handleAvatarPick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setAvatarMsg({ type: "error", text: "Use JPEG, PNG, or WebP." });
      toast("Use JPEG, PNG, or WebP.", "error");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setAvatarMsg({ type: "error", text: "Image must be under 5 MB." });
      toast("Image must be under 5 MB.", "error");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    // Read as data URL so the crop modal can work in-memory. The
    // backend still does the authoritative 256x256 crop + format
    // conversion; this lets the user pick the square they actually
    // want rather than always getting a center crop.
    const reader = new FileReader();
    reader.onload = () => {
      const src = typeof reader.result === "string" ? reader.result : null;
      if (src) setCropSource(src);
    };
    reader.readAsDataURL(file);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleCropConfirm(blob: Blob) {
    setUploadingAvatar(true);
    setAvatarMsg(null);
    try {
      const formData = new FormData();
      // Backend expects a content-type we accept; WEBP is our target
      // format anyway, so encoding the cropped canvas as webp keeps
      // the bytes small. PNG fallback is handled by the browser.
      formData.append("file", blob, "avatar.webp");
      const res = await api.upload<{ avatar_url: string }>(
        "/portal/me/avatar",
        formData
      );
      setUser((prev) => (prev ? { ...prev, avatar_url: res.avatar_url } : prev));
      setAvatarMsg({ type: "success", text: "Avatar updated." });
      toast("Avatar updated", "success");
      setTimeout(() => setAvatarMsg(null), 3000);
      setCropSource(null);
    } catch (err) {
      const msg = err instanceof PortalApiError ? err.message : "Upload failed.";
      setAvatarMsg({ type: "error", text: msg });
      toast(msg, "error");
    } finally {
      setUploadingAvatar(false);
    }
  }

  async function handleAvatarRemove() {
    if (!user?.avatar_url) return;
    if (!confirm("Remove your profile picture?")) return;
    setUploadingAvatar(true);
    setAvatarMsg(null);
    try {
      await api.delete<{ avatar_url: null }>("/portal/me/avatar");
      setUser((prev) => (prev ? { ...prev, avatar_url: null } : prev));
      setAvatarMsg({ type: "success", text: "Avatar removed." });
      toast("Avatar removed", "success");
      setTimeout(() => setAvatarMsg(null), 3000);
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Could not remove avatar.";
      setAvatarMsg({ type: "error", text: msg });
      toast(msg, "error");
    } finally {
      setUploadingAvatar(false);
    }
  }

  function copyUserId() {
    if (!user) return;
    navigator.clipboard.writeText(user.id).then(() => {
      setIdCopied(true);
      toast("User ID copied", "success");
      setTimeout(() => setIdCopied(false), 1500);
    });
  }

  if (loading) return <PageSpinner />;

  const planLabel = user?.plan
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const dirty =
    displayName.trim() !== (user?.display_name ?? "") ||
    language !== (user?.preferred_language ?? "en");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        subtitle="Manage your account and credentials."
      />

      {error ? (
        <div
          style={{
            background: "rgba(220,38,38,0.08)",
            border: "1px solid rgba(220,38,38,0.25)",
            color: "var(--accent-danger)",
            fontSize: 13,
            padding: "8px 12px",
            borderRadius: 8,
          }}
        >
          {error}
        </div>
      ) : null}

      {/* Account */}
      <Card padding="md">
        <h2
          className="font-heading mb-4"
          style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}
        >
          Account
        </h2>
        <dl>
          <Row label="Member since" value={formatDate(user?.created_at)} />
          <Row
            label="User ID"
            value={
              <div
                className="flex items-center gap-2"
                style={{ fontFamily: "var(--ff-mono)", fontSize: 12 }}
              >
                <span
                  title={user?.id}
                  style={{ color: "var(--text-secondary)", cursor: "help" }}
                >
                  {user?.id.slice(0, 8)}…
                </span>
                <button
                  onClick={copyUserId}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-0.5"
                  style={{
                    fontSize: 11,
                    fontWeight: 500,
                    color: "var(--accent-primary)",
                    background: "transparent",
                    border: "1px solid var(--border-default)",
                    cursor: "pointer",
                  }}
                >
                  {idCopied ? (
                    <>
                      <Check size={11} /> Copied
                    </>
                  ) : (
                    <>
                      <Copy size={11} /> Copy
                    </>
                  )}
                </button>
              </div>
            }
          />
          <Row
            label="Plan"
            value={
              <span
                className="inline-flex items-center rounded-full px-2.5 py-0.5"
                style={{
                  background: "rgba(99,102,241,0.12)",
                  border: "1px solid rgba(99,102,241,0.3)",
                  color: "var(--accent-primary)",
                  fontSize: 12,
                  fontWeight: 500,
                }}
              >
                {planLabel}
              </span>
            }
          />
          <Row
            label="Email"
            value={<span style={{ color: "var(--text-primary)" }}>{user?.email}</span>}
          />
        </dl>
      </Card>

      {/* Profile */}
      <Card padding="md">
        <h2
          className="font-heading mb-5"
          style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}
        >
          Profile
        </h2>

        <div
          className="flex items-start gap-5 pb-5 mb-5"
          style={{ borderBottom: "1px solid var(--border-subtle)" }}
        >
          <div className="relative">
            <Avatar
              url={user?.avatar_url}
              name={user?.display_name}
              email={user?.email}
              id={user?.id}
              size={70}
            />
            {uploadingAvatar ? (
              <div
                className="absolute inset-0 flex items-center justify-center"
                style={{ background: "rgba(0,0,0,0.5)", borderRadius: "50%" }}
              >
                <div className="w-6 h-6 border-2 border-white/40 border-t-white rounded-full animate-spin" />
              </div>
            ) : null}
          </div>

          <div className="flex-1 min-w-0">
            <h3
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-primary)",
                marginBottom: 4,
              }}
            >
              Profile picture
            </h3>
            <p
              style={{
                fontSize: 12.5,
                color: "var(--text-secondary)",
                marginBottom: 12,
                lineHeight: 1.5,
              }}
            >
              JPEG, PNG, or WebP. Max 5 MB. You&apos;ll crop it to a 256×256 square after picking.
            </p>

            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="hidden"
                onChange={handleAvatarPick}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadingAvatar}
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg"
                style={{
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border-default)",
                  color: "var(--text-primary)",
                  fontSize: 13,
                  fontWeight: 500,
                  cursor: uploadingAvatar ? "not-allowed" : "pointer",
                  opacity: uploadingAvatar ? 0.6 : 1,
                }}
              >
                <Upload className="w-4 h-4" />
                {user?.avatar_url ? "Replace" : "Upload"}
              </button>
              {user?.avatar_url ? (
                <button
                  type="button"
                  onClick={handleAvatarRemove}
                  disabled={uploadingAvatar}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg"
                  style={{
                    background: "var(--bg-surface)",
                    border: "1px solid rgba(248,113,113,0.3)",
                    color: "var(--accent-danger)",
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: uploadingAvatar ? "not-allowed" : "pointer",
                    opacity: uploadingAvatar ? 0.6 : 1,
                  }}
                >
                  <Trash2 className="w-4 h-4" />
                  Remove
                </button>
              ) : null}
            </div>

            {avatarMsg ? (
              <div
                className="mt-3"
                style={{
                  fontSize: 12.5,
                  color:
                    avatarMsg.type === "success"
                      ? "var(--accent-success)"
                      : "var(--accent-danger)",
                }}
              >
                {avatarMsg.text}
              </div>
            ) : null}
          </div>
        </div>

        <form onSubmit={handleProfileSubmit} className="space-y-4 max-w-lg">
          <Input
            label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="How your name appears in the portal"
          />

          {profileMsg ? (
            <div
              className="rounded-lg px-3 py-2"
              style={{
                fontSize: 13,
                background:
                  profileMsg.type === "success"
                    ? "rgba(16,185,129,0.1)"
                    : "rgba(248,113,113,0.1)",
                border: `1px solid ${
                  profileMsg.type === "success"
                    ? "rgba(16,185,129,0.25)"
                    : "rgba(248,113,113,0.25)"
                }`,
                color:
                  profileMsg.type === "success"
                    ? "var(--accent-success)"
                    : "var(--accent-danger)",
              }}
            >
              {profileMsg.text}
            </div>
          ) : null}

          <Button
            type="submit"
            loading={savingProfile}
            disabled={savingProfile || !dirty}
          >
            Save changes
          </Button>
        </form>
      </Card>

      {/* Preferences — Feature 5: language toggle is live; other rows
          remain "coming soon" until email wiring lands. */}
      <Card padding="md">
        <div className="flex items-center justify-between mb-5">
          <h2
            className="font-heading"
            style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}
          >
            Preferences
          </h2>
        </div>

        <div
          className="flex items-start justify-between gap-4 py-3"
          style={{ borderBottom: "1px solid var(--border-subtle)" }}
        >
          <div className="min-w-0 flex-1">
            <div
              style={{
                fontWeight: 500,
                color: "var(--text-primary)",
                fontSize: 13.5,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Globe size={14} style={{ color: "var(--accent-primary)" }} />
              Language
            </div>
            <div
              style={{ fontSize: 12.5, color: "var(--text-tertiary)", marginTop: 2 }}
            >
              Transactional emails and (soon) the portal interface will use this
              language.
            </div>
          </div>
          <div
            role="radiogroup"
            aria-label="Language"
            style={{
              display: "inline-flex",
              background: "var(--bg-elevated)",
              borderRadius: 8,
              padding: 3,
              border: "1px solid var(--border-default)",
              flexShrink: 0,
            }}
          >
            {[
              { code: "en", label: "English" },
              { code: "th", label: "ไทย" },
            ].map((opt) => {
              const active = language === opt.code;
              return (
                <button
                  key={opt.code}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setLanguage(opt.code)}
                  style={{
                    padding: "6px 14px",
                    fontSize: 12.5,
                    fontWeight: 500,
                    borderRadius: 6,
                    background: active ? "var(--accent-primary)" : "transparent",
                    color: active ? "#FFFFFF" : "var(--text-secondary)",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="opacity-60 pointer-events-none">
          <PreferenceRow
            title="Email notifications"
            description="Get alerted when usage approaches your balance threshold"
            defaultOn
            pill={<ComingSoonPill />}
          />
          <PreferenceRow
            title="Weekly usage summary"
            description="Receive a weekly email with request counts and costs"
            defaultOn
            pill={<ComingSoonPill />}
          />
          <PreferenceRow
            title="Product updates"
            description="New features, model availability, and platform changes"
            pill={<ComingSoonPill />}
          />
        </div>
      </Card>

      {/* Security — coming soon. Fields stay shown (disabled) so the
          roadmap is visible, but they read as non-interactive. */}
      <Card padding="md">
        <div className="flex items-center justify-between mb-5">
          <h2
            className="font-heading"
            style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}
          >
            Security
          </h2>
          <ComingSoonPill />
        </div>
        <div className="opacity-60 pointer-events-none">
          <form onSubmit={noop} className="space-y-4 max-w-lg">
            <Input
              label="Current password"
              type="password"
              showToggle
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              disabled
              style={{
                background: "var(--bg-surface-2)",
                color: "var(--text-tertiary)",
                cursor: "not-allowed",
              }}
            />
            <Input
              label="New password"
              type="password"
              showToggle
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              disabled
              style={{
                background: "var(--bg-surface-2)",
                color: "var(--text-tertiary)",
                cursor: "not-allowed",
              }}
            />
            <Input
              label="Confirm new password"
              type="password"
              showToggle
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              disabled
              style={{
                background: "var(--bg-surface-2)",
                color: "var(--text-tertiary)",
                cursor: "not-allowed",
              }}
            />
            <Button type="submit" disabled className="opacity-50">
              Update Password
            </Button>
          </form>
        </div>
      </Card>

      {/* Danger zone */}
      <section
        className="p-6"
        style={{
          background: "var(--bg-surface)",
          border: "1px solid rgba(248,113,113,0.3)",
          borderRadius: 12,
        }}
      >
        <div className="flex items-center gap-2 mb-2">
          <AlertTriangle
            className="w-5 h-5"
            style={{ color: "var(--accent-danger)" }}
          />
          <h2
            className="font-heading"
            style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}
          >
            Danger Zone
          </h2>
        </div>
        <p
          style={{
            fontSize: 13,
            color: "var(--text-secondary)",
            marginBottom: 20,
          }}
        >
          These actions cannot be undone. Proceed carefully.
        </p>

        <div
          className="flex items-start justify-between gap-4 p-4 rounded-lg"
          style={{
            border: "1px solid rgba(248,113,113,0.2)",
            background: "rgba(248,113,113,0.04)",
          }}
        >
          <div className="min-w-0">
            <div
              style={{
                fontWeight: 500,
                color: "var(--text-primary)",
                marginBottom: 4,
                fontSize: 13.5,
              }}
            >
              Delete account
            </div>
            <div
              style={{
                fontSize: 12.5,
                color: "var(--text-secondary)",
                lineHeight: 1.5,
              }}
            >
              Permanently delete your SRAN account, all projects, API keys, and
              usage history. Unused token balance will be forfeited.
            </div>
          </div>
          <button
            type="button"
            disabled
            className="flex-shrink-0 px-4 py-2 rounded-lg"
            style={{
              background: "var(--bg-surface)",
              border: "1px solid rgba(248,113,113,0.3)",
              color: "var(--accent-danger)",
              fontSize: 13,
              fontWeight: 500,
              opacity: 0.5,
              cursor: "not-allowed",
            }}
            title="Coming soon"
          >
            Delete account
          </button>
        </div>
      </section>

      <AvatarCropModal
        open={cropSource !== null}
        imageSrc={cropSource}
        onCancel={() => setCropSource(null)}
        onConfirm={handleCropConfirm}
      />
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      className="py-3 flex items-center justify-between gap-4"
      style={{ borderBottom: "1px solid var(--border-subtle)" }}
    >
      <dt
        style={{
          fontSize: 11,
          textTransform: "uppercase",
          letterSpacing: "0.12em",
          color: "var(--text-tertiary)",
          fontWeight: 600,
        }}
      >
        {label}
      </dt>
      <dd style={{ fontSize: 13, color: "var(--text-primary)" }}>{value}</dd>
    </div>
  );
}

function ComingSoonPill() {
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full"
      style={{
        fontSize: 11,
        fontWeight: 500,
        background: "rgba(245,158,11,0.12)",
        color: "var(--accent-warning)",
        border: "1px solid rgba(245,158,11,0.25)",
      }}
    >
      <Clock className="w-3 h-3" />
      Coming soon
    </span>
  );
}

function PreferenceRow({
  title,
  description,
  defaultOn,
  pill,
}: {
  title: string;
  description: string;
  defaultOn?: boolean;
  pill?: React.ReactNode;
}) {
  return (
    <div
      className="flex items-start justify-between gap-4 py-3"
      style={{ borderBottom: "1px solid var(--border-subtle)" }}
    >
      <div className="min-w-0">
        <div
          style={{
            fontWeight: 500,
            color: "var(--text-primary)",
            fontSize: 13.5,
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {title}
          {pill}
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-tertiary)", marginTop: 2 }}>
          {description}
        </div>
      </div>
      <div
        className="flex-shrink-0 relative"
        style={{
          width: 40,
          height: 24,
          borderRadius: 999,
          background: defaultOn ? "var(--accent-primary)" : "var(--bg-elevated)",
          transition: "background 150ms",
        }}
        aria-hidden="true"
      >
        <span
          className="absolute"
          style={{
            top: 2,
            width: 20,
            height: 20,
            background: "#FFFFFF",
            borderRadius: "50%",
            boxShadow: "0 1px 3px rgba(0,0,0,0.1)",
            left: defaultOn ? "calc(100% - 22px)" : 2,
            transition: "left 150ms",
          }}
        />
      </div>
    </div>
  );
}
