"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useT, useI18nStore } from "@/lib/i18n";
import { WORKSPACE_TEMPLATES, templateTable, type TemplateID } from "@/lib/workspaceTemplates";
import type { FileAttachment } from "@/lib/types";

export function TemplateGallery({ conversationId, onCreated }: { conversationId: string; onCreated: (file: FileAttachment) => void }) {
  const t = useT(); const locale = useI18nStore(s => s.locale);
  const [selected, setSelected] = useState<TemplateID>(); const [title, setTitle] = useState("");
  const [sections, setSections] = useState<Record<string, string>>({}); const [table, setTable] = useState("");
  const [customer, setCustomer] = useState(""); const [number, setNumber] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const template = WORKSPACE_TEMPLATES.find(item => item.id === selected);
  async function create() {
    if (!template || !title.trim()) return; setBusy(true); setError("");
    try {
      const data: Record<string, unknown> = { title: title.trim(), language: locale };
      if (template.sections.length) {
        data.sections = template.sections.filter(heading => sections[heading]?.trim()).map(heading => ({ heading: t(`artifact.section.${heading}`), body: sections[heading].trim() }));
        if (!(data.sections as unknown[]).length) { setError(t("artifact.contentRequired")); return; }
      } else {
        let parsed; try { parsed = templateTable(template.id, table); } catch { setError(t("artifact.tableError")); return; }
        if (template.id === "pdf.invoice") Object.assign(data, { customer, invoice_number: number, currency: "THB", items: parsed.rows });
        else Object.assign(data, parsed);
      }
      const result = await api.createWorkspaceTemplate(conversationId, template.id, data);
      if (result.file) onCreated(result.file);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }
  return <div className="space-y-4">
    <p className="text-sm text-text-secondary">{t("artifact.templateHint")}</p>
    {!template ? <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">{WORKSPACE_TEMPLATES.map(item => <button key={item.id} className="overflow-hidden rounded-lg border border-border-subtle text-left" onClick={() => { setSelected(item.id); setSections({}); setTable(""); setError(""); }}>
      <img src={`/templates/${item.id}.png`} alt={t(`artifact.template.${item.id}`)} loading="lazy" className="h-36 w-full bg-bg-secondary object-contain" /><span className="block p-2 text-sm">{t(`artifact.template.${item.id}`)} <small className="uppercase">{item.id.split(".")[0]}</small></span>
    </button>)}</div> : <form className="space-y-3" onSubmit={e => { e.preventDefault(); void create(); }}>
      <button type="button" disabled={busy} onClick={() => setSelected(undefined)}>{t("artifact.backGallery")}</button>
      <h3>{t(`artifact.template.${template.id}`)}</h3>
      <img src={`/templates/${template.id}.png`} alt={t(`artifact.template.${template.id}`)} className="max-h-56 w-full object-contain" />
      <label className="block">{t("artifact.title")}<input required maxLength={160} value={title} onChange={e => setTitle(e.target.value)} className="block w-full rounded border border-border-subtle bg-bg-base p-2" /></label>
      {template.sections.map(heading => <label key={heading} className="block">{t(`artifact.section.${heading}`)}<textarea value={sections[heading] ?? ""} maxLength={20000} onChange={e => setSections(old => ({ ...old, [heading]: e.target.value }))} rows={3} className="block w-full rounded border border-border-subtle bg-bg-base p-2" /></label>)}
      {template.id === "pdf.invoice" && <><label className="block">{t("artifact.customer")}<input required value={customer} onChange={e => setCustomer(e.target.value)} className="block w-full border border-border-subtle bg-bg-base p-2" /></label><label className="block">{t("artifact.invoiceNumber")}<input required value={number} onChange={e => setNumber(e.target.value)} className="block w-full border border-border-subtle bg-bg-base p-2" /></label></>}
      {!template.sections.length && <label className="block">{t("artifact.table")}<p className="text-xs text-text-secondary">{t(template.id === "xlsx.budget" ? "artifact.budgetColumns" : template.id === "xlsx.tracker" ? "artifact.trackerColumns" : template.id === "pdf.invoice" ? "artifact.invoiceColumns" : "artifact.headerColumns")}</p><textarea required rows={7} value={table} onChange={e => setTable(e.target.value)} className="block w-full rounded border border-border-subtle bg-bg-base p-2 font-mono" /></label>}
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={busy} className="rounded border border-border-subtle px-4 py-2">{t(busy ? "editor.working" : "artifact.create")}</button>
    </form>}
  </div>;
}
