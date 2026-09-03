"use client";

// Data-retention admin: per-tenant transcript retention settings,
// the deletion audit, and the dry-run preview.
//
// Shape rules this page enforces in the UI (the server enforces them
// again): the retention window has a hard 30-day floor; changes take
// effect at the next scheduled run (00:00 UTC), never immediately —
// the effective time is always shown; dry-run is the first mode and
// the preview button lets the admin see the numbers before anything
// is allowed to delete.

import { useCallback, useState } from "react";

import PageHeader from "../../components/ui/PageHeader";
import ErrorState from "../../components/ui/ErrorState";
import EmptyState from "../../components/ui/EmptyState";
import { useToast } from "../../components/ui/Toast";
import { useT } from "../../lib/i18n";
import {
  DeletionAuditEntry,
  RetentionSettings,
  fetchDeletionAudit,
  fetchRetentionPreview,
  fetchRetentionSettings,
  putRetentionSettings,
} from "../../lib/chatEdgeApi";

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function ChatRetentionPage() {
  const t = useT();
  const { toast } = useToast();

  const [tenantId, setTenantId] = useState("");
  const [minDays, setMinDays] = useState(30);
  const [effectiveAt, setEffectiveAt] = useState<string | null>(null);
  const [settings, setSettings] = useState<RetentionSettings | null>(null);
  const [daysText, setDaysText] = useState("");
  const [audit, setAudit] = useState<DeletionAuditEntry[] | null>(null);
  const [preview, setPreview] = useState<Record<string, number> | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const tid = tenantId.trim();
    if (!tid) return;
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const [res, auditRes] = await Promise.all([
        fetchRetentionSettings(tid),
        fetchDeletionAudit(tid),
      ]);
      setSettings(res.settings);
      setMinDays(res.min_days);
      setEffectiveAt(res.effective_at);
      setDaysText(
        res.settings.transcript_retention_days == null
          ? ""
          : String(res.settings.transcript_retention_days),
      );
      setAudit(auditRes.entries);
    } catch (e) {
      setError(String(e));
      setSettings(null);
      setAudit(null);
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  const save = useCallback(async () => {
    if (!settings) return;
    const days = daysText.trim() === "" ? null : Number(daysText);
    if (days != null && (!Number.isFinite(days) || days < minDays)) {
      toast(t("chat.retention.floorError", { n: String(minDays) }), "error");
      return;
    }
    setSaving(true);
    try {
      const res = await putRetentionSettings({
        ...settings,
        transcript_retention_days: days,
      });
      setSettings(res.settings);
      setEffectiveAt(res.effective_at);
      toast(t("chat.retention.saved", { at: fmtWhen(res.effective_at) }), "success");
      const auditRes = await fetchDeletionAudit(settings.tenant_id);
      setAudit(auditRes.entries);
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setSaving(false);
    }
  }, [settings, daysText, minDays, t, toast]);

  const runPreview = useCallback(async () => {
    if (!settings) return;
    try {
      const res = await fetchRetentionPreview(settings.tenant_id);
      setPreview(res.counts);
    } catch (e) {
      toast(String(e), "error");
    }
  }, [settings, toast]);

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("chat.retention.title")}
        subtitle={t("chat.retention.subtitle")}
      />

      <div className="flex items-end gap-2">
        <label className="flex flex-col text-sm">
          <span className="mb-1 text-gray-500">{t("chat.retention.tenantLabel")}</span>
          <input
            className="w-96 rounded border px-2 py-1.5 font-mono text-sm"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
          />
        </label>
        <button
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          onClick={() => void load()}
          disabled={loading || !tenantId.trim()}
        >
          {t("chat.retention.load")}
        </button>
      </div>

      {error ? <ErrorState message={error} /> : null}

      {settings ? (
        <div className="space-y-4 rounded border p-4">
          {effectiveAt ? (
            <p className="text-sm text-gray-500">
              {t("chat.retention.effectiveAt", { at: fmtWhen(effectiveAt) })}
            </p>
          ) : null}

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={settings.transcript_retention_enabled}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  transcript_retention_enabled: e.target.checked,
                })
              }
            />
            {t("chat.retention.enabled")}
          </label>

          <label className="flex flex-col text-sm">
            <span className="mb-1">{t("chat.retention.days")}</span>
            <input
              type="number"
              min={minDays}
              className="w-40 rounded border px-2 py-1.5"
              value={daysText}
              onChange={(e) => setDaysText(e.target.value)}
            />
            <span className="mt-1 text-xs text-gray-500">
              {t("chat.retention.daysHint", { n: String(minDays) })}
            </span>
          </label>

          <label className="flex flex-col text-sm">
            <span className="mb-1">{t("chat.retention.purgeDays")}</span>
            <input
              type="number"
              min={1}
              className="w-40 rounded border px-2 py-1.5"
              value={settings.deleted_conversation_purge_days}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  deleted_conversation_purge_days: Number(e.target.value) || 1,
                })
              }
            />
            <span className="mt-1 text-xs text-gray-500">
              {t("chat.retention.purgeDaysHint")}
            </span>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={settings.dry_run}
              onChange={(e) =>
                setSettings({ ...settings, dry_run: e.target.checked })
              }
            />
            {t("chat.retention.dryRun")}
          </label>
          <p className="text-xs text-gray-500">{t("chat.retention.dryRunHint")}</p>

          <div className="flex gap-2">
            <button
              className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              onClick={() => void save()}
              disabled={saving}
            >
              {t("chat.retention.save")}
            </button>
            <button
              className="rounded border px-3 py-1.5 text-sm"
              onClick={() => void runPreview()}
            >
              {t("chat.retention.preview")}
            </button>
          </div>

          {preview ? (
            <div className="rounded border border-white/10 bg-white/5 p-3 text-sm">
              <p className="mb-2 font-medium">{t("chat.retention.previewTitle")}</p>
              <table className="text-sm">
                <tbody>
                  {Object.entries(preview)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([k, v]) => (
                      <tr key={k}>
                        <td className="pr-4 font-mono text-xs">{k}</td>
                        <td className="text-right tabular-nums">{v}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      {audit ? (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold">{t("chat.retention.audit.title")}</h2>
          {audit.length === 0 ? (
            <EmptyState message={t("chat.retention.audit.empty")} />
          ) : (
            <div className="overflow-x-auto rounded border">
              <table className="w-full text-sm">
                <thead className="bg-white/5 text-left text-xs text-gray-500">
                  <tr>
                    <th className="px-3 py-2">{t("chat.retention.audit.when")}</th>
                    <th className="px-3 py-2">{t("chat.retention.audit.action")}</th>
                    <th className="px-3 py-2">{t("chat.retention.audit.rule")}</th>
                    <th className="px-3 py-2">{t("chat.retention.audit.actor")}</th>
                    <th className="px-3 py-2">{t("chat.retention.audit.counts")}</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((e) => (
                    <tr key={e.id} className="border-t align-top">
                      <td className="whitespace-nowrap px-3 py-2">{fmtWhen(e.created_at)}</td>
                      <td className="px-3 py-2 font-mono text-xs">{e.action}</td>
                      <td className="px-3 py-2 font-mono text-xs">{e.rule}</td>
                      <td className="px-3 py-2 font-mono text-xs">{e.actor}</td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {Object.entries(e.counts || {})
                          .filter(([, v]) => v > 0)
                          .map(([k, v]) => `${k}:${v}`)
                          .join(" ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
