"use client";

/**
 * Team management page (Phase 3 C26).
 *
 * Minimal UI for the internal-tool launch:
 * - Every member sees the member list.
 * - Owners additionally see active invites and can generate / revoke.
 * - Owners can remove members; members can self-remove.
 * - Real email invitations are out of scope — invite codes are shown
 *   inline for the owner to copy and share out-of-band.
 */

import { FormEvent, useEffect, useState } from "react";
import { Copy, Crown, Mail, Trash2, UserMinus } from "lucide-react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Input } from "@/components/Input";
import { PageSpinner } from "@/components/Spinner";
import { api, PortalApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { PortalUser, TenantInvite, TenantMember } from "@/lib/types";

import PageHeader from "../_components/PageHeader";

interface MembersResponse {
  members: TenantMember[];
}

interface InvitesResponse {
  invites: TenantInvite[];
}

export default function TeamPage() {
  const [me, setMe] = useState<PortalUser | null>(null);
  const [members, setMembers] = useState<TenantMember[] | null>(null);
  const [invites, setInvites] = useState<TenantInvite[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [inviteEmailHint, setInviteEmailHint] = useState("");
  const [creating, setCreating] = useState(false);
  const [lastGenerated, setLastGenerated] = useState<TenantInvite | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const user = await api.get<PortalUser>("/portal/me");
      setMe(user);
      const m = await api.get<MembersResponse>("/portal/team/members");
      setMembers(m.members);
      if (user.tenant_role === "owner") {
        const inv = await api.get<InvitesResponse>("/portal/team/invites");
        setInvites(inv.invites);
      } else {
        setInvites(null);
      }
    } catch (err) {
      setError(
        err instanceof PortalApiError
          ? err.message
          : "Failed to load team data."
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleGenerateInvite(e: FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      const invite = await api.post<TenantInvite>(
        "/portal/team/invites",
        { email_hint: inviteEmailHint.trim() || undefined }
      );
      setLastGenerated(invite);
      setInviteEmailHint("");
      await refresh();
    } catch (err) {
      setError(
        err instanceof PortalApiError
          ? err.message
          : "Failed to create invite."
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(inviteId: string) {
    if (!confirm("Revoke this invite? The code will stop working immediately.")) {
      return;
    }
    try {
      await api.delete(`/portal/team/invites/${inviteId}`);
      await refresh();
    } catch (err) {
      setError(
        err instanceof PortalApiError
          ? err.message
          : "Failed to revoke invite."
      );
    }
  }

  async function handleRemoveMember(memberId: string, email: string) {
    const self = me && memberId === me.id;
    const prompt = self
      ? "Leave this team? You'll lose access until you're invited back."
      : `Remove ${email} from the team? Their existing sessions will end.`;
    if (!confirm(prompt)) return;
    try {
      await api.delete(`/portal/team/members/${memberId}`);
      if (self) {
        // Current user left their tenant — bounce to login.
        window.location.href = "/login";
        return;
      }
      await refresh();
    } catch (err) {
      setError(
        err instanceof PortalApiError
          ? err.message
          : "Failed to remove member."
      );
    }
  }

  function copyCode(code: string) {
    navigator.clipboard.writeText(code);
    setCopied(code);
    setTimeout(() => setCopied(null), 2000);
  }

  if (loading) return <PageSpinner />;
  if (error) {
    return (
      <div>
        <PageHeader title="Team" subtitle="Manage your team members and invites." />
        <Card>
          <p className="text-sm text-red-600">{error}</p>
        </Card>
      </div>
    );
  }
  if (!me) return null;

  const isOwner = me.tenant_role === "owner";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Team"
        subtitle={
          isOwner
            ? "Invite teammates and manage membership."
            : "Your team members. Only the owner can invite new people."
        }
      />

      {isOwner && (
        <Card>
          <h2 className="text-lg font-semibold mb-3">Create invite</h2>
          <form onSubmit={handleGenerateInvite} className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="block text-sm font-medium mb-1">
                Email hint (optional — display only)
              </label>
              <Input
                type="email"
                placeholder="bob@teammate.com"
                value={inviteEmailHint}
                onChange={(e) => setInviteEmailHint(e.target.value)}
              />
            </div>
            <Button type="submit" disabled={creating}>
              {creating ? "Creating…" : "Generate code"}
            </Button>
          </form>
          {lastGenerated && (
            <div className="mt-4 p-3 bg-green-50 border border-green-200 rounded">
              <p className="text-sm font-medium mb-1">Invite created.</p>
              <p className="text-xs text-gray-600 mb-2">
                Send this code to your teammate via Slack, email, or any
                other channel. Expires {formatDate(lastGenerated.expires_at)}.
              </p>
              <div className="flex gap-2 items-center">
                <code className="flex-1 text-xs p-2 bg-white border rounded break-all">
                  {lastGenerated.code}
                </code>
                <Button
                  type="button"
                  onClick={() => copyCode(lastGenerated.code)}
                  variant="secondary"
                >
                  {copied === lastGenerated.code ? "Copied" : <Copy size={14} />}
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {isOwner && invites && invites.length > 0 && (
        <Card>
          <h2 className="text-lg font-semibold mb-3">Pending invites</h2>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-gray-500">
              <tr>
                <th className="py-2">Hint</th>
                <th className="py-2">Code</th>
                <th className="py-2">Expires</th>
                <th className="py-2 w-20"></th>
              </tr>
            </thead>
            <tbody>
              {invites.map((inv) => (
                <tr key={inv.id} className="border-t">
                  <td className="py-2">
                    <Mail size={14} className="inline mr-1 text-gray-400" />
                    {inv.email_hint || <em>unspecified</em>}
                  </td>
                  <td className="py-2 font-mono text-xs">
                    <button
                      type="button"
                      onClick={() => copyCode(inv.code)}
                      className="underline hover:opacity-80"
                    >
                      {copied === inv.code ? "Copied!" : inv.code.slice(0, 8) + "…"}
                    </button>
                  </td>
                  <td className="py-2">{formatDate(inv.expires_at)}</td>
                  <td className="py-2">
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => handleRevoke(inv.id)}
                      aria-label="Revoke invite"
                    >
                      <Trash2 size={14} />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card>
        <h2 className="text-lg font-semibold mb-3">
          Members ({members?.length ?? 0})
        </h2>
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-gray-500">
            <tr>
              <th className="py-2">Email</th>
              <th className="py-2">Name</th>
              <th className="py-2">Role</th>
              <th className="py-2">Joined</th>
              <th className="py-2 w-20"></th>
            </tr>
          </thead>
          <tbody>
            {members?.map((m) => {
              const canRemove = isOwner || m.id === me.id;
              return (
                <tr key={m.id} className="border-t">
                  <td className="py-2">{m.email}</td>
                  <td className="py-2">
                    {m.display_name || <em className="text-gray-400">—</em>}
                  </td>
                  <td className="py-2">
                    {m.tenant_role === "owner" ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700">
                        <Crown size={12} /> Owner
                      </span>
                    ) : (
                      <span className="text-xs text-gray-600">Member</span>
                    )}
                  </td>
                  <td className="py-2">{formatDate(m.created_at)}</td>
                  <td className="py-2">
                    {canRemove && (
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => handleRemoveMember(m.id, m.email)}
                        aria-label={
                          m.id === me.id ? "Leave team" : "Remove member"
                        }
                      >
                        <UserMinus size={14} />
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
