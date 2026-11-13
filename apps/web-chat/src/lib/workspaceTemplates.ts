export const WORKSPACE_TEMPLATES = [
  { id: "pptx.executive_brief", sections: ["Summary", "Evidence", "Recommendation", "Next steps"] },
  { id: "pptx.project_update", sections: ["Progress", "Milestones", "Risks", "Decisions"] },
  { id: "pptx.proposal", sections: ["Problem", "Approach", "Scope", "Timeline", "Cost", "Next steps"] },
  { id: "docx.business_report", sections: ["Executive summary", "Findings", "Recommendations", "Sources"] },
  { id: "docx.proposal", sections: ["Objective", "Scope", "Deliverables", "Timeline", "Commercial terms"] },
  { id: "docx.meeting_minutes", sections: ["Attendees", "Discussion", "Decisions", "Actions"] },
  { id: "pdf.business_report", sections: ["Summary", "Findings", "Recommendations", "Sources"] },
  { id: "pdf.invoice", sections: [] }, { id: "xlsx.budget", sections: [] },
  { id: "xlsx.tracker", sections: [] }, { id: "xlsx.analysis", sections: [] }, { id: "csv.data_export", sections: [] },
] as const;
export type TemplateID = typeof WORKSPACE_TEMPLATES[number]["id"];

/** Paste a spreadsheet rectangle: use tab separators, preserve text, type only known numeric columns. */
export function templateTable(id: TemplateID, input: string) {
  if (!input.trim()) throw new Error("Empty table");
  const lines = input.replace(/^\r?\n+|\r?\n+$/g, "").split(/\r?\n/).map(line => line.split("\t"));
  const columns = id === "xlsx.budget" ? ["item", "planned", "actual"] : id === "xlsx.tracker" ? ["task", "owner", "status", "due_date"] : id === "pdf.invoice" ? ["description", "quantity", "unit_price"] : lines.shift() ?? [];
  if (!columns.length || new Set(columns).size !== columns.length || columns.some(c => !c.trim()) || !lines.length || lines.length > 1000 || columns.length > 12 || lines.some(row => row.length !== columns.length)) throw new Error("Invalid table dimensions");
  const numeric = new Set(id === "xlsx.budget" ? ["planned", "actual"] : id === "pdf.invoice" ? ["quantity", "unit_price"] : []);
  const rows = lines.map(line => Object.fromEntries(columns.map((key, n) => { const value = line[n]; if (numeric.has(key) && (!value.trim() || !Number.isFinite(Number(value)))) throw new Error("Invalid number"); return [key, numeric.has(key) ? Number(value) : value]; })));
  return { rows, columns: columns.map(key => ({ key, label: key, type: numeric.has(key) ? "number" : "text" })) };
}
