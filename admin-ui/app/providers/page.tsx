"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
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

interface ProviderItem {
  provider_name: string;
  enabled: boolean;
  models: string[];
  key_count: number;
  has_valid_key: boolean;
}

// Brand color + display initial per provider. Used for the logo fallback
// tile when no PNG is present. Keeping the map co-located with the page
// since it's purely presentational.
const BRAND: Record<string, { color: string; initial: string; display: string; note?: string }> = {
  openai:     { color: "#10a37f", initial: "O", display: "OpenAI" },
  anthropic:  { color: "#d97757", initial: "A", display: "Anthropic" },
  openrouter: { color: "#1a1f36", initial: "◆", display: "OpenRouter", note: "Unified billing across 300+ models" },
  google:     { color: "#4285f4", initial: "G", display: "Google AI" },
  groq:       { color: "#f55036", initial: "Q", display: "Groq" },
  deepseek:   { color: "#4d6bfe", initial: "D", display: "DeepSeek" },
  xai:        { color: "#000000", initial: "X", display: "xAI" },
  cohere:     { color: "#ff7759", initial: "C", display: "Cohere" },
  perplexity: { color: "#20b8cd", initial: "P", display: "Perplexity" },
  mistral:    { color: "#fa520f", initial: "M", display: "Mistral" },
  minimax:    { color: "#00a971", initial: "M", display: "Minimax" },
  qwen:       { color: "#615ced", initial: "Q", display: "Qwen" },
  kimi:       { color: "#1877f2", initial: "K", display: "Kimi" },
  zai:        { color: "#8b5cf6", initial: "Z", display: "Z.AI" },
};

function brandFor(name: string) {
  return BRAND[name] ?? { color: "#64748b", initial: name.charAt(0).toUpperCase(), display: name };
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

function ProviderLogo({ name, size = 28 }: { name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const b = brandFor(name);
  const src = `/providers/${name.toLowerCase()}.png`;

  const base: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: size >= 36 ? "8px" : "6px",
    flexShrink: 0,
  };

  if (failed) {
    return (
      <div
        style={{
          ...base,
          background: `linear-gradient(135deg, ${b.color} 0%, ${b.color}cc 100%)`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: size >= 36 ? "0.875rem" : "0.6875rem",
          fontWeight: 700,
          color: "#ffffff",
          textTransform: "uppercase",
          letterSpacing: "-0.02em",
          boxShadow: `0 1px 3px ${b.color}40`,
        }}
        aria-label={b.display}
      >
        {b.initial}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={b.display}
      onError={() => setFailed(true)}
      style={{
        ...base,
        objectFit: "contain",
        background: "var(--color-surface-tertiary)",
        border: "1px solid var(--color-border-default)",
      }}
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
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, [providerName]);

  useEffect(() => {
    loadKeys();
  }, [loadKeys]);

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
    } catch {
      toast("Test failed", "error");
    } finally {
      setTestingId(null);
    }
  }

  async function handleDelete(keyId: string) {
    try {
      await api.delete(`/admin/providers/${providerName}/keys/${keyId}`);
      toast("Key deactivated");
      loadKeys();
    } catch {
      toast("Failed to delete", "error");
    }
  }

  const activeKeys = keys.filter((k) => k.is_active);
  const validCount = activeKeys.filter((k) => k.last_test_status === "valid").length;
  const b = brandFor(providerName);

  return (
    <div
      style={{
        background: "var(--color-surface-primary)",
        border: "1px solid var(--color-border-default)",
        borderRadius: "var(--radius-lg)",
        boxShadow: "0 1px 3px rgba(15, 31, 56, 0.05), 0 1px 2px rgba(15, 31, 56, 0.03)",
        padding: "1.25rem 1.5rem",
        gridColumn: "1 / -1",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h4 style={{ margin: 0, fontSize: "var(--font-size-base)", fontWeight: 600 }}>
          API Keys for <span>{b.display}</span>
          {activeKeys.length > 0 && (
            <span style={{ marginLeft: "0.5rem", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", fontWeight: 400 }}>
              {validCount} valid / {activeKeys.length} total
            </span>
          )}
        </h4>
        <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={onClose}>
          Close
        </button>
      </div>

      {loading ? (
        <div className="sran-empty" style={{ padding: "1rem" }}>
          Loading keys...
        </div>
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
                      <button className="sran-btn sran-btn-danger sran-btn-sm" onClick={() => handleDelete(k.id)}>
                        Delete
                      </button>
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
          <input
            className="sran-input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Primary"
            style={{ fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <div style={{ flex: 1, minWidth: "200px" }}>
          <label className="sran-label">API Key *</label>
          <input
            className="sran-input"
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder={
              providerName === "openai"
                ? "sk-..."
                : providerName === "anthropic"
                ? "sk-ant-..."
                : providerName === "google"
                ? "AIza..."
                : providerName === "openrouter"
                ? "sk-or-..."
                : "..."
            }
            style={{ fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <button
          className="sran-btn sran-btn-primary"
          onClick={handleAdd}
          disabled={adding || !keyInput.trim()}
          style={{ whiteSpace: "nowrap" }}
        >
          {adding ? "Validating..." : "Add & Validate"}
        </button>
      </div>
    </div>
  );
}

/* ── Compact provider card ───────────────────────────────────────── */

function ProviderCard({
  p,
  keyStatus,
  isExpanded,
  onToggle,
}: {
  p: ProviderItem;
  keyStatus: any;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const b = brandFor(p.provider_name);
  const totalKeys = keyStatus?.total_keys ?? p.key_count ?? 0;
  const validKeys = keyStatus?.valid_keys ?? 0;
  const isNew = totalKeys === 0 && (keyStatus === undefined || !keyStatus?.configured);

  return (
    <button
      onClick={onToggle}
      style={{
        textAlign: "left",
        background: "var(--color-surface-primary)",
        border: `1px solid ${isExpanded ? "var(--color-border-strong)" : "var(--color-border-default)"}`,
        borderRadius: "var(--radius-lg)",
        boxShadow: "0 1px 2px rgba(15, 31, 56, 0.04)",
        padding: "0.875rem 1rem 1rem",
        display: "flex",
        flexDirection: "column",
        gap: "0.625rem",
        cursor: "pointer",
        transition: "border-color 150ms, box-shadow 150ms, transform 150ms",
        width: "100%",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--color-border-strong)";
        e.currentTarget.style.boxShadow = "0 4px 10px -2px rgba(15, 31, 56, 0.08)";
        e.currentTarget.style.transform = "translateY(-1px)";
      }}
      onMouseLeave={(e) => {
        if (!isExpanded) e.currentTarget.style.borderColor = "var(--color-border-default)";
        e.currentTarget.style.boxShadow = "0 1px 2px rgba(15, 31, 56, 0.04)";
        e.currentTarget.style.transform = "translateY(0)";
      }}
    >
      {/* Row 1: logo + name + status dot + NEW badge */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
        <ProviderLogo name={p.provider_name} size={28} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
            <span
              style={{
                fontSize: "0.875rem",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                letterSpacing: "-0.01em",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {b.display}
            </span>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                flexShrink: 0,
                background: p.enabled ? "#10b981" : "#cbd5e1",
              }}
              title={p.enabled ? "Active" : "Disabled"}
            />
          </div>
          {b.note && (
            <div
              style={{
                fontSize: "0.6875rem",
                color: "var(--color-text-muted)",
                marginTop: "0.125rem",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {b.note}
            </div>
          )}
        </div>
        {isNew && (
          <span
            style={{
              fontSize: "0.5625rem",
              fontWeight: 700,
              color: "#7c3aed",
              background: "rgba(124,58,237,0.1)",
              border: "1px solid rgba(124,58,237,0.2)",
              borderRadius: "999px",
              padding: "0.0625rem 0.5rem",
              letterSpacing: "0.05em",
              lineHeight: 1.4,
            }}
          >
            NEW
          </span>
        )}
      </div>

      {/* Row 2: key status badge + model count */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" }}>
        {totalKeys > 0 ? (
          <Badge variant={validKeys > 0 ? "success" : "warning"}>
            {validKeys}/{totalKeys} keys
          </Badge>
        ) : keyStatus?.source === "environment" ? (
          <Badge variant="warning">via env</Badge>
        ) : keyStatus?.configured ? (
          <Badge variant="success">key set</Badge>
        ) : (
          <Badge variant="neutral">no key</Badge>
        )}
        <span
          style={{
            fontSize: "0.625rem",
            color: "var(--color-text-muted)",
            fontWeight: 500,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {p.models.length} model{p.models.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Row 3: example model names */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem" }}>
        {p.models.slice(0, 2).map((m) => (
          <code
            key={m}
            style={{
              padding: "0.125rem 0.4375rem",
              background: "var(--color-surface-tertiary)",
              borderRadius: "var(--radius-sm)",
              fontSize: "0.625rem",
              color: "var(--color-text-muted)",
              lineHeight: 1.4,
              fontWeight: 500,
              maxWidth: "100%",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {m}
          </code>
        ))}
        {p.models.length > 2 && (
          <span
            style={{
              fontSize: "0.625rem",
              color: "var(--color-text-muted)",
              fontWeight: 500,
              padding: "0.125rem 0.25rem",
            }}
          >
            +{p.models.length - 2}
          </span>
        )}
      </div>
    </button>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

type FilterTab = "all" | "configured" | "available";

export default function ProvidersPage() {
  const { data, loading, error, refetch } = useApi<{ items: ProviderItem[] }>("/admin/providers");
  const [keyStatuses, setKeyStatuses] = useState<Record<string, any>>({});
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterTab>("all");

  useEffect(() => {
    if (!data?.items) return;
    data.items.forEach(async (p) => {
      try {
        const status = await api.get(`/admin/providers/${p.provider_name}/key/status`);
        setKeyStatuses((prev) => ({ ...prev, [p.provider_name]: status }));
      } catch {
        /* ignore */
      }
    });
  }, [data]);

  const items = data?.items ?? [];

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((p) => {
      const b = brandFor(p.provider_name);
      if (q) {
        const hay = `${p.provider_name} ${b.display} ${p.models.join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      const ks = keyStatuses[p.provider_name];
      const isConfigured = (ks?.total_keys ?? 0) > 0 || ks?.configured;
      if (filter === "configured" && !isConfigured) return false;
      if (filter === "available" && isConfigured) return false;
      return true;
    });
  }, [items, keyStatuses, query, filter]);

  const counts = useMemo(() => {
    let configured = 0;
    let available = 0;
    for (const p of items) {
      const ks = keyStatuses[p.provider_name];
      const isConfigured = (ks?.total_keys ?? 0) > 0 || ks?.configured;
      if (isConfigured) configured++;
      else available++;
    }
    return { all: items.length, configured, available };
  }, [items, keyStatuses]);

  const handleClosePanel = (name: string) => {
    setExpandedProvider(null);
    api
      .get(`/admin/providers/${name}/key/status`)
      .then((s) => setKeyStatuses((prev) => ({ ...prev, [name]: s })))
      .catch(() => {});
  };

  return (
    <>
      <PageHeader title="Providers" subtitle="LLM provider adapters and API key management" />

      {/* Search + filter bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.75rem",
          marginBottom: "1rem",
          flexWrap: "wrap",
        }}
      >
        <input
          className="sran-input"
          placeholder="Search providers..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1, minWidth: 200, maxWidth: 360, fontSize: "var(--font-size-sm)" }}
        />
        <div
          role="tablist"
          style={{
            display: "inline-flex",
            // bg-surface-2 is the subtlest of the three surfaces — reads
            // as a faint plate behind the tabs in both themes, so the
            // active tab's bg-elevated stands out (lighter in dark, very
            // slightly lighter in light).
            background: "var(--bg-surface-2)",
            border: "1px solid var(--border-default)",
            borderRadius: "var(--radius-md)",
            padding: "2px",
            gap: "2px",
          }}
        >
          {(
            [
              ["all", "All", counts.all],
              ["configured", "Configured", counts.configured],
              ["available", "Available", counts.available],
            ] as const
          ).map(([key, label, count]) => {
            const active = filter === key;
            return (
              <button
                key={key}
                role="tab"
                aria-selected={active}
                onClick={() => setFilter(key)}
                style={{
                  background: active ? "var(--bg-elevated)" : "transparent",
                  color: active ? "var(--text-primary)" : "var(--text-secondary)",
                  border: "none",
                  borderRadius: "calc(var(--radius-md) - 2px)",
                  padding: "0.375rem 0.75rem",
                  fontSize: "0.8125rem",
                  fontWeight: 500,
                  cursor: "pointer",
                  boxShadow: active ? "0 1px 2px rgba(0, 0, 0, 0.18)" : "none",
                  transition: "background 120ms, color 120ms",
                }}
              >
                {label}
                <span
                  style={{
                    marginLeft: "0.375rem",
                    fontSize: "0.6875rem",
                    color: active ? "var(--accent-primary)" : "var(--text-muted)",
                    background: active ? "rgba(99,102,241,0.15)" : "transparent",
                    padding: active ? "1px 6px" : "0",
                    borderRadius: 10,
                    fontWeight: 600,
                  }}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {loading ? (
        <SkeletonCards count={8} />
      ) : error ? (
        <div className="sran-card">
          <ErrorState message={error} onRetry={refetch} />
        </div>
      ) : !items.length ? (
        <div className="sran-card">
          <EmptyState message="No providers registered" icon="⬡" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="sran-card">
          <EmptyState message={`No providers match "${query}"`} icon="🔍" />
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 240px), 1fr))",
            gap: "0.75rem",
          }}
        >
          {filtered.map((p) => {
            const isExpanded = expandedProvider === p.provider_name;
            return (
              <div key={p.provider_name} style={{ display: "contents" }}>
                <ProviderCard
                  p={p}
                  keyStatus={keyStatuses[p.provider_name]}
                  isExpanded={isExpanded}
                  onToggle={() => setExpandedProvider(isExpanded ? null : p.provider_name)}
                />
                {isExpanded && (
                  <KeysPanel
                    providerName={p.provider_name}
                    onClose={() => handleClosePanel(p.provider_name)}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
