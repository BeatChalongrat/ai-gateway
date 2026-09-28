"use client";

// AM4 — one compact control near the composer: which model reads this
// round's documents. "อัตโนมัติ (แนะนำ)" first; below it the organisation's
// short list, each row saying what both an engineer and an accountant need —
// the outcome, the real model, inside or outside the organisation, a rough
// cost and time. A model outside the organisation is shown disabled with the
// reason until the organisation lets documents leave. The pick applies to
// this round; "ใช้เป็นค่าตั้งต้น" keeps it for the agent.

import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, Lock } from "lucide-react";

import { amApi, errorMessage } from "../api";
import { formatDuration } from "../format";
import { useAmT } from "../i18n";
import type { ModelOption, ModelPicker } from "../types";
import { cls, Spin } from "../ui";

export function optionLine(t: ReturnType<typeof useAmT>, o: ModelOption): string {
  const parts = [o.label];
  if (o.model) parts.push(o.model);
  parts.push(o.self_hosted ? t("where.inOrg") : t("where.outside"));
  parts.push(o.cost_thb != null ? t("model.cost", { baht: o.cost_thb.toFixed(2) }) : t("model.costNone"));
  if (o.seconds != null && o.seconds > 0) parts.push(formatDuration(o.seconds * 1000, t));
  return parts.join(" · ");
}

export function useModelPicker(installId: string | null): [ModelPicker | null, (p: ModelPicker) => void] {
  const [picker, setPicker] = useState<ModelPicker | null>(null);
  useEffect(() => {
    if (!installId) return;
    let alive = true;
    amApi.models(installId).then(
      (p) => alive && setPicker(p),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);
  return [picker, setPicker];
}

export function ModelControl({
  installId,
  picker,
  onPicker,
  roundChoice,
  onRoundChoice,
}: {
  installId: string;
  picker: ModelPicker | null;
  onPicker: (p: ModelPicker) => void;
  roundChoice: string | null;
  onRoundChoice: (choice: string | null) => void;
}) {
  const t = useAmT();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!picker || picker.uses === "none" || !(picker.options ?? []).length) return null;
  const options = picker.options ?? [];
  const current = roundChoice ?? picker.choice;
  const chosen = options.find((o) => o.id === current) ?? options[0]!;
  const label = picker.uses === "text" ? t("model.labelText") : t("model.label");

  const saveDefault = () => {
    setSaving(true);
    setNote(null);
    amApi.putModel(installId, current).then(
      (p) => {
        setSaving(false);
        onPicker(p);
        onRoundChoice(null);
        setNote(t("model.saved"));
      },
      (err) => {
        setSaving(false);
        setNote(errorMessage(err));
      },
    );
  };

  return (
    <div ref={ref} className="relative min-w-0">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`${label}: ${chosen.label}`}
        title={optionLine(t, chosen)}
        onClick={() => setOpen((v) => !v)}
        className="focus-ring inline-flex max-w-[16rem] items-center gap-1 truncate rounded-lg px-2 py-1 text-xs text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
        data-testid="am-model-control"
      >
        <span className="truncate">{chosen.label}</span>
        <ChevronDown width={13} height={13} aria-hidden className="shrink-0" />
      </button>
      {open ? (
        <div
          id={menuId}
          role="listbox"
          aria-label={t("model.aria")}
          className="absolute bottom-full left-0 z-30 mb-1.5 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-border bg-bg-surface py-1 shadow-lg"
          data-testid="am-model-menu"
        >
          <p className="px-3 pb-1 pt-1.5 text-[11px] font-medium text-text-muted">{label}</p>
          {options.map((o) => {
            const selected = o.id === current;
            return (
              <button
                key={o.id}
                type="button"
                role="option"
                aria-selected={selected}
                aria-disabled={o.disabled}
                disabled={o.disabled}
                title={o.disabled ? o.reason : undefined}
                onClick={() => {
                  onRoundChoice(o.id === picker.choice ? null : o.id);
                  setOpen(false);
                }}
                className={cls(
                  "focus-ring flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-bg-elevated disabled:cursor-not-allowed disabled:opacity-60",
                  selected ? "bg-bg-elevated" : "",
                )}
                data-testid="am-model-option"
                data-model={o.model}
              >
                <span className="flex items-center gap-1.5 text-sm text-text-primary">
                  {o.disabled ? <Lock width={12} height={12} aria-hidden /> : null}
                  <span>{o.label}</span>
                  {o.id === picker.choice ? <span className="text-[11px] text-text-muted">{`(${t("model.default")})`}</span> : null}
                </span>
                <span className="text-[11px] text-text-muted">{optionLine(t, o).split(" · ").slice(1).join(" · ")}</span>
                {o.disabled && o.reason ? <span className="text-[11px] text-status-warning">{o.reason}</span> : null}
              </button>
            );
          })}
          <p className="border-t border-border-subtle px-3 py-1.5 text-[11px] text-text-muted">
            {picker.documents_may_leave ? t("model.policyOutside") : t("model.policyInside")}
          </p>
        </div>
      ) : null}
      {roundChoice && roundChoice !== picker.choice ? (
        <span className="ml-1 inline-flex items-center gap-1 text-[11px] text-text-muted">
          <span>{t("model.thisRound")}</span>
          <button type="button" onClick={saveDefault} disabled={saving} className="focus-ring rounded underline underline-offset-2 hover:text-text-primary">
            {saving ? <Spin size={10} /> : t("model.saveDefault")}
          </button>
        </span>
      ) : null}
      {note ? <span className="ml-1 text-[11px] text-text-muted">{note}</span> : null}
    </div>
  );
}
