"use client";

import { useState, useEffect, useCallback } from "react";
import PageHeader from "../../components/ui/PageHeader";
import Badge from "../../components/ui/Badge";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { SkeletonCards } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";

interface ProviderKey {
  id: string;
  provider_name: string;
  label: string;
  key_masked: string;
  is_active: boolean;
  last_tested_at: string | null;
  last_test_status: string;
  last_test_latency_ms: number | null;
  created_at: string;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function StatusBadge({ status }: { status: string }) {
  if (status === "valid") return <Badge variant="success">valid</Badge>;
  if (status === "invalid") return <Badge variant="error">invalid</Badge>;
  return <Badge variant="neutral">untested</Badge>;
}

/* ── Logo ────────────────────────────────────────────────────────── */

function ProviderLogo({ name }: { name: string }) {
  const [failed, setFailed] = useState(false);
  const src = `/providers/${name.toLowerCase()}.png`;
  const initial = name.charAt(0).toUpperCase();

  const base: React.CSSProperties = {
    width: 44, height: 44, borderRadius: "10px", flexShrink: 0,
    border: "1px solid var(--color-border-default)",
    boxShadow: "0 1px 2px rgba(15, 31, 56, 0.05)",
  };

  if (failed) {
    return (
      <div style={{
        ...base,
        background: "linear-gradient(135deg, var(--color-surface-secondary) 0%, var(--color-surface-tertiary) 100%)",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: "1.125rem", fontWeight: 700, color: "var(--color-text-muted)",
        textTransform: "uppercase",
      }}>{initial}</div>
    );
  }

  return (
    <img
      src={src} alt={name} onError={() => setFailed(true)}
      style={{ ...base, objectFit: "contain", background: "var(--color-surface-tertiary)" }}
    />
  );
}

/* ── Keys management panel ──────────────────────────────────────── */

function KeysPanel({ providerName, onClose }: { providerName: string; onClose: () => void }) {
  const [keys, setKeys] = useState<ProviderKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [label, setLabel] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const { toast } = useToast();

  const loadKeys = useCallback(async () => {
    try {
      const data = await api.get(`/admin/providers/${providerName}/keys`);
      setKeys(data.items || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, [providerName]);

  useEffect(() => { loadKeys(); }, [loadKeys]);

  async function handleAdd() {
    if (!keyInput.trim()) return;
    setAdding(true);
    try {
      const result = await api.post(`/admin/providers/${providerName}/keys`, {
        api_key: keyInput.trim(),
        label: label.trim() || undefined,
      });
      toast(`Key validated (${result.latency_ms}ms) and saved`);
      setKeyInput("");
      setLabel("");
      loadKeys();
    } catch (e: any) {
      const msg = e.body?.error?.message || "Failed to add key";
      toast(msg, "error");
    } finally {
      setAdding(false);
    }
  }

  async function handleTest(keyId: string) {
    setTestingId(keyId);
    try {
      const result = await api.post(`/admin/providers/${providerName}/keys/${keyId}/test`, {});
      if (result.valid) toast(`Key valid (${result.latency_ms}ms)`);
      else toast(`Key invalid: ${result.error}`, "error");
      loadKeys();
    } catch { toast("Test failed", "error"); }
    finally { setTestingId(null); }
  }

  async function handleDelete(keyId: string) {
    try {
      await api.delete(`/admin/providers/${providerName}/keys/${keyId}`);
      toast("Key deactivated");
      loadKeys();
    } catch { toast("Failed to delete", "error"); }
  }

  const activeKeys = keys.filter((k) => k.is_active);
  const validCount = activeKeys.filter((k) => k.last_test_status === "valid").length;

  return (
    <div style={{
      background: "var(--color-surface-primary)",
      border: "1px solid var(--color-border-default)",
      borderRadius: "var(--radius-lg)",
      boxShadow: "0 1px 3px rgba(15, 31, 56, 0.05), 0 1px 2px rgba(15, 31, 56, 0.03)",
      padding: "1.25rem 1.5rem",
      gridColumn: "1 / -1",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h4 style={{ margin: 0, fontSize: "var(--font-size-base)", fontWeight: 600 }}>
          API Keys for <span style={{ textTransform: "capitalize" }}>{providerName}</span>
          {activeKeys.length > 0 && (
            <span style={{ marginLeft: "0.5rem", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", fontWeight: 400 }}>
              {validCount} valid / {activeKeys.length} total
            </span>
          )}
        </h4>
        <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={onClose}>Close</button>
      </div>

      {loading ? (
        <div className="sran-empty" style={{ padding: "1rem" }}>Loading keys...</div>
      ) : activeKeys.length > 0 ? (
        <div style={{ overflowX: "auto", marginBottom: "1.25rem" }}>
          <table className="sran-table">
            <thead>
              <tr>
                <th>Label</th>
                <th>Key</th>
                <th>Status</th>
                <th>Latency</th>
                <th>Last Tested</th>
                <th style={{ width: "140px" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {activeKeys.map((k) => (
                <tr key={k.id}>
                  <td style={{ fontWeight: 500 }}>{k.label || "—"}</td>
                  <td><code style={{ fontSize: "var(--font-size-xs)" }}>{k.key_masked}</code></td>
                  <td><StatusBadge status={k.last_test_status} /></td>
                  <td>{k.last_test_latency_ms ? `${k.last_test_latency_ms}ms` : "—"}</td>
                  <td style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>{timeAgo(k.last_tested_at)}</td>
                  <td>
                    <div style={{ display: "flex", gap: "0.375rem" }}>
                      <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => handleTest(k.id)} disabled={testingId === k.id}>
                        {testingId === k.id ? "..." : "Test"}
                      </button>
                      <button className="sran-btn sran-btn-danger sran-btn-sm" onClick={() => handleDelete(k.id)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ padding: "0.75rem 0", color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)", marginBottom: "1rem" }}>
          No keys configured. Add one below.
        </div>
      )}

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", flexWrap: "wrap" }}>
        <div style={{ flex: "0 0 140px" }}>
          <label className="sran-label">Label</label>
          <input className="sran-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Primary" style={{ fontSize: "var(--font-size-sm)" }} />
        </div>
        <div style={{ flex: 1, minWidth: "200px" }}>
          <label className="sran-label">API Key *</label>
          <input className="sran-input" type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} placeholder={providerName === "openai" ? "sk-..." : providerName === "anthropic" ? "sk-ant-..." : "sk-or-..."} style={{ fontSize: "var(--font-size-sm)" }} />
        </div>
        <button className="sran-btn sran-btn-primary" onClick={handleAdd} disabled={adding || !keyInput.trim()} style={{ whiteSpace: "nowrap" }}>
          {adding ? "Validating..." : "Add & Validate"}
        </button>
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function ProvidersPage() {
  const { data, loading, error, refetch } = useApi<{ items: any[] }>("/admin/providers");
  const [keyStatuses, setKeyStatuses] = useState<Record<string, any>>({});
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);

  useEffect(() => {
    if (!data?.items) return;
    data.items.forEach(async (p: any) => {
      try {
        const status = await api.get(`/admin/providers/${p.provider_name}/key/status`);
        setKeyStatuses((prev) => ({ ...prev, [p.provider_name]: status }));
      } catch { /* ignore */ }
    });
  }, [data]);

  const items = data?.items ?? [];

  // Build grid: cards + full-width panel injected after each row-pair
  const gridItems: React.ReactNode[] = [];

  for (let i = 0; i < items.length; i++) {
    const p = items[i];
    const ks = keyStatuses[p.provider_name];
    const totalKeys = ks?.total_keys ?? 0;
    const validKeys = ks?.valid_keys ?? 0;
    const isExpanded = expandedProvider === p.provider_name;

    const keyBadge = (() => {
      if (!ks) return null;
      if (totalKeys > 0) return <Badge variant={validKeys > 0 ? "success" : "warning"}>{validKeys}/{totalKeys} keys valid</Badge>;
      if (!ks.configured) return <Badge variant="error">No Key</Badge>;
      if (ks.source === "environment") return <span title="Key loaded from environment variable"><Badge variant="warning">Via env</Badge></span>;
      return <Badge variant="success">Key Set</Badge>;
    })();

    // Provider card
    gridItems.push(
      <div key={p.provider_name} style={{
        background: "linear-gradient(180deg, rgba(238,242,248,0.55) 0%, var(--color-surface-primary) 42%)",
        border: `1px solid ${isExpanded ? "var(--color-border-strong)" : "var(--color-border-default)"}`,
        borderRadius: "var(--radius-xl)",
        boxShadow: "0 1px 3px rgba(15, 31, 56, 0.06), 0 1px 2px rgba(15, 31, 56, 0.03)",
        padding: "1.375rem 1.5rem 1.25rem",
        display: "flex", flexDirection: "column",
        transform: "translateY(0)",
        transition: "border-color 180ms, box-shadow 180ms, transform 180ms",
      }}
        onMouseEnter={(e) => {
          e.currentTarget.style.boxShadow = "0 6px 16px -3px rgba(15, 31, 56, 0.10), 0 2px 4px rgba(15, 31, 56, 0.04)";
          e.currentTarget.style.borderColor = "var(--color-border-strong)";
          e.currentTarget.style.transform = "translateY(-1px)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.boxShadow = "0 1px 3px rgba(15, 31, 56, 0.06), 0 1px 2px rgba(15, 31, 56, 0.03)";
          if (!isExpanded) e.currentTarget.style.borderColor = "var(--color-border-default)";
          e.currentTarget.style.transform = "translateY(0)";
        }}
      >
        {/* Identity block: logo + name + status dot + key metadata */}
        <div style={{ display: "flex", gap: "0.875rem", marginBottom: "0.875rem" }}>
          <ProviderLogo name={p.provider_name} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <h3 style={{
                fontSize: "1.0625rem", fontWeight: 700, margin: 0,
                textTransform: "capitalize", color: "var(--color-text-primary)",
                lineHeight: 1.2, letterSpacing: "-0.01em",
              }}>{p.provider_name}</h3>
              <span style={{
                width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                background: p.enabled ? "#10b981" : "#cbd5e1",
                boxShadow: p.enabled ? "0 0 0 2.5px rgba(16, 185, 129, 0.14)" : "none",
              }} title={p.enabled ? "Active" : "Disabled"} />
            </div>
            <div style={{
              display: "flex", alignItems: "center", gap: "0.375rem",
              flexWrap: "wrap", marginTop: "0.3125rem",
            }}>
              {keyBadge}
              {ks?.source && (
                <span style={{
                  fontSize: "0.5625rem", fontWeight: 600, color: "var(--color-text-muted)",
                  letterSpacing: "0.03em", textTransform: "uppercase",
                  padding: "0.0625rem 0.375rem",
                  background: "var(--color-surface-tertiary)",
                  borderRadius: "var(--radius-sm)",
                }}>
                  {ks.source}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Models */}
        <div style={{
          paddingTop: "0.6875rem",
          borderTop: "1px solid rgba(0, 0, 0, 0.045)",
          marginTop: "auto",
        }}>
          <div style={{
            display: "flex", flexWrap: "wrap", gap: "0.3125rem", alignItems: "center",
            marginBottom: "0.75rem",
          }}>
            <span style={{
              fontSize: "0.625rem", fontWeight: 600, color: "var(--color-text-muted)",
              textTransform: "uppercase", letterSpacing: "0.04em",
              marginRight: "0.125rem",
            }}>{p.models.length} model{p.models.length !== 1 ? "s" : ""}</span>
            {p.models.slice(0, 4).map((m: string) => (
              <code key={m} style={{
                padding: "0.125rem 0.5rem",
                background: "var(--color-surface-tertiary)",
                borderRadius: "var(--radius-sm)",
                fontSize: "0.625rem",
                color: "var(--color-text-muted)",
                lineHeight: 1.6,
                fontWeight: 500,
              }}>{m}</code>
            ))}
            {p.models.length > 4 && (
              <span style={{ fontSize: "0.625rem", color: "var(--color-text-muted)", fontWeight: 500 }}>
                +{p.models.length - 4}
              </span>
            )}
          </div>

          {/* Action */}
          <button
            onClick={() => setExpandedProvider(isExpanded ? null : p.provider_name)}
            style={{
              background: isExpanded ? "var(--color-surface-tertiary)" : "transparent",
              border: `1px solid ${isExpanded ? "var(--color-border-strong)" : "var(--color-border-default)"}`,
              borderRadius: "var(--radius-md)",
              cursor: "pointer",
              padding: "0.375rem 0.75rem",
              fontSize: "0.8125rem", fontWeight: 500,
              color: isExpanded ? "var(--color-text-primary)" : "var(--color-text-secondary)",
              display: "inline-flex", alignItems: "center", gap: "0.375rem",
              transition: "all 150ms",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--color-surface-tertiary)";
              e.currentTarget.style.borderColor = "var(--color-border-strong)";
              e.currentTarget.style.color = "var(--color-text-primary)";
            }}
            onMouseLeave={(e) => {
              if (!isExpanded) {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.borderColor = "var(--color-border-default)";
                e.currentTarget.style.color = "var(--color-text-secondary)";
              }
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.5 }}>
              <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>
            </svg>
            {isExpanded ? "Hide Keys" : "Manage Keys"}
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform 180ms", opacity: 0.4 }}>
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </button>
        </div>
      </div>
    );

    // After each row-pair, inject expanded panel if either card is expanded
    const isEndOfRow = (i % 2 === 1) || (i === items.length - 1);
    if (isEndOfRow) {
      const leftIdx = i % 2 === 1 ? i - 1 : i;
      const rightIdx = i % 2 === 1 ? i : -1;
      const leftName = items[leftIdx]?.provider_name;
      const rightName = rightIdx >= 0 ? items[rightIdx]?.provider_name : null;
      const expandedName = expandedProvider === leftName ? leftName
        : expandedProvider === rightName ? rightName : null;
      if (expandedName) {
        gridItems.push(
          <KeysPanel
            key={`keys-${expandedName}`}
            providerName={expandedName}
            onClose={() => {
              setExpandedProvider(null);
              api.get(`/admin/providers/${expandedName}/key/status`).then((s) =>
                setKeyStatuses((prev) => ({ ...prev, [expandedName]: s }))
              ).catch(() => {});
            }}
          />
        );
      }
    }
  }

  return (
    <>
      <PageHeader title="Providers" subtitle="LLM provider adapters and API key management" />

      {loading ? <SkeletonCards count={4} /> : error ? (
        <div className="sran-card"><ErrorState message={error} onRetry={refetch} /></div>
      ) : !items.length ? (
        <div className="sran-card"><EmptyState message="No providers registered" icon="⬡" /></div>
      ) : (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 480px), 1fr))",
          gap: "1rem",
        }}>
          {gridItems}
        </div>
      )}
    </>
  );
}
