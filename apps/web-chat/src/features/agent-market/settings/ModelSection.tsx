"use client";

// AM4 — the agent's settings page: the agent's model, and what the agent
// keeps, for how long, and whose it is.
//
// AM6 — the model is chosen with the same control the workspace has (the
// chat's model menu); this page adds the one sentence that says what the
// model does for this agent.

import { formatBytes } from "@/lib/fileUtil";

import { useAmT } from "../i18n";
import { EgressLine, ModelControl, useAgentModels } from "../workspace/ModelControl";
import { useStorage } from "../workspace/UploadInfo";

export function ModelSection({ installId }: { installId: string }) {
  const t = useAmT();
  const [models, setModels] = useAgentModels(installId);
  if (!models) return null;
  return (
    <section className="mt-8" aria-labelledby="am-models" data-testid="am-settings-models">
      <h2 id="am-models" className="text-sm font-semibold text-text-primary">
        {models.uses === "text" ? t("model.labelText") : t("model.label")}
      </h2>
      <p className="mt-0.5 text-xs text-text-muted">{models.reads}</p>
      {models.uses === "none" ? null : <EgressLine egress={models.egress} className="mt-1 text-xs text-text-muted" />}
      {models.uses === "none" ? null : (
        <div className="mt-2.5 flex">
          <ModelControl installId={installId} models={models} onModels={setModels} />
        </div>
      )}
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
