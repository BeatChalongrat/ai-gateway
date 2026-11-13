"use client";

import { useEffect, useState } from "react";
import { useArtifactDraft } from "@/hooks/useArtifactDraft";
import { DraftControls } from "./DraftControls";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { EditorField, EditorInspection } from "@/lib/fileEditor";
import type { FileAttachment, OfficeQualityReport } from "@/lib/types";
import { OfficeQualityNotice } from "./OfficeQualityNotice";

export function FileEditor({ file, onSaved, onDirtyChange }: { file: FileAttachment; onSaved: (file: FileAttachment) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const t = useT();
  const [data, setData] = useState<EditorInspection>();
  const draft = useArtifactDraft(file.id, data?.sha256);
  const changes = draft.value.structured ?? {};
  const setChanges = (update: Record<string, EditorField> | ((old: Record<string, EditorField>) => Record<string, EditorField>)) => draft.update(old => ({ ...old, structured: typeof update === "function" ? update(old.structured ?? {}) : update, sheet, offset }), "cells");
  const [versions, setVersions] = useState<FileAttachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [quality, setQuality] = useState<OfficeQualityReport>();
  const [sheet, setSheet] = useState("");
  const [offset, setOffset] = useState(0);
  const dirty = Object.keys(changes).length > 0;
  useEffect(() => { onDirtyChange?.(busy || dirty && draft.status !== "saved" && draft.status !== "local"); return () => onDirtyChange?.(false); }, [dirty, busy, draft.status, onDirtyChange]);
  useEffect(() => {
    if (!dirty || draft.status === "saved" || draft.status === "local") return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, draft.status]);
  // Each mount/file change gets its own response guard, including StrictMode.
  useEffect(() => {
    let current = true;
    setBusy(true); setError("");
    Promise.all([api.editFile(file.id, { mode: "inspect", sheet, offset }), api.editFile(file.id, { mode: "history" })])
      .then(([inspection, history]) => { if (current) { setData(inspection.inspection); setVersions(history.versions ?? []); } })
      .catch(e => { if (current) setError(String(e.message ?? e)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [file.id, sheet, offset]);
  useEffect(() => { if (draft.ready && draft.value.structured && Object.keys(draft.value.structured).length) { setSheet(draft.value.sheet ?? ""); setOffset(draft.value.offset ?? 0); } }, [draft.ready, file.id]);
  const head = versions.filter(v => !versions.some(child => child.parent_file_id === v.id)).sort((a, b) => (b.version ?? 1) - (a.version ?? 1) || b.created_at.localeCompare(a.created_at))[0];
  const latest = !versions.some(v => v.parent_file_id === file.id);
  async function save(target?: string) {
    if (!data || busy) return;
    setBusy(true); setError("");
    try {
      const result = await api.editFile(file.id, target
        ? { mode: "restore", target_id: target }
        : { mode: "edit", sha256: data.sha256, sheet, offset, changes: Object.values(changes) });
      if (result.file) { await draft.clear(); setQuality(result.quality); onSaved(result.file); }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // Discover a competing head without throwing away the user's draft.
      try { const history = await api.editFile(file.id, { mode: "history" }); setVersions(history.versions ?? []); } catch { /* Keep the original error/draft. */ }
    }
    finally { setBusy(false); }
  }
  return <section className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-4 text-sm text-text-primary" aria-label={t("editor.title")}>
    <p className="text-xs text-text-secondary">{t("editor.limits")}</p><DraftControls draft={draft} disabled={busy} />
    {data?.pdf_notes_only && <p role="note">{t("editor.pdf")}</p>}
    {error && <p role="alert">{error}</p>}
    {!latest && <button onClick={() => head && onSaved(head)} disabled={busy || dirty}>{t("editor.newer")}</button>}
    <div className="flex flex-wrap gap-3">
      {!!data?.sheets.length && <select aria-label={t("editor.sheet")} disabled={busy || dirty} value={sheet || data.sheets[0]} onChange={e => { setSheet(e.target.value); setOffset(0); }}>
        {data.sheets.map(name => <option key={name}>{name}</option>)}
      </select>}
      {(data?.format === "xlsx" || data?.format === "csv") && <>
        <button disabled={busy || dirty || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>{t("editor.previous")}</button>
        <span>{offset + 1}–{offset + 50}</span>
        <button disabled={busy || dirty || !data.fields.length} onClick={() => setOffset(offset + 50)}>{t("editor.next")}</button>
      </>}
      <button disabled={busy || !dirty || !latest} onClick={() => void save()} className="rounded border border-border-subtle px-3 py-1">{busy ? t("editor.working") : t("editor.save")}</button>
      <button disabled={busy || !dirty} onClick={() => void draft.clear()}>{t("editor.discard")}</button>
    </div>
    <OfficeQualityNotice report={quality} />
    <details><summary>{t("editor.history")} ({versions.length})</summary>
      <ul className="space-y-2 py-2">{versions.map(v => <li key={v.id} className="rounded border border-border-subtle p-2">
        <span>{t("editor.version", { n: String(v.version ?? 1) })} · {new Date(v.created_at).toLocaleString()}</span>
        <div className="flex gap-3">
          <button disabled={busy || dirty} onClick={() => onSaved(v)}>{t("editor.open")}</button>
          <button disabled={busy || dirty || !latest || v.id === file.id} onClick={() => void save(v.id)}>{t("editor.restore")}</button>
          <button onClick={() => void api.downloadFile(v.id, v.filename)}>{t("preview.download")}</button>
        </div>
        {!!v.change_log?.length && <details><summary>{t("editor.details")}</summary><pre className="whitespace-pre-wrap break-all text-xs">{v.change_log.join("\n")}</pre></details>}
      </li>)}</ul>
    </details>
    <div className="space-y-2">{data?.fields.map(field => {
      const value = changes[field.id] ?? field;
      return <div key={field.id} className="flex items-start gap-2">
        <label className="flex min-w-0 flex-1 flex-col gap-1"><span className="break-all text-xs text-text-secondary">{field.id}</span>
          <textarea aria-label={field.id} disabled={busy || !latest || !draft.ready || draft.status === "conflict"} rows={data.format === "txt" || data.format === "md" || data.format === "json" ? 20 : 2}
            className="w-full rounded border border-border-subtle bg-bg-base p-2" value={value.value}
            onChange={e => setChanges(old => ({ ...old, [field.id]: { ...value, value: e.target.value } }))} />
        </label>
        {data.format === "xlsx" && <select aria-label={`${field.id} ${t("editor.type")}`} disabled={busy || !latest || !draft.ready || draft.status === "conflict"} value={value.type}
          onChange={e => setChanges(old => ({ ...old, [field.id]: { ...value, type: e.target.value } }))}>
          <option value="text">{t("editor.text")}</option><option value="number">{t("editor.number")}</option><option value="formula">{t("editor.formula")}</option>
        </select>}
      </div>;
    })}</div>
  </section>;
}
