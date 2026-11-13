"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { ArtifactWork } from "@/lib/artifactWorkspace";
import type { FileAttachment } from "@/lib/types";

const STAGES = ["inspect", "render_before", "review", "apply", "render_after", "verify", "ready"] as const;
export function ArtifactReview({ work, onWork, onSaved, onDismiss }: { work: ArtifactWork; onWork: (w: ArtifactWork) => void; onSaved: (f: FileAttachment) => void; onDismiss: () => void }) {
  const t = useT(); const [selected, setSelected] = useState<string[]>([]); const [page, setPage] = useState(0); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { if (work.status === "ready") setSelected((work.changes ?? []).map(c => c.id)); }, [work.id, work.status]);
  useEffect(() => {
    if (work.status !== "running" && work.status !== "accepting") return;
    let active = true; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { try { const res = await api.getArtifactWork(work.id); if (active) { setError(""); onWork(res.work); } } catch (e) { if (active) setError(e instanceof Error ? e.message : String(e)); } finally { if (active) timer = setTimeout(poll, 1200); } };
    timer = setTimeout(poll, 500); return () => { active = false; clearTimeout(timer); };
  }, [work.id, work.status, onWork]);
  async function control(action: "accept" | "reject" | "cancel" | "retry") {
    setBusy(true); setError("");
    try { const result = await api.controlArtifactWork(work.id, action, selected); if (result.file) { onSaved(result.file); onDismiss(); } else { onWork((await api.getArtifactWork(work.id)).work); } }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  const running = work.status === "running" || work.status === "accepting";
  const stage = work.stage === "suggest" ? 2 : Math.max(0, STAGES.indexOf(work.stage as typeof STAGES[number]));
  const before = work.before_pages?.[page]; const after = work.after_pages?.[page];
  return <section className="m-3 space-y-3 rounded-lg border border-border-subtle bg-bg-base p-3" aria-label={t("artifact.reviewTitle")}>
    <div className="flex items-center justify-between gap-2"><strong>{t("artifact.reviewTitle")}</strong><span role="status">{t(`artifact.status.${work.status}`)}</span></div>
    {running && <><ol className="flex flex-wrap gap-2 text-xs">{STAGES.map((s, i) => <li key={s} aria-current={i === stage ? "step" : undefined} className={i === stage ? "font-semibold text-text-primary" : "text-text-secondary"}>{i < stage ? "✓ " : ""}{t(`artifact.stage.${s}`)}</li>)}</ol><p className="text-xs text-text-secondary">{t("artifact.background")}</p></>}
    {(error || work.error) && <p role="alert" className="text-sm">{error || work.error}</p>}
    {work.status === "ready" && <>
      <p className="text-xs">{t("artifact.reviewHint")}</p>
      <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
        <figure><figcaption>{t("artifact.before")}</figcaption>{before && <img src={before.image} alt={t("artifact.before")} className="w-full rounded border border-border-subtle" />}</figure>
        <figure><figcaption>{t("artifact.after")}</figcaption>{after && <img src={after.image} alt={t("artifact.after")} className="w-full rounded border border-border-subtle" />}</figure>
      </div>
      {(work.before_pages?.length ?? 0) > 1 && <div className="flex items-center gap-2"><button disabled={page === 0} onClick={() => setPage(n => n - 1)}>{t("editor.previous")}</button><span>{page + 1} / {work.before_pages?.length}</span><button disabled={page + 1 >= (work.before_pages?.length ?? 0)} onClick={() => setPage(n => n + 1)}>{t("editor.next")}</button></div>}
      <ul className="max-h-64 space-y-2 overflow-auto">{work.changes?.map((change, index) => <li key={change.id} className="rounded border border-border-subtle p-2"><label className="flex items-start gap-2"><input type="checkbox" checked={selected.includes(change.id)} onChange={e => setSelected(old => e.target.checked ? [...old, change.id] : old.filter(id => id !== change.id))} /><span className="min-w-0 flex-1"><strong>{t("artifact.change", { n: String(index + 1) })}</strong>{change.id === "image" ? <p>{t("artifact.imageChange")}</p> : <>{change.before.value !== change.after.value ? <><del className="block whitespace-pre-wrap text-text-secondary">{change.before.value}</del><ins className="block whitespace-pre-wrap no-underline">{change.after.value}</ins></> : <p className="line-clamp-2 whitespace-pre-wrap">{change.after.value}</p>}<div className="text-xs text-text-secondary">{JSON.stringify(change.before.style) !== JSON.stringify(change.after.style) && t("artifact.formatChanged")}{JSON.stringify(change.before.box) !== JSON.stringify(change.after.box) && ` · ${t("artifact.positionChanged")}`}</div></>}</span></label></li>)}</ul>
      {selected.length !== work.changes?.length && <p className="text-xs">{t("artifact.partialHint")}</p>}
    </>}
    {work.review?.warning && <p className="text-xs text-text-secondary">{work.review.warning}</p>}
    <div className="flex flex-wrap gap-2">
      {work.status === "ready" && <><button disabled={busy || selected.length === 0} onClick={() => void control("accept")}>{t("artifact.accept", { n: String(selected.length) })}</button><button disabled={busy} onClick={() => void control("reject")}>{t("artifact.reject")}</button></>}
      {work.status === "running" && <button disabled={busy} onClick={() => void control("cancel")}>{t("artifact.cancel")}</button>}
      {work.status === "failed" && <><button disabled={busy} onClick={() => void control("retry")}>{t("artifact.retryStage")}</button><button disabled={busy} onClick={() => void control("cancel")}>{t("artifact.cancel")}</button></>}
      {!running && work.status !== "ready" && <button onClick={onDismiss}>{t("common.close")}</button>}
    </div>
  </section>;
}
