"use client";

// AM4 — the agent's settings page: which model reads the documents (the
// install's default; a round can differ from the composer), and what the
// agent keeps, for how long, and whose it is.

import { useState } from "react";
import { Lock } from "lucide-react";

import { formatBytes } from "@/lib/fileUtil";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { ModelPicker } from "../types";
import { cls, ErrorPanel, Spin } from "../ui";
import { collapseOptions, optionFacts, useModelPicker } from "../workspace/ModelControl";
import { useStorage } from "../workspace/UploadInfo";

export function ModelSection({ installId }: { installId: string }) {
  const t = useAmT();
  const [picker, setPicker] = useModelPicker(installId);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!picker) return null;
  const choose = (p: ModelPicker, id: string) => {
    if (id === p.choice) return;
    setSaving(id);
    setError(null);
    amApi.putModel(installId, id).then(
      (next) => {
        setSaving(null);
        setPicker(next);
      },
      (err) => {
        setSaving(null);
        setError(errorMessage(err));
      },
    );
  };
  return (
    <section className="mt-8" aria-labelledby="am-models" data-testid="am-settings-models">
      <h2 id="am-models" className="text-sm font-semibold text-text-primary">
        {picker.uses === "text" ? t("model.labelText") : t("model.label")}
      </h2>
      <p className="mt-0.5 text-xs text-text-muted">{picker.reads}</p>
      {picker.uses === "none" ? null : (
        <>
          <p className="mt-1 text-xs text-text-muted">{t("model.sectionBody")}</p>
          <div role="radiogroup" aria-labelledby="am-models" className="mt-2.5 flex flex-col gap-2">
            {collapseOptions(picker.options ?? []).map((o) => {
              const selected = o.id === picker.choice;
              return (
                <button
                  key={o.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  aria-disabled={o.disabled}
                  disabled={o.disabled || !!saving}
                  onClick={() => choose(picker, o.id)}
                  className={cls(
                    "focus-ring flex flex-col items-start gap-0.5 rounded-xl border px-4 py-3 text-left disabled:cursor-not-allowed",
                    selected ? "border-border-strong bg-bg-elevated" : "border-border-subtle bg-bg-surface hover:border-border",
                    o.disabled ? "opacity-60" : "",
                  )}
                  data-testid="am-settings-model"
                  data-model={o.model}
                >
                  <span className="flex items-center gap-2 text-sm font-medium text-text-primary">
                    <span
                      aria-hidden
                      className={cls("inline-block h-3.5 w-3.5 rounded-full border", selected ? "border-[5px] border-text-primary" : "border-border-strong")}
                    />
                    {o.disabled ? <Lock width={12} height={12} aria-hidden /> : null}
                    <span>{o.label}</span>
                    {saving === o.id ? <Spin size={12} /> : null}
                  </span>
                  <span className="pl-5 text-xs text-text-muted">{optionFacts(t, o)}</span>
                  {o.disabled && o.reason ? <span className="pl-5 text-xs text-status-warning">{o.reason}</span> : null}
                </button>
              );
            })}
          </div>
          {picker.single_in_house ? (
            <p className="mt-2 text-xs text-text-secondary" data-testid="am-settings-model-single">
              {picker.notice || t("model.single")}
            </p>
          ) : null}
          <p className="mt-2 text-xs text-text-muted">
            {picker.documents_may_leave ? t("model.policyOutside") : t("model.policyInside")}
          </p>
          <p className="mt-1 text-xs text-text-muted">{picker.note}</p>
        </>
      )}
      {error ? (
        <div className="mt-2">
          <ErrorPanel message={error} />
        </div>
      ) : null}
    </section>
  );
}

export function StorageSection({ installId }: { installId: string }) {
  const t = useAmT();
  const info = useStorage(installId);
  if (!info) return null;
  const s = info.storage;
  return (
    <section className="mt-8" aria-labelledby="am-storage" data-testid="am-settings-storage">
      <h2 id="am-storage" className="text-sm font-semibold text-text-primary">
        {t("storage.title")}
      </h2>
      <div className="mt-2.5 flex flex-col gap-1.5 rounded-xl border border-border-subtle bg-bg-surface px-4 py-3 text-sm text-text-secondary">
        <p>{t("storage.used", { size: formatBytes(s.total), uploads: formatBytes(s.uploads), outputs: formatBytes(s.outputs) })}</p>
        {s.warn ? (
          <p className="text-status-warning" role="status">
            {t("storage.warn", { limit: formatBytes(s.warn_bytes) })}
          </p>
        ) : null}
        <p>{info.retention}</p>
        <p>{info.not_archive}</p>
        <p className="text-xs text-text-muted">{t("storage.deleteNote")}</p>
      </div>
    </section>
  );
}
