"use client";

import { useMemo, useState } from "react";

import ConfirmModal from "../../components/ui/ConfirmModal";
import EmptyState from "../../components/ui/EmptyState";
import ErrorState from "../../components/ui/ErrorState";
import Modal from "../../components/ui/Modal";
import StatCard from "../../components/ui/StatCard";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useToast } from "../../components/ui/Toast";
import { api, ApiError } from "../../lib/api";
import { useApi } from "../../lib/useApi";
import { ThemeToggle } from "../../components/ThemeToggle";
import { Icon } from "../../components/ui/Icon";

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
        <StatCard label="Total Packs" value={stats.total} />
        <StatCard label="Active" value={stats.active} />
        <StatCard label="Price Range" value={stats.priceRange} />
      </div>

      <div className="sran-card" style={{ padding: 0, overflow: "hidden" }}>
        {loading ? (
          <div style={{ padding: 20 }}>
            <SkeletonTable rows={4} />
          </div>
        ) : error ? (
          <ErrorState message={error} onRetry={refetch} />
        ) : sorted.length === 0 ? (
          <EmptyState icon={<Icon name="gem" size={22} />} message="No token packs yet." action={
            <button className="sran-btn sran-btn-primary" onClick={openCreate}>Create your first pack</button>
          } />
        ) : (
          <div className="overflow-x-auto">
          <table className="sran-table">
            <thead>
              <tr>
                <th style={{ paddingLeft: 20 }}>Name</th>
                <th>Tokens</th>
                <th>Price</th>
                <th>Description</th>
                <th style={{ textAlign: "center", width: 100 }}>Status</th>
                <th style={{ width: 80, textAlign: "center" }}>Order</th>
                <th style={{ width: 180, textAlign: "right", paddingRight: 20 }}></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => (
                <tr key={p.id}>
                  <td style={{ paddingLeft: 20 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>{p.name}</span>
                      {p.id === popularId ? (
                        <span className="sran-badge sran-badge-info" style={{ fontSize: 9.5, padding: "2px 6px" }}><Icon name="star" size={11} /> Popular</span>
                      ) : null}
                    </div>
                  </td>
                  <td style={{ fontSize: 13, color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                    {formatTokensShort(p.tokens)} tokens
                  </td>
                  <td style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{formatUsd(p.price_usd)}</td>
                  <td style={{ fontSize: 12, color: "var(--text-tertiary)", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {p.description || "—"}
                  </td>
                  <td style={{ textAlign: "center" }}>
                    <span className={`sran-badge ${p.is_active ? "sran-badge-success" : "sran-badge-neutral"}`}>
                      {p.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td style={{ textAlign: "center", fontSize: 12, color: "var(--text-tertiary)" }}>{p.sort_order}</td>
                  <td style={{ textAlign: "right", paddingRight: 20 }}>
                    <div style={{ display: "inline-flex", gap: 6 }}>
                      <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => openEdit(p)}>
                        Edit
                      </button>
                      <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => toggleActive(p)}>
                        {p.is_active ? "Deactivate" : "Activate"}
                      </button>
                      <button className="sran-btn sran-btn-danger sran-btn-sm" onClick={() => setDeleteTarget(p)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {/* Create / edit modal */}
      <Modal
        open={createOpen}
        onClose={closeForm}
        title={editPack ? "Edit Token Pack" : "New Token Pack"}
        footer={
          <>
            <button className="sran-btn sran-btn-secondary" onClick={closeForm} disabled={saving}>
              Cancel
            </button>
            <button className="sran-btn sran-btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : editPack ? "Save Changes" : "Create Pack"}
            </button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
          {formErr ? (
            <div style={{ fontSize: 12, color: "var(--accent-danger)", background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.25)", padding: "6px 10px", borderRadius: 6 }}>
              {formErr}
            </div>
          ) : null}

          <div>
            <label className="sran-label">Name *</label>
            <input
              className="sran-input"
              placeholder="e.g. Enterprise"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              autoFocus
            />
          </div>

          <div>
            <label className="sran-label">Tokens *</label>
            <input
              type="number"
              min={1}
              className="sran-input"
              placeholder="50000000"
              value={form.tokens}
              onChange={(e) => setForm((f) => ({ ...f, tokens: e.target.value }))}
            />
            {form.tokens ? (
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                = {formatTokensShort(parseInt(form.tokens, 10) || 0)} tokens
              </div>
            ) : null}
          </div>

          <div>
            <label className="sran-label">Price (USD) *</label>
            <input
              type="number"
              step="0.01"
              min={0.01}
              className="sran-input"
              placeholder="500.00"
              value={form.price_usd}
              onChange={(e) => setForm((f) => ({ ...f, price_usd: e.target.value }))}
            />
          </div>

          <div>
            <label className="sran-label">Description</label>
            <input
              className="sran-input"
              placeholder="Short tagline shown in the portal"
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              maxLength={200}
            />
          </div>

          <div>
            <label className="sran-label">Sort Order</label>
            <input
              type="number"
              min={0}
              className="sran-input"
              style={{ maxWidth: 160 }}
              value={form.sort_order}
              onChange={(e) => setForm((f) => ({ ...f, sort_order: e.target.value }))}
            />
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
              Lower values render first. The seed pack with <code>sort_order = 2</code> is shown as
              "Most Popular" in the portal.
            </div>
          </div>

          {editPack ? (
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <input
                type="checkbox"
                id="is_active"
                checked={!!form.is_active}
                onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
              />
              <label htmlFor="is_active" style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                Pack is active (visible in the portal)
              </label>
            </div>
          ) : null}
        </div>
      </Modal>

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
