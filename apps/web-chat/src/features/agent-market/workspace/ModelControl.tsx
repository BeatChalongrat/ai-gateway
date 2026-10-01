"use client";

// The agent's model — the chat's model control (components/chat/ModelSelector:
// the pill, the popover and the picker), with nothing of the agent's own
// inside the menu.
//
// AM6 — the menu is exactly the chat's: the search box, the models this
// person's chat lists, grouped by provider, "อัตโนมัติ" as the first row, a
// tick on the current one. Picking a row sets the agent's model, the way
// picking in a chat sets the conversation's. The rows of AM4 and AM5 (an
// outcome, a cost and a time, "ในองค์กร", a reason on every locked row, a
// second list behind "เลือกโมเดลเอง", a choice for one round) are gone.
//
// Two things the agent has to say are not on the rows. A model the
// organisation does not allow is drawn as the chat draws a model that cannot
// be used, and the reason is one line under the search box. A model that
// cannot do the agent's job is not listed, and the last line of its group
// says how many and why — a list that hides things without a word reads as
// a bug.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ModelPicker, ModelPopover, ModelTrigger, shortLabel } from "@/components/chat/ModelSelector";
import { toastError } from "@/components/ui/Toast";
import { useDismissable } from "@/hooks/useDismissable";
import { useT } from "@/lib/i18n";
import type { ModelEntry } from "@/lib/types";
import { AUTO_MODEL_ID, useModelStore } from "@/stores/modelStore";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { AgentEgress, AgentModels } from "../types";
import { Thai } from "../ui";

export function useAgentModels(installId: string | null): [AgentModels | null, (m: AgentModels) => void] {
  const [models, setModels] = useState<AgentModels | null>(null);
  useEffect(() => {
    if (!installId) return;
    let alive = true;
    amApi.models(installId).then(
      (m) => alive && setModels(m),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);
  return [models, setModels];
}

interface Props {
  installId: string;
  models: AgentModels | null;
  onModels: (m: AgentModels) => void;
}

/** AM9: both levels of document egress, where a person picks the agent's model — the organisation's
 *  switch (the ceiling) and the agent's own setting under it. */
export function EgressLine({ egress, className }: { egress?: AgentEgress; className?: string }) {
  const t = useAmT();
  if (!egress) return null;
  const yes = t("egress.yes");
  const no = t("egress.no");
  const agent = egress.agent ? yes : egress.agent_default && egress.accounting ? t("egress.agentDefault") : no;
  return (
    <p className={className ?? "text-[11px] leading-snug text-text-muted"} data-testid="am-model-egress">
      {t("egress.title")} · {t("egress.org", { v: egress.org ? yes : no })} · {t("egress.agent", { v: agent })}
      {egress.agent && !egress.org ? <> · {t("egress.ceiling")}</> : null}
    </p>
  );
}

export function ModelControl(props: Props) {
  const { models } = props;
  if (!models || models.uses === "none") return null;
  return (
    <Thai>
      <Control {...props} models={models} />
    </Thai>
  );
}

function Control({ installId, models, onModels }: Props & { models: AgentModels }) {
  const t = useAmT();
  const tChat = useT();
  const [open, setOpen] = useState(false);
  // the row just picked, until the server has answered
  const [picked, setPicked] = useState<string | null>(null);
  const asked = useRef(0);
  const ref = useRef<HTMLDivElement>(null);
  const chatModels = useModelStore((s) => s.models);

  const close = useCallback(() => setOpen(false), []);
  useDismissable(ref, open, close);

  // The list is the app's catalogue: load it the first time the menu opens.
  useEffect(() => {
    if (!open) return;
    const s = useModelStore.getState();
    if (!s.loaded && !s.loading) void s.loadModels();
  }, [open]);

  const allowed = useMemo(() => new Map((models.models ?? []).map((m) => [m.id, m.allowed] as const)), [models.models]);
  const current = picked ?? (models.choice || AUTO_MODEL_ID);
  const isAuto = current === AUTO_MODEL_ID;
  const name = isAuto ? tChat("model.auto.name") : (chatModels.find((m) => m.id === current)?.name ?? current);
  const label = models.uses === "text" ? t("model.labelText") : t("model.label");
  const blocked = models.blocked_reason || t("model.blocked");

  const pick = (choice: string) => {
    close();
    if (choice === current) return;
    setPicked(choice);
    // a second pick before the first is answered: the last one asked is the one shown
    const mine = ++asked.current;
    amApi.putModel(installId, choice).then(
      (next) => {
        if (mine !== asked.current) return;
        if (next) onModels(next);
        setPicked(null);
      },
      (err) => {
        if (mine !== asked.current) return;
        // nothing was saved: the pill goes back to the model the agent still uses
        setPicked(null);
        toastError(errorMessage(err));
      },
    );
  };

  return (
    <div ref={ref} className="relative">
      <ModelTrigger
        label={isAuto ? name : shortLabel(name)}
        fullLabel={name}
        auto={isAuto}
        compact
        open={open}
        onClick={() => setOpen((v) => !v)}
        ariaLabel={`${label}: ${name}`}
        testId="am-model-control"
      />
      {open ? (
        <ModelPopover label={tChat("a11y.models")} onClose={close} testId="am-model-menu">
          <ModelPicker
            models={chatModels}
            activeId={current}
            isAuto={isAuto}
            onPickAuto={() => pick(AUTO_MODEL_ID)}
            onPickModel={(m: ModelEntry) => pick(m.id)}
            needsVision={false}
            t={tChat}
            chatOnly
            gate={(m) => (allowed.get(m.id) === false ? blocked : undefined)}
            quietGate
            notice={blocked}
            hidden={(m) => !allowed.has(m.id)}
            hiddenLine={(n) => (models.hidden_reason ? t("model.hiddenWhy", { n: String(n), why: models.hidden_reason }) : t("model.hidden", { n: String(n) }))}
          />
          <EgressLine egress={models.egress} className="px-3 pb-2 pt-1 text-[11px] leading-snug text-text-muted" />
        </ModelPopover>
      ) : null}
    </div>
  );
}
