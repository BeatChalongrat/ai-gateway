"use client";

import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { useArtifactDraft } from "@/hooks/useArtifactDraft";
import { DraftControls } from "./DraftControls";
import { ArtifactReview } from "./ArtifactReview";
import { snapBox } from "@/lib/editorGeometry";
import type { ArtifactWork, WorkSelection } from "@/lib/artifactWorkspace";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { FileAttachment, OfficeQualityReport } from "@/lib/types";
import { OfficeQualityNotice } from "./OfficeQualityNotice";
import type { VisualBlock, VisualBox, VisualField, VisualInspection, VisualReview, VisualStyle } from "@/lib/visualEditor";

// Plain text only: never interpret source-file or model-returned HTML.
function EditableText({ field, disabled, selected, onSelect, onChange, onSelection, absolute }: {
  field: VisualField; disabled: boolean; selected: boolean; absolute?: boolean;
  onSelect: () => void; onChange: (text: string) => void; onSelection: (s: WorkSelection) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current && ref.current.innerText !== field.value) ref.current.innerText = field.value; }, [field.value, field.id]);
  function captureSelection() {
    const selection = window.getSelection(); const el = ref.current;
    if (!el || !selection?.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0); if (!el.contains(range.commonAncestorContainer)) return;
    const before = range.cloneRange(); before.selectNodeContents(el); before.setEnd(range.startContainer, range.startOffset);
    const start = before.toString().length; const text = range.toString();
    // Reject ambiguous DOM newline mappings; selecting the entire frame remains available.
    if (field.value.slice(start, start + text.length) === text) onSelection({ id: field.id, start, end: start + text.length, text });
  }
  const s = field.style;
  const css: CSSProperties = { fontSize: s.size ?? 12, fontWeight: s.bold ? 700 : 400, fontStyle: s.italic ? "italic" : "normal", textDecoration: s.underline ? "underline" : "none", color: s.color ?? "#222222", textAlign: s.align ?? "left", whiteSpace: "pre-wrap", overflowWrap: "anywhere", lineHeight: 1.2, marginBottom: absolute ? 0 : s.space_after ?? 8, minHeight: s.size ?? 12, outline: selected ? "2px solid #2874d0" : "none" };
  return <div ref={ref} role="textbox" aria-label={field.id} aria-multiline contentEditable={!disabled} suppressContentEditableWarning style={css}
    onFocus={onSelect} onClick={onSelect} onMouseUp={captureSelection} onKeyUp={captureSelection} onInput={event => onChange(event.currentTarget.innerText)}
    onDrop={event => event.preventDefault()}
    onPaste={event => {
      event.preventDefault(); const selection = window.getSelection();
      if (!selection?.rangeCount) return;
      const range = selection.getRangeAt(0); if (!ref.current?.contains(range.commonAncestorContainer)) return;
      range.deleteContents(); const text = document.createTextNode(event.clipboardData.getData("text/plain")); range.insertNode(text); range.setStartAfter(text); range.collapse(true); selection.removeAllRanges(); selection.addRange(range);
      if (ref.current) onChange(ref.current.innerText);
    }} />;
}

export function VisualFileEditor({ file, onSaved, onDirtyChange }: { file: FileAttachment; onSaved: (file: FileAttachment) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const t = useT();
  const [view, setView] = useState<VisualInspection>();
  const draft = useArtifactDraft(file.id, view?.sha256);
  const { changes, image } = draft.value;
  const setImage = (update: (old: typeof image) => typeof image) => draft.update(old => ({ ...old, image: update(old.image) }), "image");
  const [work, setWork] = useState<ArtifactWork>();
  const [selection, setSelection] = useState<WorkSelection>();
  const [instruction, setInstruction] = useState("");
  const [guides, setGuides] = useState<{ axis: "x" | "y"; at: number }[]>([]);
  const viewport = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(true);
  const [thumbnails, setThumbnails] = useState<Record<number, string>>({});
  const [selected, setSelected] = useState("");
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<VisualReview>();
  const [quality, setQuality] = useState<OfficeQualityReport>();
  const [preview, setPreview] = useState<{ image: string; page: number; count: number }>();
  const [versions, setVersions] = useState<FileAttachment[]>([]);
  const [zoom, setZoom] = useState(.8);
  const [canvasHeight, setCanvasHeight] = useState(800);
  const canvasRef = useRef<HTMLDivElement>(null);
  const dirty = Object.keys(changes).length > 0 || image.rotation !== 0 || image.brightness !== 1 || image.contrast !== 1;
  const latest = !versions.some(v => v.parent_file_id === file.id);
  const field = view?.fields.find(f => f.id === selected);
  const active = field ? changes[field.id] ?? field : undefined;
  const drag = useRef<{ id: string; x: number; y: number; box: VisualBox; resize?: boolean }>();
  useEffect(() => { onDirtyChange?.(busy || (dirty && draft.status !== "saved" && draft.status !== "local")); return () => onDirtyChange?.(false); }, [dirty, busy, draft.status, onDirtyChange]);
  useEffect(() => {
    if (!dirty || draft.status === "saved" || draft.status === "local") return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, draft.status]);
  useEffect(() => {
    let current = true; setBusy(true); setError(""); setPreview(undefined);
    Promise.all([api.editFile(file.id, { mode: "visual_inspect", page }), api.editFile(file.id, { mode: "history" })])
      .then(([result, history]) => { if (current) { setView(result.visual); setVersions(history.versions ?? []); setSelected(""); } })
      .catch(e => { if (current) setError(e.message ?? String(e)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [file.id, page]);
  useEffect(() => {
    if (!canvasRef.current) return;
    const update = () => setCanvasHeight(canvasRef.current?.scrollHeight ?? 800); update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update); observer.observe(canvasRef.current); return () => observer.disconnect();
  }, [view]);
  useEffect(() => {
    let active = true; setWork(undefined); setSelection(undefined); setThumbnails({});
    const refresh = () => { void api.latestArtifactWork(file.id).then(r => { if (active && r.work && r.work.file_id === file.id && !["accepted", "rejected", "cancelled"].includes(r.work.status)) setWork(r.work); }).catch(() => undefined); };
    const delivered = (event: Event) => { if ([file.id, "*"].includes((event as CustomEvent<string>).detail)) refresh(); };
    refresh(); window.addEventListener("sran-artifact-delivered", delivered); window.addEventListener("focus", refresh);
    return () => { active = false; window.removeEventListener("sran-artifact-delivered", delivered); window.removeEventListener("focus", refresh); };
  }, [file.id]);
  useEffect(() => {
    if (!fit || !view || !viewport.current) return;
    const node = viewport.current; const resize = () => setZoom(Math.min(1.5, Math.max(.2, (node.clientWidth - 24) / view.width)));
    resize(); if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(resize); observer.observe(node); return () => observer.disconnect();
  }, [fit, view]);
  useEffect(() => {
    if (!view || !["pdf", "pptx"].includes(view.kind)) return;
    let active = true;
    void (async () => { for (let n = Math.max(0, page - 1); n < Math.min(view.page_count, page + 3); n++) {
      if (!active) break;
      try { const r = await api.editFile(file.id, { mode: "visual_preview", page: n }); const img = r.visual?.pages?.[0]?.image;
        if (active && img) setThumbnails(old => ({ ...Object.fromEntries(Object.entries(old).filter(([key]) => Math.abs(Number(key) - page) <= 3)), [n]: img }));
      } catch { /* main preview retains its error/retry controls */ }
    } })(); return () => { active = false; };
  }, [file.id, page, view?.kind, view?.page_count]);
  useEffect(() => { if (draft.ready && Object.keys(draft.value.changes).length) { setPage(draft.value.page); setSelected(draft.value.selected); } }, [draft.ready, file.id]);
  function updateField(f: VisualField, patch: Partial<VisualField>) { draft.update(old => ({ ...old, page, selected: f.id, changes: { ...old.changes, [f.id]: { ...f, ...old.changes[f.id], ...patch } } }), f.id); }
  async function propose(action: "repair" | "rewrite") {
    if (!view || dirty || busy) return; setBusy(true); setError("");
    try { const result = await api.startArtifactWork(file.id, { action, sha256: view.sha256, page, ...(action === "rewrite" ? { selection, instruction } : {}) }); setWork(result.work); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  function format(patch: VisualStyle) { if (active) updateField(active, { style: { ...active.style, ...patch } }); }
  async function perform(mode: "visual_edit" | "visual_preview" | "restore", target?: string, previewPage = page) {
    if (!view || busy) return; setBusy(true); setError("");
    try {
      const result = await api.editFile(file.id, { mode, sha256: view.sha256, page: mode === "visual_preview" ? previewPage : page, ...(mode === "visual_edit" ? { changes: Object.values(changes).map(({ id, value, style, box }) => ({ id, value, style, box })), image } : {}), ...(target ? { target_id: target } : {}) });
      if (result.review) setReport(result.review);
      if (result.visual?.pages?.[0]) setPreview({ image: result.visual.pages[0].image, page: result.visual.pages[0].page, count: result.visual.page_count });
      if (result.file) { await draft.clear(); setQuality(result.quality); setPage(0); onSaved(result.file); }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      try { const history = await api.editFile(file.id, { mode: "history" }); setVersions(history.versions ?? []); } catch { /* retain draft */ }
    } finally { setBusy(false); }
  }
  const text = (original: VisualField, absolute = false) => {
    const f = changes[original.id] ?? original;
    return <EditableText key={f.id} field={f} selected={selected === f.id} disabled={busy || !latest || !draft.ready || draft.status === "conflict"} absolute={absolute} onSelection={setSelection} onSelect={() => { if (selected !== f.id) setSelection(undefined); setSelected(f.id); }} onChange={value => updateField(f, { value })} />;
  };
  function blocks(items: VisualBlock[]): React.ReactNode {
    return items.map((block, i) => block.kind === "table" ? <table key={i} className="w-full border-collapse"><tbody>{block.rows?.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} className="border border-border-subtle p-2 align-top">{blocks(cell)}</td>)}</tr>)}</tbody></table>
      : block.kind === "image" ? <img key={i} src={block.image} alt="" style={{ maxWidth: "100%", width: block.width, height: "auto" }} />
        : (() => { const f = view?.fields.find(field => field.id === block.id); return f ? text(f) : null; })());
  }
  function move(event: PointerEvent<HTMLButtonElement>) {
    const start = drag.current; if (!start || !view) return;
    const f = view.fields.find(field => field.id === start.id); if (!f) return;
    const dx = (event.clientX - start.x) / zoom; const dy = (event.clientY - start.y) / zoom;
    const box = start.resize ? { ...start.box, width: Math.max(12, start.box.width + dx), height: Math.max(12, start.box.height + dy) } : { ...start.box, left: start.box.left + dx, top: start.box.top + dy };
    const peers = view.fields.filter(p => p.id !== f.id && p.page === page).map(p => (changes[p.id] ?? p).box).filter((b): b is VisualBox => !!b);
    const snapped = snapBox(box, peers, view.width, view.height, start.resize); setGuides(snapped.guides); updateField(f, { box: snapped.box });
  }
  return <section className="visual-editor flex min-h-0 flex-1 flex-col overflow-auto text-sm text-text-primary" aria-label={t("visual.title")} aria-busy={busy} onKeyDown={e => { if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return; if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !busy) { e.preventDefault(); e.shiftKey ? draft.redo() : draft.undo(); } }}>
    <div className="sticky top-0 z-20 space-y-2 border-b border-border-subtle bg-bg-base p-3">
      <p className="text-xs text-text-secondary">{t("visual.hint")}</p><DraftControls draft={draft} disabled={busy} />
      <div className="flex flex-wrap items-center gap-2">
        <button disabled={busy || !dirty || !latest} onClick={() => void perform("visual_edit")}>{t(busy ? "editor.working" : "editor.save")}</button>
        <button disabled={busy || !dirty} onClick={() => void draft.clear()}>{t("editor.discard")}</button>
        <button disabled={busy || dirty || !view} onClick={() => void perform("visual_preview")}>{t("visual.preview")}</button>
        <button disabled={busy || dirty || !latest || !view?.ai_available || view.kind === "pdf" || !!work && ["running", "ready", "accepting"].includes(work.status)} onClick={() => void propose("repair")}>{t("visual.ai")}</button>
        <label>{t("visual.zoom")} <input aria-label={t("visual.zoom")} type="range" min={.4} max={1.5} step={.1} value={zoom} onChange={e => { setFit(false); setZoom(Number(e.target.value)); }} /></label>
      </div>
      <button aria-pressed={fit} onClick={() => setFit(true)}>{t("artifact.fit")}</button>
      {view && !view.ai_available && view.kind !== "pdf" && <p className="text-xs">{t("visual.aiUnavailable")}</p>}
      {view?.kind === "pdf" && <p className="text-xs">{t("visual.pdf")}</p>}
      {view && (view.kind === "pdf" || view.kind === "pptx") && <div className="flex gap-2">
        <button disabled={busy || dirty || page === 0} onClick={() => setPage(p => p - 1)}>{t("editor.previous")}</button>
        <span>{page + 1} / {view.page_count}</span><button disabled={busy || dirty || page + 1 >= view.page_count} onClick={() => setPage(p => p + 1)}>{t("editor.next")}</button>
      </div>}
      {active && <div className="flex flex-wrap items-center gap-2">
        <label>{t("visual.size")} <input type="number" aria-label={t("visual.size")} min={6} max={144} value={active.style.size ?? 12} disabled={busy || !latest} className="w-16 bg-bg-secondary" onChange={e => format({ size: Number(e.target.value) })} /></label>
        <button disabled={busy || !latest} aria-pressed={!!active.style.bold} onClick={() => format({ bold: !active.style.bold })}>{t("visual.bold")}</button>
        {view?.kind !== "pdf" && <><button disabled={busy || !latest} aria-pressed={!!active.style.italic} onClick={() => format({ italic: !active.style.italic })}>{t("visual.italic")}</button>
          <select aria-label={t("visual.align")} disabled={busy || !latest} value={active.style.align ?? "left"} onChange={e => format({ align: e.target.value as VisualStyle["align"] })}><option value="left">{t("visual.left")}</option><option value="center">{t("visual.center")}</option><option value="right">{t("visual.right")}</option><option value="justify">{t("visual.justify")}</option></select></>}
        <input type="color" aria-label={t("visual.color")} disabled={busy || !latest} value={active.style.color ?? "#222222"} onChange={e => format({ color: e.target.value })} />
        {active.box && (Object.keys(active.box) as (keyof VisualBox)[]).map(key => <label key={key} className="text-xs">{t(`visual.${key}`)}<input type="number" aria-label={t(`visual.${key}`)} disabled={busy || !latest} className="w-16 bg-bg-secondary" value={Math.round(active.box![key] * 10) / 10} onChange={e => updateField(active, { box: { ...active.box!, [key]: Number(e.target.value) } })} /></label>)}
      </div>}
      {active && view?.ai_available && <div className="flex flex-wrap items-center gap-2">
        <button disabled={dirty || busy || !active.value} onClick={() => setSelection({ id: active.id, start: 0, end: active.value.length, text: active.value })}>{t("artifact.selectFrame")}</button>
        {selection && <><blockquote className="max-h-16 w-full overflow-auto border-l-2 border-border-subtle pl-2">{selection.text}</blockquote>
        <input aria-label={t("artifact.instruction")} placeholder={t("artifact.instruction")} value={instruction} maxLength={4000} onChange={e => setInstruction(e.target.value)} className="min-w-0 flex-1 rounded border border-border-subtle bg-bg-base p-2" />
        <button disabled={busy || dirty || !latest || !instruction.trim() || !!work && ["running", "ready", "accepting"].includes(work.status)} onClick={() => void propose("rewrite")}>{t("artifact.rewrite")}</button></>}
        {dirty && <span className="text-xs">{t("artifact.saveFirst")}</span>}
      </div>}
      {view?.kind === "image" && <div className="flex flex-wrap gap-2">
        <button disabled={busy || !latest} onClick={() => setImage(v => ({ ...v, rotation: (v.rotation + 90) % 360 }))}>{t("visual.rotate")}</button>
        <label>{t("visual.brightness")}<input type="range" min={.25} max={2} step={.05} value={image.brightness} disabled={busy || !latest} onChange={e => setImage(v => ({ ...v, brightness: Number(e.target.value) }))} /></label>
        <label>{t("visual.contrast")}<input type="range" min={.25} max={2} step={.05} value={image.contrast} disabled={busy || !latest} onChange={e => setImage(v => ({ ...v, contrast: Number(e.target.value) }))} /></label>
      </div>}
      {error && <p role="alert">{error}</p>}
      {!latest && <p>{t("editor.newer")}</p>}
    </div>
    {report && <div role="status" className="m-3 rounded border border-border-subtle p-3"><p>{t("visual.review", { model: report.model, pages: String(report.pages), attempts: String(report.attempts) })}</p><p>{report.status} · {report.assessments.map(a => a.score).join(" → ")}</p>{report.assessments.at(-1)?.issues.map((issue, i) => <p key={i}>{issue}</p>)}{report.warning && <p>{report.warning}</p>}</div>}
    {work && <ArtifactReview work={work} onWork={setWork} onSaved={f => { void draft.clear(); onSaved(f); }} onDismiss={() => setWork(undefined)} />}
    <OfficeQualityNotice report={quality} />
    {Object.keys(thumbnails).length > 0 && <nav aria-label={t("artifact.pages")} className="flex shrink-0 gap-2 overflow-x-auto border-b border-border-subtle p-2">{Object.entries(thumbnails).map(([n, src]) => <button key={n} aria-current={Number(n) === page ? "page" : undefined} disabled={busy || dirty} onClick={() => setPage(Number(n))} className="w-20 shrink-0 rounded border border-border-subtle p-1"><img src={src} alt={t("artifact.page", { n: String(Number(n) + 1) })} className="h-20 w-full object-contain" /><span>{Number(n) + 1}</span></button>)}</nav>}
    {preview && <details open className="m-3"><summary>{t("visual.preview")}</summary><div className="flex gap-2"><button disabled={busy || preview.page === 0} onClick={() => void perform("visual_preview", undefined, preview.page - 1)}>{t("editor.previous")}</button><span>{preview.page + 1} / {preview.count}</span><button disabled={busy || preview.page + 1 >= preview.count} onClick={() => void perform("visual_preview", undefined, preview.page + 1)}>{t("editor.next")}</button></div><img src={preview.image} alt={t("visual.preview")} className="max-w-full" /></details>}
    {view && <div ref={viewport} className="overflow-auto bg-bg-secondary p-3"><div style={{ width: view.width * zoom, height: canvasHeight * zoom }}>
      <div ref={canvasRef} style={{ position: "relative", width: view.width, minHeight: view.height, height: view.kind === "docx" ? undefined : view.height, transform: `scale(${zoom})`, transformOrigin: "top left", background: view.kind === "image" ? "transparent" : "white", color: "#222222", fontFamily: "Sarabun, sans-serif", padding: view.kind === "docx" ? `${view.margins?.top ?? 36}px ${view.margins?.right ?? 36}px ${view.margins?.bottom ?? 36}px ${view.margins?.left ?? 36}px` : 0 }}>
        {view.kind === "docx" ? blocks(view.blocks ?? []) : view.kind === "image" ? <img src={view.pages?.[0]?.image} alt={file.filename} style={{ width: view.width, height: view.height, objectFit: "contain", transform: `rotate(${image.rotation}deg)`, filter: `brightness(${image.brightness}) contrast(${image.contrast})` }} /> : <>
          {view.background && <img src={view.background} alt="" draggable={false} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }} />}
          {view.fields.filter(f => f.page === page).map(original => { const f = changes[original.id] ?? original; if (!f.box) return null;
            return <div key={f.id} style={{ position: "absolute", ...f.box, overflow: "visible", boxSizing: "border-box", padding: `${f.insets?.top ?? 0}px ${f.insets?.right ?? 0}px ${f.insets?.bottom ?? 0}px ${f.insets?.left ?? 0}px`, display: "flex", flexDirection: "column", justifyContent: f.vertical === "middle" ? "center" : f.vertical === "bottom" ? "flex-end" : "flex-start" }}>{text(original, true)}
              {selected === f.id && <button aria-label={t("artifact.resize")} disabled={busy || !latest} style={{ position: "absolute", right: -5, bottom: -5, width: 12, height: 12, background: "#2874d0", cursor: "nwse-resize", touchAction: "none" }} onPointerDown={e => { drag.current = { id: f.id, x: e.clientX, y: e.clientY, box: f.box!, resize: true }; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={move} onPointerUp={() => { drag.current = undefined; setGuides([]); }} onPointerCancel={() => { drag.current = undefined; setGuides([]); }} />}
              {selected === f.id && <button aria-label={t("visual.move")} disabled={busy || !latest} style={{ position: "absolute", top: -20, right: 0, touchAction: "none", color: "#222222", background: "white" }} onPointerDown={e => { drag.current = { id: f.id, x: e.clientX, y: e.clientY, box: f.box! }; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={move} onPointerUp={() => { drag.current = undefined; setGuides([]); }} onPointerCancel={() => { drag.current = undefined; setGuides([]); }}>{t("visual.move")}</button>}
            </div>;
          })}
          {guides.map((g, i) => <div key={i} aria-hidden style={{ position: "absolute", pointerEvents: "none", background: "#2874d0", ...(g.axis === "x" ? { left: g.at, top: 0, width: 1, height: view.height } : { top: g.at, left: 0, height: 1, width: view.width }) }} />)}
        </>}
      </div>
    </div></div>}
    <details className="m-3"><summary>{t("editor.history")} ({versions.length})</summary>{versions.map(v => <div key={v.id} className="flex flex-wrap gap-3 py-1">
      <span>{t("editor.version", { n: String(v.version ?? 1) })} · {new Date(v.created_at).toLocaleString()}</span><button disabled={busy || dirty} onClick={() => { setPage(0); setReport(undefined); setQuality(undefined); onSaved(v); }}>{t("editor.open")}</button><button disabled={busy || dirty || !latest || v.id === file.id} onClick={() => void perform("restore", v.id)}>{t("editor.restore")}</button><button onClick={() => void api.downloadFile(v.id, v.filename)}>{t("preview.download")}</button>
      {!!v.change_log?.length && <details><summary>{t("editor.details")}</summary><pre className="whitespace-pre-wrap break-all text-xs">{v.change_log.join("\n")}</pre></details>}
    </div>)}</details>
  </section>;
}
