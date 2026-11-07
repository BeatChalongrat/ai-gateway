"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import PageHeader from "../../components/ui/PageHeader";
import Badge from "../../components/ui/Badge";
import ConfirmModal from "../../components/ui/ConfirmModal";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { Icon } from "../../components/ui/Icon";
import { SkeletonCards } from "../../components/ui/Skeleton";
import { useApi } from "../../lib/useApi";
import { api, getApiErrorMessage } from "../../lib/api";
import { useToast } from "../../components/ui/Toast";
import { useT, type DictKey } from "../../lib/i18n";

type Translate = (key: DictKey, vars?: Record<string, string | number>) => string;

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
  // FIX85 — the box this key belongs to. `base_url` is the key's own
  // endpoint (null = inherits the provider default), `effective_base_url`
  // is where its requests go today, `endpoint_source` says which.
  base_url?: string | null;
  effective_base_url?: string | null;
  endpoint_source?: "key" | "provider" | "none";
  // The endpoint the stored verdict was reached against.
  last_tested_base_url?: string | null;
  // Models this key's endpoint reported at the last fetch.
  models_served?: number;
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
// since it's purely presentational. Provider names and model ids stay in
// English; only `noteKey` (a human-facing blurb) is localized.
const BRAND: Record<string, { color: string; initial: string; display: string; noteKey?: DictKey }> = {
  openai:     { color: "#10a37f", initial: "O", display: "OpenAI" },
  anthropic:  { color: "#d97757", initial: "A", display: "Anthropic" },
  openrouter: { color: "#1a1f36", initial: "OR", display: "OpenRouter", noteKey: "infra.providers.note.openrouter" },
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
  replicate:  { color: "#000000", initial: "R", display: "Replicate", noteKey: "infra.providers.note.replicate" },
  elevenlabs: { color: "#000000", initial: "E", display: "ElevenLabs", noteKey: "infra.providers.note.elevenlabs" },
  sranai:     { color: "#1F48A9", initial: "S", display: "Sran AI", noteKey: "infra.providers.note.sranai" },
};

// Providers whose endpoint is operator-supplied (self-hosted box): the
// keys panel shows an extra Endpoint URL field and sends `base_url` with
// the add-key payload. Mirrors backend base_urls.SELF_HOSTED_PROVIDERS.
const SELF_HOSTED = new Set(["sranai"]);

function brandFor(name: string) {
  return BRAND[name] ?? { color: "#64748b", initial: name.charAt(0).toUpperCase(), display: name, noteKey: undefined };
}

// ── Model discovery outcome ──────────────────────────────────────
// Adding a key also asks the provider for its model list. Those are two
// SEPARATE checks and they can disagree: a key can be accepted while the
// catalog fetch fails. Until now only the key verdict reached the screen,
// so a provider could sit here green with no models anywhere in the
// product and nothing on the page said why. `model_sync` was already in
// the add-key response — it just had no reader.
type SyncOutcome =
  | { kind: "ok"; found: number }        // the provider listed models
  | { kind: "empty" }                    // it answered, with nothing in it
  | { kind: "failed"; reason: string }   // it could not be read; reason is upstream's
  | { kind: "unchecked" };               // discovery did not run at all

function readSyncOutcome(providerName: string, sync: any): SyncOutcome {
  // null = the route skipped discovery or it blew up before producing a
  // result. Saying "no models" there would be a guess.
  if (!sync || typeof sync !== "object") return { kind: "unchecked" };
  const found = typeof sync.models_found === "number" ? sync.models_found : 0;
  if (found > 0) return { kind: "ok", found };
  const reason = sync.provider_errors?.[providerName];
  return typeof reason === "string" && reason
    ? { kind: "failed", reason }
    : { kind: "empty" };
}

function SyncOutcomeNotice({
  outcome,
  keyIsValid,
  t,
}: {
  outcome: SyncOutcome;
  keyIsValid: boolean;
  t: Translate;
}) {
  const ok = outcome.kind === "ok";
  const clause =
    outcome.kind === "ok"
      ? t("infra.providers.syncFound", { n: outcome.found })
      : outcome.kind === "failed"
      ? t("infra.providers.syncFailed", { reason: outcome.reason })
      : outcome.kind === "empty"
      ? t("infra.providers.syncEmpty")
      : t("infra.providers.syncUnchecked");
  // The key prefix is claimed only when a key really did validate — the
  // one thing this notice must never get wrong is implying a working key
  // is broken, or a broken one is fine.
  const text = keyIsValid ? `${t("infra.providers.syncKeyOk")} · ${clause}` : clause;
  // Warning, never error: a catalog that can't be read is not a bad key.
  const tone = ok ? "var(--color-status-success)" : "var(--color-status-warning)";
  return (
    <div
      style={{
        padding: "0.625rem 0.75rem",
        marginBottom: "0.75rem",
        color: "var(--color-text-secondary)",
        fontSize: "var(--font-size-sm)",
        background: `color-mix(in srgb, ${tone} 8%, transparent)`,
        border: `1px solid color-mix(in srgb, ${tone} 25%, transparent)`,
        borderRadius: "var(--radius-md)",
      }}
    >
      <span style={{ color: tone, fontWeight: 600 }}>{text}</span>
      {!ok && (
        <div style={{ marginTop: "0.25rem", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
          {t("infra.providers.syncHint")}
        </div>
      )}
    </div>
  );
}

function timeAgo(iso: string | null, t: Translate): string {
  if (!iso) return t("infra.never");
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return t("time.justNow");
  if (diff < 3600000) return t("time.minutesAgo", { n: Math.floor(diff / 60000) });
  if (diff < 86400000) return t("time.hoursAgo", { n: Math.floor(diff / 3600000) });
  return t("time.daysAgo", { n: Math.floor(diff / 86400000) });
}

function StatusBadge({ status, testedAt }: { status: string; testedAt?: string | null }) {
  const t = useT();
  // FIX85 Part 6 — "invalid" alone made the owner conclude the agent had
  // broken something; the verdict was real, but about the OTHER box. The
  // badge now carries which endpoint said yes or no.
  if (status === "valid")
    return (
      <Badge variant="success" title={testedAt ? t("infra.providers.acceptedBy", { url: testedAt }) : undefined}>
        {t("infra.providers.statusValid")}
      </Badge>
    );
  if (status === "invalid")
    return (
      <Badge variant="error" title={testedAt ? t("infra.providers.rejectedBy", { url: testedAt }) : undefined}>
        {t("infra.providers.statusInvalid")}
      </Badge>
    );
  // FIX57 — "stale" is not "invalid". The endpoint moved after this key
  // was last tested, so the stored verdict is about a different box and
  // nobody knows whether the key still works. Warning, not error: the row
  // needs a Test press, not a replacement. Dispatch will not draw from it
  // until then, which is why it cannot quietly read as neutral either.
  if (status === "stale")
    return (
      <Badge variant="warning" title={t("infra.providers.statusStaleHint")}>
        {t("infra.providers.statusStale")}
      </Badge>
    );
  return <Badge variant="neutral">{t("infra.providers.statusUntested")}</Badge>;
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

function KeysPanel({
  providerName,
  currentEndpoint,
  onClose,
  onEndpointChanged,
}: {
  providerName: string;
  currentEndpoint?: string;
  onClose: () => void;
  onEndpointChanged?: () => void;
}) {
  const [keys, setKeys] = useState<ProviderKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [keysError, setKeysError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [keyInput, setKeyInput] = useState("");
  // FIX85 — two endpoint inputs with two meanings. `endpoint` is the
  // PROVIDER DEFAULT (the fallback for keys without one of their own);
  // `newKeyEndpoint` is the box the key being added talks to. Before
  // this, the single field silently rewrote the provider default on
  // every add — which is how adding a key for box B retired box A's.
  const [endpoint, setEndpoint] = useState(currentEndpoint ?? "");
  const [endpointTouched, setEndpointTouched] = useState(false);
  const [savingDefault, setSavingDefault] = useState(false);
  const [newKeyEndpoint, setNewKeyEndpoint] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [adding, setAdding] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  // Held together: the model-list verdict and whether a key had actually
  // validated when it was produced. Reading the key half off live state
  // instead would let the notice flicker its claim while the key list
  // reloads.
  const [syncNotice, setSyncNotice] = useState<{ outcome: SyncOutcome; keyIsValid: boolean } | null>(null);
  const [syncing, setSyncing] = useState(false);
  const { toast } = useToast();
  const t = useT();
  const selfHosted = SELF_HOSTED.has(providerName);

  // key/status resolves asynchronously — prefill the endpoint once it
  // arrives, unless the admin already started typing.
  useEffect(() => {
    if (!endpointTouched && currentEndpoint) setEndpoint(currentEndpoint);
  }, [currentEndpoint, endpointTouched]);

  const urlOk = (v: string) => /^https?:\/\/\S+$/.test(v.trim());
  const endpointOk = urlOk(endpoint);
  // A new key needs SOME endpoint: its own, or the provider default.
  const hasDefault = !!currentEndpoint;
  const newKeyEndpointOk = newKeyEndpoint.trim() ? urlOk(newKeyEndpoint) : hasDefault;

  const loadKeys = useCallback(async () => {
    setKeysError(null);
    try {
      const data = await api.get(`/admin/providers/${providerName}/keys`);
      setKeys(data.items || []);
    } catch (e: any) {
      setKeysError(e?.body?.error?.message || t("infra.providers.keysLoadError"));
    }
    setLoading(false);
  }, [providerName, t]);

  useEffect(() => {
    loadKeys();
  }, [loadKeys]);

  async function handleAdd() {
    if (!keyInput.trim()) return;
    if (selfHosted && !newKeyEndpointOk) return;
    setAdding(true);
    try {
      const result = await api.post(`/admin/providers/${providerName}/keys`, {
        api_key: keyInput.trim(),
        label: label.trim() || undefined,
        // Only an endpoint the admin typed travels with the key; empty
        // means "inherit the default" and is stored as NULL.
        base_url: selfHosted && newKeyEndpoint.trim() ? newKeyEndpoint.trim() : undefined,
      });
      toast(t("infra.providers.toastKeySaved", { ms: result.latency_ms }));
      // The key verdict is a toast; the model-list verdict is a notice
      // that stays put — it is the half the operator has to act on, and
      // a toast that vanishes is how this went unnoticed in the first
      // place.
      // keyIsValid is not a guess: the route returns 200 only after a
      // live call to the provider accepted this key.
      setSyncNotice({
        outcome: readSyncOutcome(providerName, result.model_sync),
        keyIsValid: true,
      });
      setKeyInput("");
      setLabel("");
      setNewKeyEndpoint("");
      loadKeys();
      onEndpointChanged?.();
    } catch (e: any) {
      const msg = getApiErrorMessage(e, t("infra.providers.toastAddFailed"), t);
      toast(msg, "error");
    } finally {
      setAdding(false);
    }
  }

  // FIX85 — the provider DEFAULT endpoint, saved on its own.
  async function handleSaveDefault() {
    if (!endpointOk) return;
    setSavingDefault(true);
    try {
      await api.put(`/admin/providers/${providerName}/base_url`, { base_url: endpoint.trim() });
      toast(t("infra.providers.toastDefaultSaved"));
      setEndpointTouched(false);
      loadKeys();
      onEndpointChanged?.();
    } catch (e: any) {
      toast(getApiErrorMessage(e, t("infra.providers.toastEndpointFailed"), t), "error");
    } finally {
      setSavingDefault(false);
    }
  }

  // FIX85 — ONE key's endpoint. Empty = back on the default.
  function startEdit(k: ProviderKey) {
    setEditingId(k.id);
    setEditValue(k.base_url || "");
  }

  async function handleSaveEdit(keyId: string, value: string | null) {
    if (value && !urlOk(value)) return;
    setSavingEdit(true);
    try {
      const result = await api.patch(`/admin/providers/${providerName}/keys/${keyId}`, {
        base_url: value ? value.trim() : null,
      });
      toast(
        result?.changed
          ? result?.staled
            ? t("infra.providers.toastEndpointSaved")
            : t("infra.providers.toastEndpointMoved")
          : t("infra.providers.toastEndpointUnchanged"),
      );
      setEditingId(null);
      loadKeys();
      onEndpointChanged?.();
    } catch (e: any) {
      toast(getApiErrorMessage(e, t("infra.providers.toastEndpointFailed"), t), "error");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleFetchModels() {
    setSyncing(true);
    try {
      // Same discovery the add-key route runs, on demand — so a failure
      // is recoverable without deleting and re-pasting a working key.
      const result = await api.post("/admin/models/discovery/sync", {
        provider: providerName,
      });
      setSyncNotice({
        outcome: readSyncOutcome(providerName, result),
        keyIsValid: validCount > 0,
      });
    } catch {
      setSyncNotice({
        outcome: { kind: "failed", reason: t("infra.providers.syncUnreachable") },
        keyIsValid: validCount > 0,
      });
    } finally {
      setSyncing(false);
    }
  }

  async function handleTest(keyId: string) {
    setTestingId(keyId);
    try {
      const result = await api.post(`/admin/providers/${providerName}/keys/${keyId}/test`, {});
      if (result.valid) toast(t("infra.providers.toastKeyValid", { ms: result.latency_ms }));
      else toast(t("infra.providers.toastKeyInvalid", { error: result.error }), "error");
      loadKeys();
      // The card's stale/invalid counts read from key/status — refresh
      // them so a verdict shows on the card without closing the panel.
      onEndpointChanged?.();
    } catch {
      toast(t("infra.testFailed"), "error");
    } finally {
      setTestingId(null);
    }
  }

  // FIX84 Part D sibling — deactivating a provider key stops traffic on it
  // and cannot be undone (the row stays inactive; a new key must be added).
  // It used to fire on the first click; it now asks, naming the key.
  const [deleteTarget, setDeleteTarget] = useState<ProviderKey | null>(null);

  async function handleDelete(keyId: string) {
    setDeleteTarget(null);
    try {
      await api.delete(`/admin/providers/${providerName}/keys/${keyId}`);
      toast(t("infra.providers.toastDeactivated"));
      loadKeys();
    } catch {
      toast(t("infra.providers.toastDeleteFailed"), "error");
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
          {t("infra.providers.keysTitle", { name: b.display })}
          {activeKeys.length > 0 && (
            <span style={{ marginLeft: "0.5rem", fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", fontWeight: 400 }}>
              {t("infra.providers.keysSummary", { valid: validCount, total: activeKeys.length })}
            </span>
          )}
        </h4>
        <div style={{ display: "flex", gap: "0.375rem" }}>
          <button
            className="sran-btn sran-btn-secondary sran-btn-sm"
            onClick={handleFetchModels}
            disabled={syncing}
          >
            {syncing ? t("infra.providers.syncRunning") : t("infra.providers.syncRefetch")}
          </button>
          <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={onClose}>
            {t("action.close")}
          </button>
        </div>
      </div>

      {syncNotice && (
        <SyncOutcomeNotice
          outcome={syncNotice.outcome}
          keyIsValid={syncNotice.keyIsValid}
          t={t}
        />
      )}

      {loading ? (
        <div className="sran-empty" style={{ padding: "1rem" }}>
          {t("infra.providers.loadingKeys")}
        </div>
      ) : keysError ? (
        <div className="sran-empty" style={{ padding: "1rem", display: "flex", flexDirection: "column", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ color: "var(--color-status-error)", fontWeight: 500 }}>{keysError}</span>
          <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => { setLoading(true); loadKeys(); }}>{t("action.retry")}</button>
        </div>
      ) : activeKeys.length > 0 ? (
        <div style={{ overflowX: "auto", marginBottom: "1.25rem" }}>
          <table className="sran-table">
            <thead>
              <tr>
                <th>{t("infra.providers.colLabel")}</th>
                <th>{t("infra.providers.colKey")}</th>
                {selfHosted && <th>{t("infra.providers.colEndpoint")}</th>}
                <th>{t("field.status")}</th>
                <th>{t("infra.providers.colLatency")}</th>
                <th>{t("infra.providers.colLastTested")}</th>
                <th style={{ width: "140px" }}>{t("field.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {activeKeys.map((k) => (
                <tr key={k.id} data-testid={`key-row-${k.label}`}>
                  <td style={{ fontWeight: 500 }}>{k.label || "—"}</td>
                  <td><code style={{ fontSize: "var(--font-size-xs)" }}>{k.key_masked}</code></td>
                  {selfHosted && (
                    <td data-testid="key-endpoint">
                      {editingId === k.id ? (
                        <div style={{ display: "flex", gap: "0.375rem", alignItems: "center", flexWrap: "wrap" }}>
                          <input
                            className="sran-input"
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            placeholder={currentEndpoint || "http://host.docker.internal:8000/v1"}
                            aria-label={t("infra.providers.editEndpoint")}
                            style={{ fontSize: "var(--font-size-xs)", minWidth: 220 }}
                          />
                          <button
                            className="sran-btn sran-btn-primary sran-btn-sm"
                            disabled={savingEdit || !editValue.trim() || !urlOk(editValue)}
                            onClick={() => handleSaveEdit(k.id, editValue)}
                          >
                            {t("action.save")}
                          </button>
                          {hasDefault && (
                            <button
                              className="sran-btn sran-btn-secondary sran-btn-sm"
                              disabled={savingEdit}
                              onClick={() => handleSaveEdit(k.id, null)}
                              title={t("infra.providers.useDefaultEndpointHint")}
                            >
                              {t("infra.providers.useDefaultEndpoint")}
                            </button>
                          )}
                          <button className="sran-btn sran-btn-secondary sran-btn-sm" disabled={savingEdit} onClick={() => setEditingId(null)}>
                            {t("action.cancel")}
                          </button>
                        </div>
                      ) : (
                        <div style={{ display: "flex", gap: "0.375rem", alignItems: "center", flexWrap: "wrap" }}>
                          <code style={{ fontSize: "var(--font-size-xs)" }}>
                            {k.effective_base_url || t("infra.providers.endpointNotSet")}
                          </code>
                          {k.endpoint_source === "key" ? (
                            <Badge variant="info" title={t("infra.providers.endpointOwnHint")}>
                              {t("infra.providers.endpointOwn")}
                            </Badge>
                          ) : k.endpoint_source === "provider" ? (
                            <Badge variant="neutral" title={t("infra.providers.endpointInheritedHint")}>
                              {t("infra.providers.endpointInherited")}
                            </Badge>
                          ) : (
                            <Badge variant="warning">{t("infra.providers.endpointNotSet")}</Badge>
                          )}
                          {typeof k.models_served === "number" && k.models_served > 0 && (
                            <span
                              style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}
                              title={t("infra.providers.modelsServedHint")}
                            >
                              {t("infra.providers.modelsServed", { n: k.models_served })}
                            </span>
                          )}
                          <button
                            className="sran-btn sran-btn-secondary sran-btn-sm"
                            onClick={() => startEdit(k)}
                            title={t("infra.providers.editEndpoint")}
                            aria-label={`${t("infra.providers.editEndpoint")} ${k.label}`}
                          >
                            {t("action.edit")}
                          </button>
                        </div>
                      )}
                    </td>
                  )}
                  <td>
                    <StatusBadge status={k.last_test_status} testedAt={k.last_tested_base_url} />
                    {k.last_test_status === "invalid" && k.last_tested_base_url && (
                      <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.125rem" }}>
                        {t("infra.providers.rejectedBy", { url: k.last_tested_base_url })}
                      </div>
                    )}
                  </td>
                  <td>{k.last_test_latency_ms ? `${k.last_test_latency_ms}ms` : "—"}</td>
                  <td style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>{timeAgo(k.last_tested_at, t)}</td>
                  <td>
                    <div style={{ display: "flex", gap: "0.375rem" }}>
                      <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={() => handleTest(k.id)} disabled={testingId === k.id}>
                        {testingId === k.id ? "…" : t("action.test")}
                      </button>
                      <button className="sran-btn sran-btn-danger sran-btn-sm" onClick={() => setDeleteTarget(k)}>
                        {t("action.delete")}
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
          {t("infra.providers.noKeysConfigured")}
        </div>
      )}

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && void handleDelete(deleteTarget.id)}
        title={t("infra.providers.deleteKeyTitle")}
        message={t("infra.providers.deleteKeyBody", {
          label: deleteTarget?.label || "—",
          masked: deleteTarget?.key_masked || "",
        })}
        confirmLabel={t("infra.providers.deleteKeyConfirm")}
      />

      {selfHosted && (
        <div
          style={{
            marginBottom: "0.75rem",
            padding: "0.625rem 0.75rem",
            border: "1px dashed var(--color-border-default)",
            borderRadius: "var(--radius-md)",
          }}
          data-testid="default-endpoint"
        >
          <label className="sran-label">{t("infra.providers.defaultEndpointLabel")}</label>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input
              className="sran-input"
              value={endpoint}
              onChange={(e) => {
                setEndpointTouched(true);
                setEndpoint(e.target.value);
              }}
              placeholder="http://host.docker.internal:8000/v1"
              aria-label={t("infra.providers.defaultEndpointLabel")}
              style={{ fontSize: "var(--font-size-sm)", flex: 1 }}
            />
            <button
              className="sran-btn sran-btn-secondary sran-btn-sm"
              onClick={handleSaveDefault}
              disabled={savingDefault || !endpointOk || (!endpointTouched && endpoint.trim() === (currentEndpoint ?? ""))}
            >
              {savingDefault ? "…" : t("action.save")}
            </button>
          </div>
          <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>
            {hasDefault ? t("infra.providers.defaultEndpointHint") : t("infra.providers.defaultEndpointNone")}
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", flexWrap: "wrap" }}>
        <div style={{ flex: "0 0 140px" }}>
          <label className="sran-label">{t("infra.providers.colLabel")}</label>
          <input
            className="sran-input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={t("infra.providers.labelPlaceholder")}
            style={{ fontSize: "var(--font-size-sm)" }}
          />
        </div>
        <div style={{ flex: 1, minWidth: "200px" }}>
          <label className="sran-label">{t("infra.providers.apiKeyLabel")}</label>
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
                : providerName === "replicate"
                ? "r8_..."
                : providerName === "elevenlabs"
                ? "xi-..."
                : "..."
            }
            style={{ fontSize: "var(--font-size-sm)" }}
          />
        </div>
        {selfHosted && (
          <div style={{ flex: 1, minWidth: "220px" }}>
            <label className="sran-label">
              {hasDefault ? t("infra.providers.newKeyEndpointLabel") : t("infra.providers.endpointLabel")}
            </label>
            <input
              className="sran-input"
              value={newKeyEndpoint}
              onChange={(e) => setNewKeyEndpoint(e.target.value)}
              placeholder={currentEndpoint ? currentEndpoint : "http://host.docker.internal:8000/v1"}
              aria-label={t("infra.providers.newKeyEndpointLabel")}
              style={{ fontSize: "var(--font-size-sm)" }}
            />
            <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>
              {hasDefault
                ? t("infra.providers.newKeyEndpointOptional")
                : t("infra.providers.newKeyEndpointRequired")}
            </div>
          </div>
        )}
        <button
          className="sran-btn sran-btn-primary"
          onClick={handleAdd}
          disabled={adding || !keyInput.trim() || (selfHosted && !newKeyEndpointOk)}
          style={{ whiteSpace: "nowrap" }}
        >
          {adding ? t("infra.providers.validating") : t("infra.providers.addValidate")}
        </button>
      </div>
      {selfHosted && (
        <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", marginTop: "0.5rem" }}>
          {t("infra.providers.endpointHint")}
        </div>
      )}
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
  const t = useT();
  const b = brandFor(p.provider_name);
  const totalKeys = keyStatus?.total_keys ?? p.key_count ?? 0;
  const validKeys = keyStatus?.valid_keys ?? 0;
  // Active rows dispatch will NOT use (invalid or placeholder).
  const unusableKeys = keyStatus?.unusable_keys ?? 0;
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
      // Keyboard-focus parity: mirror the hover lift so tabbing to the card
      // gets the same affordance as a mouse hover.
      onFocus={(e) => {
        e.currentTarget.style.borderColor = "var(--color-border-strong)";
        e.currentTarget.style.boxShadow = "0 4px 10px -2px rgba(15, 31, 56, 0.08)";
        e.currentTarget.style.transform = "translateY(-1px)";
      }}
      onBlur={(e) => {
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
              title={p.enabled ? t("field.active") : t("field.disabled")}
            />
          </div>
          {b.noteKey && (
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
              {t(b.noteKey)}
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
            {t("infra.providers.new")}
          </span>
        )}
      </div>

      {/* Row 2: key status badge + model count */}
      <div style={{ display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" }}>
        {keyStatus === undefined && totalKeys === 0 ? (
          // Key/status resolves asynchronously after the card mounts — show a
          // badge-sized skeleton instead of flashing a premature "no key".
          <span
            className="sran-skeleton"
            aria-hidden
            style={{ width: 64, height: 18, borderRadius: 999 }}
          />
        ) : totalKeys > 0 ? (
          <Badge variant={validKeys > 0 ? "success" : "warning"}>
            {t("infra.providers.keyCount", { valid: validKeys, total: totalKeys })}
          </Badge>
        ) : keyStatus?.configured ? (
          <Badge variant="success">{t("infra.providers.keySet")}</Badge>
        ) : (
          <Badge variant="neutral">{t("infra.providers.noKey")}</Badge>
        )}
        {/* A provider can show "no key" while visibly holding key rows —
            every row is invalid or a placeholder, so dispatch has nothing.
            Say so on the card, or the badge reads as a bug. */}
        {/* FIX85 Part 6 — WHICH case: "endpoint moved, press Test" and
            "the box said no" had one badge and opposite fixes. */}
        {(keyStatus?.stale_keys ?? 0) > 0 && (
          <Badge variant="warning" title={t("infra.providers.statusStaleHint")}>
            {t("infra.providers.staleCount", { n: keyStatus.stale_keys })}
          </Badge>
        )}
        {(keyStatus?.invalid_keys ?? 0) > 0 && (
          <Badge variant="error" title={t("infra.providers.invalidCountHint")}>
            {t("infra.providers.invalidCount", { n: keyStatus.invalid_keys })}
          </Badge>
        )}
        {!keyStatus?.configured &&
          unusableKeys > 0 &&
          (keyStatus?.stale_keys ?? 0) + (keyStatus?.invalid_keys ?? 0) < unusableKeys && (
          <Badge
            variant="warning"
            title={t("infra.providers.unusableNote", { n: unusableKeys })}
          >
            {t("infra.providers.unusableBadge", { n: unusableKeys })}
          </Badge>
        )}
        {SELF_HOSTED.has(p.provider_name) &&
          keyStatus !== undefined &&
          !(keyStatus?.endpoint_configured ?? !!keyStatus?.base_url) && (
          <Badge variant="warning">{t("infra.providers.endpointNotSet")}</Badge>
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
          {p.models.length === 1
            ? t("infra.providers.modelCountOne")
            : t("infra.providers.modelCount", { n: p.models.length })}
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
  const t = useT();
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
      <PageHeader title={t("infra.providers.title")} subtitle={t("infra.providers.subtitle")} />

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
          placeholder={t("infra.providers.searchPlaceholder")}
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
              ["all", "field.all", counts.all],
              ["configured", "infra.providers.tabConfigured", counts.configured],
              ["available", "infra.providers.tabAvailable", counts.available],
            ] as [FilterTab, DictKey, number][]
          ).map(([key, labelKey, count]) => {
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
                {t(labelKey)}
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
          <EmptyState message={t("infra.providers.empty")} icon={<Icon name="server" size={22} />} />
        </div>
      ) : filtered.length === 0 ? (
        <div className="sran-card">
          <EmptyState message={t("infra.providers.noMatch", { query })} icon={<Icon name="search" size={22} />} />
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
                    currentEndpoint={keyStatuses[p.provider_name]?.base_url || undefined}
                    onClose={() => handleClosePanel(p.provider_name)}
                    onEndpointChanged={() =>
                      api
                        .get(`/admin/providers/${p.provider_name}/key/status`)
                        .then((s) => setKeyStatuses((prev) => ({ ...prev, [p.provider_name]: s })))
                        .catch(() => {})
                    }
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
