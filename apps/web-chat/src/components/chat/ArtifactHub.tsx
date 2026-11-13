"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { ArtifactLibraryItem } from "@/lib/artifactWorkspace";
import type { FileAttachment } from "@/lib/types";
import { useSidePaneStore } from "@/stores/sidePaneStore";
import { TemplateGallery } from "./TemplateGallery";
import { AuthedImage } from "./AuthedImage";

const FILE_FORMATS = ["docx", "pptx", "xlsx", "pdf", "csv", "png", "jpg", "webp", "mp4"];

function Thumbnail({ file }: { file: FileAttachment }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    if (!/\.(pdf|pptx|docx)$/i.test(file.filename)) return;
    let active = true; void api.editFile(file.id, { mode: "visual_preview", page: 0 }).then(r => { if (active) setSrc(r.visual?.pages?.[0]?.image ?? ""); }).catch(() => undefined);
    return () => { active = false; };
  }, [file.id, file.filename]);
  if (file.content_type.startsWith("image/")) return <AuthedImage fileId={file.id} alt={file.filename} className="h-28 w-full object-contain" />;
  return src ? <img src={src} alt={file.filename} className="h-28 w-full object-contain" /> : <span className="flex h-28 items-center justify-center bg-bg-secondary text-lg uppercase">{file.filename.split(".").pop()}</span>;
}

export function ArtifactHub({ conversationId }: { conversationId: string }) {
  const t = useT(); const router = useRouter(); const dialog = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false); const [tab, setTab] = useState<"library" | "templates">("library");
  const [query, setQuery] = useState(""); const [kind, setKind] = useState(""); const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<ArtifactLibraryItem[]>([]); const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false); const [error, setError] = useState(""); const [reload, setReload] = useState(0);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  useEffect(() => {
    if (!open || tab !== "library") return;
    let active = true; setLoading(true); const timer = setTimeout(() => {
      void api.artifactLibrary(query, kind, offset).then(r => { if (active) { setItems(r.items); setMore(r.has_more); setError(""); } }).catch(e => { if (active) setError(String(e.message ?? e)); }).finally(() => { if (active) setLoading(false); });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [open, tab, query, kind, offset, reload]);
  function openFile(file: FileAttachment) { useSidePaneStore.getState().open({ kind: "preview", file }); setOpen(false); }
  return <>
    <button className="rounded px-2 py-1 text-xs text-text-secondary" onClick={() => setOpen(true)}>{t("artifact.library")}</button>
    <dialog ref={dialog} onCancel={() => setOpen(false)} onClose={() => setOpen(false)} aria-label={t("artifact.library")} className="m-auto max-h-[85vh] w-[min(900px,94vw)] overflow-auto rounded-xl border border-border-subtle bg-bg-base p-5 text-text-primary backdrop:bg-black/40">
      <div className="mb-4 flex items-center gap-4"><button aria-pressed={tab === "library"} onClick={() => setTab("library")}>{t("artifact.library")}</button><button aria-pressed={tab === "templates"} onClick={() => setTab("templates")}>{t("artifact.templates")}</button><button className="ml-auto" onClick={() => setOpen(false)}>{t("common.close")}</button></div>
      {open && (tab === "templates" ? <TemplateGallery conversationId={conversationId} onCreated={openFile} /> : <div className="space-y-3">
        <p className="text-xs text-text-secondary">{t("artifact.libraryHint")}</p>
        <div className="flex gap-2"><input aria-label={t("artifact.search")} placeholder={t("artifact.search")} value={query} maxLength={200} onChange={e => { setQuery(e.target.value); setOffset(0); }} className="min-w-0 flex-1 rounded border border-border-subtle bg-bg-base p-2" /><select aria-label={t("artifact.format")} value={kind} onChange={e => { setKind(e.target.value); setOffset(0); }}><option value="">{t("artifact.allFormats")}</option>{FILE_FORMATS.map(ext => <option key={ext}>{ext}</option>)}</select></div>
        {loading && <p role="status">{t("editor.working")}</p>}
        {error && <p role="alert">{error} <button onClick={() => setReload(n => n + 1)}>{t("artifact.sync")}</button></p>}
        {!loading && !items.length && !error && <p>{t("artifact.empty")}</p>}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">{items.map((item, index) => <article key={item.file.id} className="overflow-hidden rounded border border-border-subtle">
          {index < 6 && <Thumbnail file={item.file} />}<div className="space-y-2 p-3"><strong className="block break-all text-sm">{item.file.filename}</strong><p className="text-xs text-text-secondary">{t("editor.version", { n: String(item.file.version ?? 1) })} · {new Date(item.file.created_at).toLocaleDateString()}</p>
          <div className="flex flex-wrap gap-3 text-xs"><button onClick={() => openFile(item.file)}>{t("editor.open")}</button><button onClick={() => { setOpen(false); if (item.conversation_id === conversationId) openFile(item.file); else router.push(`/chat/${item.conversation_id}?artifact=${encodeURIComponent(item.file.id)}`); }}>{t("artifact.continue")}</button><button onClick={() => void api.downloadFile(item.file.id, item.file.filename)}>{t("preview.download")}</button></div></div>
        </article>)}</div>
        <div className="flex gap-3"><button disabled={loading || !offset} onClick={() => setOffset(n => Math.max(0, n - 40))}>{t("editor.previous")}</button><button disabled={loading || !more} onClick={() => setOffset(n => n + 40)}>{t("editor.next")}</button></div>
      </div>)}
    </dialog>
  </>;
}
