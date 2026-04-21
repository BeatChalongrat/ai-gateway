"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Clock, Copy, Trash2, Upload } from "lucide-react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Input } from "@/components/Input";
import { PageSpinner } from "@/components/Spinner";
import { api, PortalApiError } from "@/lib/api";
import { resolveAvatarUrl } from "@/lib/avatar";
import { formatDate } from "@/lib/format";
import type { PortalUser } from "@/lib/types";
import PageHeader from "../_components/PageHeader";

type InlineMsg = { type: "success" | "error"; text: string } | null;

export default function SettingsPage() {
  const [user, setUser] = useState<PortalUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [idCopied, setIdCopied] = useState(false);

  const [displayName, setDisplayName] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileMsg, setProfileMsg] = useState<InlineMsg>(null);

  const [uploadingAvatar, setUploadingAvatar] = useState(false);
  const [avatarMsg, setAvatarMsg] = useState<InlineMsg>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const me = await api.get<PortalUser>("/portal/me");
        setUser(me);
        setDisplayName(me.display_name ?? "");
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
      });
      setUser((prev) => (prev ? { ...prev, ...updated } : updated));
      setDisplayName(updated.display_name ?? "");
      setProfileMsg({ type: "success", text: "Profile updated." });
      setTimeout(() => setProfileMsg(null), 3000);
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Failed to update profile.";
      setProfileMsg({ type: "error", text: msg });
    } finally {
      setSavingProfile(false);
    }
  }

  async function handleAvatarUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setAvatarMsg({ type: "error", text: "Use JPEG, PNG, or WebP." });
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setAvatarMsg({ type: "error", text: "Image must be under 5 MB." });
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setUploadingAvatar(true);
    setAvatarMsg(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await api.upload<{ avatar_url: string }>(
        "/portal/me/avatar",
        formData
      );
      setUser((prev) => (prev ? { ...prev, avatar_url: res.avatar_url } : prev));
      setAvatarMsg({ type: "success", text: "Avatar updated." });
      setTimeout(() => setAvatarMsg(null), 3000);
    } catch (err) {
      const msg = err instanceof PortalApiError ? err.message : "Upload failed.";
      setAvatarMsg({ type: "error", text: msg });
    } finally {
      setUploadingAvatar(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
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
      setTimeout(() => setAvatarMsg(null), 3000);
    } catch (err) {
      const msg =
        err instanceof PortalApiError ? err.message : "Could not remove avatar.";
      setAvatarMsg({ type: "error", text: msg });
    } finally {
      setUploadingAvatar(false);
    }
  }

  function copyUserId() {
    if (!user) return;
    navigator.clipboard.writeText(user.id).then(() => {
      setIdCopied(true);
      setTimeout(() => setIdCopied(false), 1500);
    });
  }

  if (loading) return <PageSpinner />;

  const planLabel = user?.plan
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        subtitle="Manage your account and credentials."
      />

      {error ? (
        <div className="rounded-lg bg-red-50 border border-red-200 text-brand-red text-sm px-3 py-2">
          {error}
        </div>
      ) : null}

      {/* Account — live data, not a stub */}
      <Card padding="md">
        <h2 className="text-lg font-heading font-semibold mb-4">Account</h2>
        <dl className="divide-y divide-gray-100">
          <Row label="Member since" value={formatDate(user?.created_at)} />
          <Row
            label="User ID"
            value={
              <div className="flex items-center gap-2 font-mono text-xs">
                <span className="text-gray-700">{user?.id.slice(0, 8)}…</span>
                <button
                  onClick={copyUserId}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium text-brand-navy hover:bg-gray-100"
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
              <span className="inline-flex items-center rounded-full bg-brand-blue/10 border border-brand-blue/30 px-2.5 py-0.5 text-xs font-medium text-brand-navy">
                {planLabel}
              </span>
            }
          />
          <Row label="Email" value={<span className="text-gray-900">{user?.email}</span>} />
        </dl>
      </Card>

      {/* Profile */}
      <Card padding="md">
        <h2 className="text-lg font-heading font-semibold mb-5">Profile</h2>

        {/* Avatar block */}
        <div className="flex items-start gap-5 pb-5 mb-5 border-b border-gray-100">
          <div className="relative w-24 h-24 rounded-full overflow-hidden bg-gradient-to-br from-violet-500 to-cyan-500 flex items-center justify-center text-white text-3xl font-semibold flex-shrink-0">
            {user?.avatar_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={resolveAvatarUrl(user.avatar_url) ?? ""}
                alt="Profile"
                className="w-full h-full object-cover"
              />
            ) : (
              <span>
                {(user?.display_name || user?.email || "?").slice(0, 1).toUpperCase()}
              </span>
            )}
            {uploadingAvatar ? (
              <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
                <div className="w-6 h-6 border-2 border-white/40 border-t-white rounded-full animate-spin" />
              </div>
            ) : null}
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-gray-900 mb-1">
              Profile picture
            </h3>
            <p className="text-sm text-gray-500 mb-3 leading-relaxed">
              JPEG, PNG, or WebP. Max 5 MB. We&apos;ll crop it to a 256×256 square.
            </p>

            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="hidden"
                onChange={handleAvatarUpload}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadingAvatar}
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <Upload className="w-4 h-4" />
                {user?.avatar_url ? "Replace" : "Upload"}
              </button>
              {user?.avatar_url ? (
                <button
                  type="button"
                  onClick={handleAvatarRemove}
                  disabled={uploadingAvatar}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-rose-200 bg-white text-sm font-medium text-rose-600 hover:bg-rose-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                  Remove
                </button>
              ) : null}
            </div>

            {avatarMsg ? (
              <div
                className={`mt-3 text-sm ${
                  avatarMsg.type === "success" ? "text-green-600" : "text-rose-600"
                }`}
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
          <Input
            label="Email"
            value={user?.email ?? ""}
            readOnly
            className="bg-gray-50 text-gray-500 cursor-not-allowed"
          />

          {profileMsg ? (
            <div
              className={`rounded-lg border px-3 py-2 text-sm ${
                profileMsg.type === "success"
                  ? "bg-green-50 border-green-200 text-green-700"
                  : "bg-rose-50 border-rose-200 text-rose-700"
              }`}
            >
              {profileMsg.text}
            </div>
          ) : null}

          <Button
            type="submit"
            loading={savingProfile}
            disabled={
              savingProfile || displayName.trim() === (user?.display_name ?? "")
            }
          >
            Save changes
          </Button>
        </form>
      </Card>

      {/* Security — coming soon */}
      <Card padding="md">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-heading font-semibold">Security</h2>
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
              className="bg-gray-50 text-gray-500 cursor-not-allowed"
            />
            <Input
              label="New password"
              type="password"
              showToggle
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              disabled
              className="bg-gray-50 text-gray-500 cursor-not-allowed"
            />
            <Input
              label="Confirm new password"
              type="password"
              showToggle
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              disabled
              className="bg-gray-50 text-gray-500 cursor-not-allowed"
            />
            <Button type="submit" disabled className="opacity-50">
              Update Password
            </Button>
          </form>
        </div>
      </Card>

      {/* Preferences — coming soon */}
      <Card padding="md">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-heading font-semibold">Preferences</h2>
          <ComingSoonPill />
        </div>
        <div className="opacity-60 pointer-events-none">
          <PreferenceRow
            title="Email notifications"
            description="Get alerted when usage approaches your balance threshold"
            defaultOn
          />
          <PreferenceRow
            title="Weekly usage summary"
            description="Receive a weekly email with request counts and costs"
            defaultOn
          />
          <PreferenceRow
            title="Product updates"
            description="New features, model availability, and platform changes"
          />
        </div>
      </Card>

      {/* Danger zone */}
      <section className="bg-white rounded-xl border border-rose-200 shadow-sm p-6">
        <div className="flex items-center gap-2 mb-2">
          <AlertTriangle className="w-5 h-5 text-rose-600" />
          <h2 className="text-lg font-heading font-semibold text-gray-900">
            Danger Zone
          </h2>
        </div>
        <p className="text-sm text-gray-500 mb-5">
          These actions cannot be undone. Proceed carefully.
        </p>

        <div className="flex items-start justify-between gap-4 p-4 rounded-lg border border-rose-100 bg-rose-50/40">
          <div className="min-w-0">
            <div className="font-medium text-gray-900 mb-1">Delete account</div>
            <div className="text-sm text-gray-600 leading-relaxed">
              Permanently delete your SRAN account, all projects, API keys, and
              usage history. Unused token balance will be forfeited.
            </div>
          </div>
          <button
            type="button"
            disabled
            className="flex-shrink-0 px-4 py-2 rounded-lg border border-rose-200 bg-white text-rose-600 hover:bg-rose-50 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            title="Coming soon"
          >
            Delete account
          </button>
        </div>
      </section>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="py-3 flex items-center justify-between gap-4">
      <dt className="text-xs uppercase tracking-widest text-gray-500 font-medium">
        {label}
      </dt>
      <dd className="text-sm">{value}</dd>
    </div>
  );
}

function ComingSoonPill() {
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-100">
      <Clock className="w-3 h-3" />
      Coming soon
    </span>
  );
}

function PreferenceRow({
  title,
  description,
  defaultOn,
}: {
  title: string;
  description: string;
  defaultOn?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b border-gray-100 last:border-b-0">
      <div className="min-w-0">
        <div className="font-medium text-gray-900">{title}</div>
        <div className="text-sm text-gray-500 mt-0.5">{description}</div>
      </div>
      <div
        className={`flex-shrink-0 w-10 h-6 rounded-full relative transition-colors ${
          defaultOn ? "bg-violet-500" : "bg-gray-300"
        }`}
        aria-hidden="true"
      >
        <span
          className={`absolute top-0.5 w-5 h-5 bg-white rounded-full shadow-sm transition-all ${
            defaultOn ? "left-[calc(100%-22px)]" : "left-0.5"
          }`}
        />
      </div>
    </div>
  );
}
