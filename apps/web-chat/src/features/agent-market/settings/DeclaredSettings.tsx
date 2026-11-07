"use client";

// AM6 — "ค่าที่ใช้ตรวจ": the settings an agent's manifest declares
// (install.settings), rendered from what the server sends and nothing the
// page knows about the agent — generic by default. A choice is radios, a
// number a text field with its range under it, a text a text area; the
// setting's help sits under its field. The server checks every value by the
// declared kind and bounds; its sentence is shown at the field.

import { useId, useState } from "react";

import { Button } from "@/components/ui/Button";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import type { DeclaredSetting } from "../types";
import { cls } from "../ui";

const NUMBER_KINDS = new Set(["money", "percent", "int", "ratio"]);
/** Settings the agent asks about itself, once, in its first round, while they are not set. */
const ASKED_ONCE = new Set(["ap_gr_practice"]);
const NUMBER = /^-?\d+(\.\d+)?$/;
const bound = new Intl.NumberFormat("th-TH", { maximumFractionDigits: 6 });

export function DeclaredSection({ installId, settings, onChanged }: { installId: string; settings: DeclaredSetting[]; onChanged: () => void }) {
  const t = useAmT();
  if (!settings.length) return null;
  return (
    <section className="mt-8" aria-labelledby="am-declared-title" data-testid="am-declared">
      <h2 id="am-declared-title" className="text-sm font-semibold text-text-primary">
        {t("declared.title")}
      </h2>
      <p className="mt-0.5 text-xs text-text-muted">{t("declared.body")}</p>
      <div className="mt-2.5 flex flex-col gap-2">
        {settings.map((s) => (
          // a saved value comes back from the server: the row starts again from it
          <DeclaredRow key={`${s.key}:${JSON.stringify(s.value)}:${s.set}`} installId={installId} setting={s} onChanged={onChanged} />
        ))}
      </div>
    </section>
  );
}

function initialText(s: DeclaredSetting): string {
  if (!s.set || s.value === null || s.value === undefined) return "";
  return typeof s.value === "string" || typeof s.value === "number" ? String(s.value) : JSON.stringify(s.value);
}

function DeclaredRow({ installId, setting: s, onChanged }: { installId: string; setting: DeclaredSetting; onChanged: () => void }) {
  const t = useAmT();
  const id = useId();
  const initial = initialText(s);
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const numeric = NUMBER_KINDS.has(s.kind);
  const dirty = text !== initial;

  const save = async () => {
    const value = numeric ? text.replace(/[,\s]/g, "") : s.kind === "text" ? text.trim() : text;
    if (numeric && !NUMBER.test(value)) {
      setError(t("declared.notNumber"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // an emptied text un-sets the setting
      if (s.kind === "text" && !value) await amApi.deleteDefault(installId, s.key);
      else await amApi.putDefault(installId, s.key, value);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const hint =
    numeric && typeof s.min === "number" && typeof s.max === "number"
      ? t("declared.range", { min: bound.format(s.min), max: bound.format(s.max) })
      : s.kind === "text" && s.max_length
        ? t("declared.maxLength", { n: bound.format(s.max_length) })
        : "";
  const field = "focus-ring w-full rounded-lg border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary";
  const describedBy = [s.help ? `${id}-help` : "", hint ? `${id}-hint` : ""].filter(Boolean).join(" ") || undefined;

  return (
    <form
      className="rounded-xl border border-border bg-bg-surface px-4 py-3"
      data-testid="am-declared-row"
      data-key={s.key}
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy && dirty) void save();
      }}
    >
      {s.kind === "choice" ? (
        <fieldset aria-describedby={describedBy}>
          <legend className="text-sm font-medium text-text-primary">{s.label}</legend>
          <State s={s} />
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {(s.choices ?? []).map((c) => (
              <label
                key={c.value}
                className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-border bg-bg-base px-3 py-2 text-sm text-text-primary hover:bg-bg-elevated"
              >
                <input type="radio" name={`${id}-choice`} value={c.value} checked={text === c.value} onChange={() => setText(c.value)} disabled={busy} className="h-4 w-4 accent-current" />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : (
        <>
          <label htmlFor={`${id}-field`} className="block text-sm font-medium text-text-primary">
            {s.label}
          </label>
          <State s={s} />
          {s.kind === "text" ? (
            <textarea
              id={`${id}-field`}
              value={text}
              rows={3}
              maxLength={s.max_length ?? undefined}
              onChange={(e) => setText(e.target.value)}
              aria-describedby={describedBy}
              disabled={busy}
              className={cls(field, "mt-2 resize-y")}
            />
          ) : (
            <input
              id={`${id}-field`}
              type="text"
              value={text}
              inputMode={s.kind === "int" ? "numeric" : "decimal"}
              onChange={(e) => setText(e.target.value)}
              aria-describedby={describedBy}
              disabled={busy}
              className={cls(field, "mt-2 tabular-nums sm:w-56")}
            />
          )}
        </>
      )}
      {hint ? (
        <p id={`${id}-hint`} className="mt-1 text-[11px] text-text-muted" data-testid="am-declared-hint">
          {hint}
        </p>
      ) : null}
      {s.help ? (
        <p id={`${id}-help`} className="mt-1 text-xs text-text-secondary" data-testid="am-declared-help">
          {s.help}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1.5 text-xs text-status-danger">
          {error}
        </p>
      ) : null}
      <div className="mt-2.5">
        <Button type="submit" size="sm" loading={busy} disabled={!dirty}>
          <span>{busy ? t("common.saving") : t("common.save")}</span>
        </Button>
      </div>
    </form>
  );
}

/** "ยังไม่ได้ตั้ง" for a setting that is not set (and that the agent asks once, when it does). */
function State({ s }: { s: DeclaredSetting }) {
  const t = useAmT();
  if (s.set) return null;
  return (
    <p className="mt-0.5 text-xs text-text-muted" data-testid="am-declared-state">
      {t("declared.unset")}
      {ASKED_ONCE.has(s.key) ? ` · ${t("declared.askOnce")}` : ""}
    </p>
  );
}
