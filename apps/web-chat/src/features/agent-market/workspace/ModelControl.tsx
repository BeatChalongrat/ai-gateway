"use client";

// Which model reads this round's documents — the app's own model control
// (AM5: the chat's pill, popover, rows and picker from
// components/chat/ModelSelector), holding the agent's choices.
//
// "อัตโนมัติ (แนะนำ)" first; below it only rows that resolve to a DIFFERENT
// model, each saying what both an engineer and an accountant need — the
// outcome, the real model, inside or outside the organisation, a rough cost
// and time. Two rows never resolve to the same model (the server collapses
// them; this collapses again, so an older server cannot bring the defect
// back), and an organisation with one model of its own is told so in one
// line instead of a padded list. A model outside the organisation is shown
// locked with the reason until the organisation lets documents leave.
// "เลือกโมเดลเอง" opens the chat's picker — search and provider groups —
// with the same lock on every outside model. The pick applies to this round;
// "ใช้เป็นค่าตั้งต้น" keeps it for the agent.

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, Lock, SlidersHorizontal } from "lucide-react";

import { ModelPicker as ChatModelPicker, ModelPopover, ModelRow, ModelTrigger } from "@/components/chat/ModelSelector";
import { useDismissable } from "@/hooks/useDismissable";
import { useT } from "@/lib/i18n";
import type { ModelEntry as ChatModel } from "@/lib/types";
import { useModelStore } from "@/stores/modelStore";

import { amApi, errorMessage } from "../api";
import { formatDuration } from "../format";
import { useAmT } from "../i18n";
import type { ModelOption, ModelPicker } from "../types";
import { Spin, Thai } from "../ui";

/** What a row says under its name: the real model, where it runs, cost, time. */
export function optionFacts(t: ReturnType<typeof useAmT>, o: ModelOption): string {
  const parts: string[] = [];
  if (o.model && o.model !== o.label) parts.push(o.model);
  parts.push(o.self_hosted ? t("where.inOrg") : t("where.outside"));
  parts.push(o.cost_thb != null ? t("model.cost", { baht: o.cost_thb.toFixed(2) }) : t("model.costNone"));
  if (o.seconds != null && o.seconds > 0) parts.push(formatDuration(o.seconds * 1000, t));
  return parts.join(" · ");
}

export function optionLine(t: ReturnType<typeof useAmT>, o: ModelOption): string {
  return `${o.label} · ${optionFacts(t, o)}`;
}

/** One row per model: a row that resolves to a model an earlier row already
 *  is (first of all the one "อัตโนมัติ" resolves to) is not a choice. */
export function collapseOptions(options: ModelOption[]): ModelOption[] {
  const seen = new Set<string>();
  const out: ModelOption[] = [];
  for (const o of options) {
    const key = o.model || o.id;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(o);
  }
  return out;
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

export function ModelControl(props: {
  installId: string;
  picker: ModelPicker | null;
  onPicker: (p: ModelPicker) => void;
  roundChoice: string | null;
  onRoundChoice: (choice: string | null) => void;
}) {
  const { picker } = props;
  if (!picker || picker.uses === "none" || !(picker.options ?? []).length) return null;
  return (
    <Thai>
      <Control {...props} picker={picker} />
    </Thai>
  );
}

function Control({
  installId,
  picker,
  onPicker,
  roundChoice,
  onRoundChoice,
}: {
  installId: string;
  picker: ModelPicker;
  onPicker: (p: ModelPicker) => void;
  roundChoice: string | null;
  onRoundChoice: (choice: string | null) => void;
}) {
  const t = useAmT();
  const tChat = useT();
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const chatModels = useModelStore((s) => s.models);

  const close = useCallback(() => {
    setOpen(false);
    setCustom(false);
  }, []);
  useDismissable(ref, open, close);

  // "เลือกโมเดลเอง" lists the app's catalogue: load it the first time it opens.
  useEffect(() => {
    if (!custom) return;
    const s = useModelStore.getState();
    if (!s.loaded && !s.loading) void s.loadModels();
  }, [custom]);

  const options = collapseOptions(picker.options ?? []);
  const auto = options[0]!;
  // a model named outright that is the one "อัตโนมัติ" resolves to is that row
  const same = (id: string) => (id === auto.model ? auto.id : id);
  const current = same(roundChoice ?? picker.choice);
  const byHand: ModelOption | null =
    options.some((o) => o.id === current) || !current
      ? null
      : { id: current, outcome: "own", label: current, model: current, self_hosted: !!picker.custom?.find((c) => c.id === current)?.self_hosted, cost_thb: null, seconds: null, disabled: false };
  const chosen = options.find((o) => o.id === current) ?? byHand ?? auto;
  const label = picker.uses === "text" ? t("model.labelText") : t("model.label");

  const pick = (id: string) => {
    onRoundChoice(same(id) === same(picker.choice) ? null : id);
    setNote(null);
    close();
  };

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

  /** The reason a row of the chat's picker is locked here (undefined: it may be picked). */
  const gate = (m: ChatModel): string | undefined => {
    const c = (picker.custom ?? []).find((x) => x.id === m.id);
    if (!c) return picker.locked_reason || t("model.lockedUnknown");
    return c.allowed ? undefined : c.reason || t("model.lockedUnknown");
  };

  return (
    <div className="flex min-w-0 items-center gap-1">
      <div ref={ref} className="relative">
        <ModelTrigger
          label={chosen.label}
          fullLabel={optionLine(t, chosen)}
          auto={chosen.id === auto.id}
          compact
          open={open}
          onClick={() => (open ? close() : setOpen(true))}
          ariaLabel={`${label}: ${chosen.label}`}
          testId="am-model-control"
        />
        {open ? (
          <ModelPopover label={t("model.aria")} onClose={close} testId="am-model-menu">
            {custom ? (
              <>
                <button
                  type="button"
                  onClick={() => setCustom(false)}
                  className="focus-ring flex shrink-0 items-center gap-1 border-b border-border px-3 py-2 text-left text-[12px] text-text-secondary hover:text-text-primary"
                  data-testid="am-model-back"
                >
                  <ChevronLeft width={14} height={14} aria-hidden />
                  <span>{t("model.customBack")}</span>
                </button>
                <ChatModelPicker
                  models={chatModels}
                  activeId={chosen.model || null}
                  isAuto={false}
                  onPickAuto={() => pick(auto.id)}
                  onPickModel={(m) => pick(m.id)}
                  needsVision={picker.uses === "vision"}
                  t={tChat}
                  gate={gate}
                  chatOnly
                  hideAuto
                />
                <p className="shrink-0 border-t border-border px-3 py-1.5 text-[11px] text-text-muted">
                  {picker.documents_may_leave ? t("model.policyOutside") : t("model.policyInside")}
                </p>
              </>
            ) : (
              <div className="scrollbar-thin flex-1 overflow-y-auto py-1">
                <p className="px-3 pb-1 pt-1.5 text-[11px] font-medium text-text-muted">{label}</p>
                {options.map((o) => (
                  <div key={o.id} data-testid="am-model-option" data-model={o.model}>
                    <ModelRow
                      label={o.id === same(picker.choice) ? `${o.label} (${t("model.default")})` : o.label}
                      sub={optionFacts(t, o)}
                      selected={o.id === current}
                      disabled={o.disabled}
                      disabledReason={o.reason}
                      leading={o.disabled ? <Lock width={12} height={12} className="mt-1 text-text-muted" aria-hidden /> : undefined}
                      onClick={() => pick(o.id)}
                    />
                  </div>
                ))}
                {byHand ? (
                  <div data-testid="am-model-option" data-model={byHand.model}>
                    <ModelRow label={byHand.label} sub={optionFacts(t, byHand)} selected onClick={() => pick(byHand.id)} />
                  </div>
                ) : null}
                {picker.single_in_house ? (
                  <p className="px-3 py-1.5 text-[11px] text-text-secondary" data-testid="am-model-single">
                    {picker.notice || t("model.single")}
                  </p>
                ) : null}
                <div className="border-t border-border" data-testid="am-model-custom-row">
                  <button
                    type="button"
                    onClick={() => setCustom(true)}
                    className="focus-ring flex w-full items-start gap-2 px-3 py-2 text-left transition-colors hover:bg-bg-elevated/60"
                    data-testid="am-model-custom"
                  >
                    <SlidersHorizontal width={13} height={13} className="mt-1 shrink-0 text-text-muted" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium leading-snug text-text-primary">{t("model.custom")}</span>
                      <span className="block text-[11px] text-text-muted">{t("model.customHint")}</span>
                    </span>
                  </button>
                </div>
                <p className="border-t border-border px-3 py-1.5 text-[11px] text-text-muted">
                  {picker.documents_may_leave ? t("model.policyOutside") : t("model.policyInside")}
                </p>
              </div>
            )}
          </ModelPopover>
        ) : null}
      </div>
      {roundChoice && same(roundChoice) !== same(picker.choice) ? (
        <span className="hidden items-center gap-1 text-[11px] text-text-muted sm:inline-flex">
          <span className="whitespace-nowrap">{t("model.thisRound")}</span>
          <button
            type="button"
            onClick={saveDefault}
            disabled={saving}
            className="focus-ring whitespace-nowrap rounded underline underline-offset-2 hover:text-text-primary"
            data-testid="am-model-save-default"
          >
            {saving ? <Spin size={10} /> : t("model.saveDefault")}
          </button>
        </span>
      ) : null}
      {note ? <span className="hidden text-[11px] text-text-muted sm:inline">{note}</span> : null}
    </div>
  );
}
