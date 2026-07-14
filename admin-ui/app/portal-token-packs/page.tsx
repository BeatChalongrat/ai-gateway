"use client";

import { useMemo, useState } from "react";

import ConfirmModal from "../../components/ui/ConfirmModal";
import ErrorState from "../../components/ui/ErrorState";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { api, ApiError } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { ThemeToggle } from "../../components/ThemeToggle";

interface TokenPack {
  id: string;
  name: string;
  tokens: number;
  price_usd: number;
  description: string | null;
  stripe_price_id: string | null;
  is_active: boolean;
  sort_order: number;
  created_at: string;
}

interface PackForm {
  name: string;
  tokens: string;
  price_usd: string;
  description: string;
  sort_order: string;
  is_active?: boolean;
}

const EMPTY_FORM: PackForm = {
  name: "",
  tokens: "",
  price_usd: "",
  description: "",
  sort_order: "",
  is_active: true,
};

function formatTokensShort(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return n.toLocaleString();
}

function formatUsd(n: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

function errorMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.body?.error?.message || fallback;
  return fallback;
}

const modalLabel: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  fontWeight: 600,
  color: "var(--text-secondary)",
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  marginBottom: 6,
};

// ---------------------------------------------------------------------------
// Inline lucide-geometry icons (the app ships no icon library)
// ---------------------------------------------------------------------------

function LucideIcon({ size = 16, children }: { size?: number; children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {children}
    </svg>
  );
}

const ICON_PACKAGE = (
  <>
    <path d="M16.5 9.4 7.55 4.24" />
    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
    <polyline points="3.29 7 12 12 20.71 7" />
    <line x1="12" y1="22" x2="12" y2="12" />
  </>
);
const ICON_CHECK_CIRCLE = (
  <>
    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
    <polyline points="22 4 12 14.01 9 11.01" />
  </>
);
const ICON_TAG = (
  <>
    <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
    <line x1="7" y1="7" x2="7.01" y2="7" />
  </>
);
const ICON_ZAP = <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />;

// Stat card — inset top accent line (.pack-stat-card::before) + a tinted
// icon square in the top-right corner.
function PackStat({
  accent,
  iconBg,
  iconColor,
  icon,
  label,
  value,
  sub,
}: {
  accent: string;
  iconBg: string;
  iconColor: string;
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="pack-stat-card" style={{ ["--stat-accent" as string]: accent }}>
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: 14,
          right: 14,
          width: 32,
          height: 32,
          borderRadius: 8,
          background: iconBg,
          color: iconColor,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {icon}
      </div>
      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.3px" }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", marginTop: 4, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{sub}</div>
    </div>
  );
}

export default function TokenPacksPage() {
  const { data, loading, error, refetch } = useApi<TokenPack[]>("/admin/token-packs");
  const { toast } = useToast();
  const packs = data ?? [];

  const sorted = useMemo(
    () => [...packs].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name)),
    [packs]
  );

  const stats = useMemo(() => {
    const total = packs.length;
    const active = packs.filter((p) => p.is_active).length;
    const prices = packs.map((p) => p.price_usd).sort((a, b) => a - b);
    const priceRange =
      prices.length === 0
        ? "—"
        : prices[0] === prices[prices.length - 1]
        ? formatUsd(prices[0])
        : `${formatUsd(prices[0])} – ${formatUsd(prices[prices.length - 1])}`;
    return { total, active, priceRange };
  }, [packs]);

  // Most-popular heuristic: sort_order === 2 (matches seed data convention).
  const popularId = useMemo(() => {
    const p = sorted.find((x) => x.sort_order === 2 && x.is_active);
    return p?.id ?? null;
  }, [sorted]);

  const [createOpen, setCreateOpen] = useState(false);
  const [editPack, setEditPack] = useState<TokenPack | null>(null);
  const [form, setForm] = useState<PackForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<TokenPack | null>(null);
  const [deleting, setDeleting] = useState(false);

  function openCreate() {
    setEditPack(null);
    setForm({ ...EMPTY_FORM, sort_order: String(packs.length + 1) });
    setFormErr(null);
    setCreateOpen(true);
  }

  function openEdit(p: TokenPack) {
    setEditPack(p);
    setForm({
      name: p.name,
      tokens: String(p.tokens),
      price_usd: String(p.price_usd),
      description: p.description ?? "",
      sort_order: String(p.sort_order),
      is_active: p.is_active,
    });
    setFormErr(null);
    setCreateOpen(true);
  }

  function closeForm() {
    if (saving) return;
    setCreateOpen(false);
    setEditPack(null);
    setForm(EMPTY_FORM);
    setFormErr(null);
  }

  async function handleSave() {
    setFormErr(null);
    if (!form.name.trim()) return setFormErr("Name is required.");
    const tokens = parseInt(form.tokens, 10);
    if (!Number.isFinite(tokens) || tokens <= 0) return setFormErr("Tokens must be a positive number.");
    const price = parseFloat(form.price_usd);
    if (!Number.isFinite(price) || price <= 0) return setFormErr("Price must be a positive number.");
    const sortOrder = parseInt(form.sort_order || "0", 10);

    const payload: Record<string, unknown> = {
      name: form.name.trim(),
      tokens,
      price_usd: price,
      description: form.description.trim() || null,
      sort_order: Number.isFinite(sortOrder) ? sortOrder : 0,
    };
    if (editPack) payload.is_active = form.is_active ?? true;

    setSaving(true);
    try {
      if (editPack) {
        await api.patch(`/admin/token-packs/${editPack.id}`, payload);
        toast("Pack updated");
      } else {
        await api.post("/admin/token-packs", payload);
        toast("Pack created");
      }
      closeForm();
      refetch();
    } catch (e) {
      setFormErr(errorMessage(e, "Save failed"));
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(p: TokenPack) {
    try {
      await api.patch(`/admin/token-packs/${p.id}`, { is_active: !p.is_active });
      toast(p.is_active ? "Pack deactivated" : "Pack activated");
      refetch();
    } catch (e) {
      toast(errorMessage(e, "Failed to update"), "error");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/admin/token-packs/${deleteTarget.id}`);
      toast("Pack deactivated");
      setDeleteTarget(null);
      refetch();
    } catch (e) {
      toast(errorMessage(e, "Failed to delete"), "error");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="p-4 sm:p-6 lg:p-10" style={{ maxWidth: 1400, margin: "0 auto" }}>
      <div className="sran-page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h1 className="sran-page-title">Token Packs</h1>
          <p className="sran-page-subtitle">
            Configure available token packages for portal users.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button className="sran-btn sran-btn-primary" onClick={openCreate}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 4 }}>
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
            New Pack
          </button>
          <ThemeToggle />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3" style={{ gap: 14, marginBottom: 20 }}>
        <PackStat
          accent="#3B82F6"
          iconBg="var(--icon-bg-blue)"
          iconColor="#3B82F6"
          icon={<LucideIcon size={16}>{ICON_PACKAGE}</LucideIcon>}
          label="Total Packs"
          value={String(stats.total)}
          sub="All configured"
        />
        <PackStat
          accent="#10B981"
          iconBg="var(--icon-bg-green)"
          iconColor="#10B981"
          icon={<LucideIcon size={16}>{ICON_CHECK_CIRCLE}</LucideIcon>}
          label="Active"
          value={String(stats.active)}
          sub="Visible in portal"
        />
        <PackStat
          accent="#F59E0B"
          iconBg="var(--icon-bg-amber)"
          iconColor="#F59E0B"
          icon={<LucideIcon size={16}>{ICON_TAG}</LucideIcon>}
          label="Price Range"
          value={stats.priceRange}
          sub="USD per pack"
        />
      </div>

      {loading ? (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-color)", borderRadius: 12, padding: 20 }}>
          <SkeletonTable rows={4} />
        </div>
      ) : error ? (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-color)", borderRadius: 12, padding: 20 }}>
          <ErrorState message={error} onRetry={refetch} />
        </div>
      ) : sorted.length === 0 ? (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-color)", borderRadius: 12, padding: 48, textAlign: "center" }}>
          <span style={{ color: "var(--border-color)", display: "inline-flex" }}>
            <LucideIcon size={48}>{ICON_PACKAGE}</LucideIcon>
          </span>
          <p style={{ fontSize: 16, color: "var(--text-muted)", margin: "12px 0" }}>No token packs yet.</p>
          <button className="sran-btn sran-btn-primary" onClick={openCreate}>Create your first pack</button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {sorted.map((p) => {
            const popular = p.id === popularId;
            const ratePerM = p.tokens > 0 ? (p.price_usd / p.tokens) * 1_000_000 : 0;
            return (
              <div
                key={p.id}
                style={{
                  background: "var(--bg-card)",
                  border: `1px solid ${popular ? "#3B82F6" : "var(--border-color)"}`,
                  borderRadius: 12,
                  padding: 20,
                  position: "relative",
                  opacity: p.is_active ? 1 : 0.6,
                }}
              >
                {popular ? (
                  <span
                    style={{
                      position: "absolute",
                      top: -1,
                      right: 16,
                      background: "#3B82F6",
                      color: "#fff",
                      fontSize: 10,
                      fontWeight: 600,
                      padding: "2px 8px",
                      borderRadius: "0 0 6px 6px",
                      letterSpacing: "0.03em",
                    }}
                  >
                    MOST POPULAR
                  </span>
                ) : null}

                <div style={{ fontSize: 18, fontWeight: 600, color: "var(--text-primary)" }}>{p.name}</div>
                <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 2, lineHeight: 1.4 }}>
                  {p.description || "—"}
                </div>

                <div style={{ fontSize: 28, fontWeight: 700, color: "var(--text-primary)", marginTop: 12, fontVariantNumeric: "tabular-nums" }}>
                  {formatUsd(p.price_usd)}
                  <span style={{ fontSize: 14, fontWeight: 400, color: "var(--text-muted)" }}> / pack</span>
                </div>
                <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ color: "#F59E0B", display: "inline-flex" }}>
                    <LucideIcon size={14}>{ICON_ZAP}</LucideIcon>
                  </span>
                  {p.tokens.toLocaleString()} tokens
                </div>

                <div
                  style={{
                    marginTop: 14,
                    paddingTop: 10,
                    borderTop: "1px solid var(--border-color)",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    ${ratePerM.toFixed(2)} / 1M tokens
                  </span>
                  <span
                    className={p.is_active ? "pack-badge-active" : "pack-badge-draft"}
                    style={{ fontSize: 11, padding: "2px 8px", borderRadius: 10, fontWeight: 600 }}
                  >
                    {p.is_active ? "Active" : "Draft"}
                  </span>
                </div>

                <div style={{ marginTop: 10, display: "flex", gap: 6 }}>
                  <button
                    onClick={() => openEdit(p)}
                    style={{
                      background: "transparent",
                      border: "1px solid var(--border-color)",
                      color: "var(--text-secondary)",
                      fontSize: 12,
                      padding: "4px 10px",
                      borderRadius: 6,
                      cursor: "pointer",
                      fontFamily: "inherit",
                    }}
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => toggleActive(p)}
                    style={{
                      background: "transparent",
                      border: "1px solid var(--border-color)",
                      color: "var(--text-secondary)",
                      fontSize: 12,
                      padding: "4px 10px",
                      borderRadius: 6,
                      cursor: "pointer",
                      fontFamily: "inherit",
                    }}
                  >
                    {p.is_active ? "Disable" : "Enable"}
                  </button>
                  {!p.is_active ? (
                    <button
                      onClick={() => setDeleteTarget(p)}
                      style={{
                        background: "transparent",
                        border: "1px solid rgba(239, 68, 68, 0.3)",
                        color: "#FCA5A5",
                        fontSize: 12,
                        padding: "4px 10px",
                        borderRadius: 6,
                        cursor: "pointer",
                        fontFamily: "inherit",
                      }}
                    >
                      Delete
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create / edit modal — custom dark-compatible dialog */}
      {createOpen ? (
        <div
          onClick={closeForm}
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.6)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border-color)",
              borderRadius: 14,
              width: 460,
              maxWidth: "90vw",
              overflow: "hidden",
            }}
          >
            {/* Header */}
            <div
              style={{
                padding: "18px 24px",
                borderBottom: "1px solid var(--border-color)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span style={{ fontSize: 18, fontWeight: 600, color: "var(--text-primary)" }}>
                {editPack ? "Edit Token Pack" : "New Token Pack"}
              </span>
              <button
                onClick={closeForm}
                aria-label="Close"
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)", padding: 2, display: "inline-flex" }}
                onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text-primary)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-muted)"; }}
              >
                <LucideIcon size={16}>
                  <>
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </>
                </LucideIcon>
              </button>
            </div>

            {/* Body */}
            <div style={{ padding: 24 }}>
              {formErr ? (
                <div style={{ fontSize: 12, color: "var(--accent-danger)", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.25)", padding: "6px 10px", borderRadius: 6, marginBottom: 18 }}>
                  {formErr}
                </div>
              ) : null}

              <div style={{ marginBottom: 18 }}>
                <label style={modalLabel}>Name <span style={{ color: "#EF4444" }}>*</span></label>
                <input
                  className="pack-input"
                  placeholder="e.g. Enterprise"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  autoFocus
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 18 }}>
                <div>
                  <label style={modalLabel}>Tokens <span style={{ color: "#EF4444" }}>*</span></label>
                  <input
                    type="number"
                    min={1}
                    className="pack-input"
                    placeholder="50000000"
                    value={form.tokens}
                    onChange={(e) => setForm((f) => ({ ...f, tokens: e.target.value }))}
                  />
                  {form.tokens ? (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                      = {formatTokensShort(parseInt(form.tokens, 10) || 0)} tokens
                    </div>
                  ) : null}
                </div>
                <div>
                  <label style={modalLabel}>Price (USD) <span style={{ color: "#EF4444" }}>*</span></label>
                  <input
                    type="number"
                    step="0.01"
                    min={0.01}
                    className="pack-input"
                    placeholder="500.00"
                    value={form.price_usd}
                    onChange={(e) => setForm((f) => ({ ...f, price_usd: e.target.value }))}
                  />
                </div>
              </div>

              <div style={{ marginBottom: 18 }}>
                <label style={modalLabel}>Description</label>
                <input
                  className="pack-input"
                  placeholder="Short tagline shown in the portal"
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  maxLength={200}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <div>
                  <label style={modalLabel}>Sort Order</label>
                  <input
                    type="number"
                    min={0}
                    className="pack-input"
                    value={form.sort_order}
                    onChange={(e) => setForm((f) => ({ ...f, sort_order: e.target.value }))}
                  />
                </div>
                <div>
                  <label style={modalLabel}>Status</label>
                  <select
                    className="pack-input"
                    value={form.is_active === false ? "draft" : "active"}
                    onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.value === "active" }))}
                    disabled={!editPack}
                    title={editPack ? undefined : "New packs are created active"}
                  >
                    <option value="active">Active</option>
                    <option value="draft">Draft</option>
                  </select>
                </div>
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
                Tip: sort_order = 2 is shown as &quot;Most Popular&quot; in the portal.
              </div>
            </div>

            {/* Footer */}
            <div
              style={{
                padding: "14px 24px",
                borderTop: "1px solid var(--border-color)",
                display: "flex",
                justifyContent: "flex-end",
                gap: 10,
              }}
            >
              <button
                onClick={closeForm}
                disabled={saving}
                style={{
                  background: "transparent",
                  border: "1px solid var(--border-color)",
                  color: "var(--text-secondary)",
                  padding: "8px 18px",
                  borderRadius: 8,
                  fontSize: 13,
                  cursor: saving ? "not-allowed" : "pointer",
                  fontFamily: "inherit",
                }}
              >
                Cancel
              </button>
              <button className="pack-btn-navy" onClick={handleSave} disabled={saving}>
                {saving ? "Saving…" : editPack ? "Save Changes" : "Create Pack"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => !deleting && setDeleteTarget(null)}
        onConfirm={confirmDelete}
        title="Delete token pack?"
        message={`Delete "${deleteTarget?.name}"? The pack is soft-deleted and hidden from the portal. You can re-activate it later.`}
        confirmLabel="Delete"
        loading={deleting}
      />
    </div>
  );
}
