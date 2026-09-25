"use client";

// /chat/agents/i/[installId]/settings — "ค่าที่จำไว้และการตั้งค่า".
// Remembered answers (what the agent will not ask again) and the settings
// chosen at install, each clearable; the simple ones editable in place.
// Uninstall confirms inline — never a window.confirm.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Pencil } from "lucide-react";

import { AmError, amApi, errorMessage } from "../api";
import { useAmLocale, useAmT, type AmKey, type AmT } from "../i18n";
import { formatCredits, formatDateTime, formatMoney } from "../format";
import type { Default, InstallDetail, Manifest, ThemePreset } from "../types";
import { BackLink, BTN_DANGER_OUTLINE, BTN_PRIMARY_SMALL, BTN_SECONDARY, BTN_SMALL, cls, ErrorPanel, FeatureGate, PageShell, PageSkeleton, Pill, Spin } from "../ui";
import { notifyInstallsChanged } from "../useAgentMarket";
import { workspaceHref } from "../workspace/context";
import { CURRENCIES } from "../install/steps";

const INSTALL_KEYS = ["company", "formats", "currency", "materiality", "theme", "language", "fiscal_year_start"];
const REMEMBERED_KEYS = new Set(["mapping", "dc_convention"]);
const FIELD_KEYS = new Set(["date", "account_code", "account_name", "debit", "credit", "amount", "dc_indicator", "cost_center", "description"]);

export function isRemembered(d: Default): boolean {
  return d.source === "answer" || d.key.startsWith("answers.") || REMEMBERED_KEYS.has(d.key);
}

/** A default's value in words: mapping → "field ← header" lines, arrays joined, objects as k: v. */
export function describeValue(d: Default, t: AmT, manifest?: Manifest): string[] {
  const v = d.value;
  if (v === null || v === undefined || v === "") return [];
  if (d.key === "mapping" && typeof v === "object" && !Array.isArray(v)) {
    return Object.entries(v as Record<string, unknown>).map(([field, header]) =>
      t("settings.mappingPair", { field: FIELD_KEYS.has(field) ? t(`field.${field}` as AmKey) : field, header: String(header) }),
    );
  }
  // A remembered answer's label already says the answer in words; its raw
  // value (an option id such as "exclude") would only add noise.
  if (d.key.startsWith("answers.")) return [];
  if (d.key === "dc_convention" && typeof v === "object" && !Array.isArray(v)) {
    return Object.entries(v as Record<string, unknown>).map(([column, conv]) => `${column}: ${describeConvention(conv, t)}`);
  }
  if (d.key === "formats" && Array.isArray(v)) {
    const labels = new Map((manifest?.outputs ?? []).map((o) => [o.format, o.label] as const));
    return [v.map((f) => labels.get(String(f)) ?? String(f)).join(", ")];
  }
  if (d.key === "materiality") return [formatMoney(v)];
  if (d.key === "language") return [v === "th" ? t("lang.th") : v === "en" ? t("lang.en") : String(v)];
  if (Array.isArray(v)) return [v.map(String).join(", ")];
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (d.key === "theme" && typeof o.id === "string") return [o.id];
    return Object.entries(o).map(([k, x]) => `${k}: ${typeof x === "object" ? JSON.stringify(x) : String(x)}`);
  }
  return [String(v)];
}

/** {"D":"debit","C":"credit"} → "D = เดบิต, C = เครดิต"; "positive_debit" → "บวก = เดบิต". */
function describeConvention(conv: unknown, t: AmT): string {
  const side = (s: unknown) => (s === "debit" ? t("field.debit") : s === "credit" ? t("field.credit") : String(s));
  if (typeof conv === "string") {
    if (conv === "positive_debit") return t("settings.positiveIs", { side: t("field.debit") });
    if (conv === "positive_credit") return t("settings.positiveIs", { side: t("field.credit") });
    return conv;
  }
  if (conv && typeof conv === "object") {
    return Object.entries(conv as Record<string, unknown>)
      .map(([k, s]) => `${k} = ${side(s)}`)
      .join(", ");
  }
  return String(conv);
}

export function SettingsPage({ installId }: { installId: string }) {
  return (
    <FeatureGate>
      <Settings installId={installId} />
    </FeatureGate>
  );
}

function Settings({ installId }: { installId: string }) {
  const t = useAmT();
  const router = useRouter();
  const [detail, setDetail] = useState<InstallDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const [presets, setPresets] = useState<ThemePreset[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    amApi.getInstall(installId).then(
      (d) => alive && setDetail(d),
      (err) => alive && setError(err),
    );
    amApi.theme().then(
      (r) => alive && setPresets(r?.presets ?? []),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId, reload]);

  const refresh = () => setReload((n) => n + 1);

  if (error) {
    return (
      <PageShell width="max-w-3xl">
        <BackLink href={workspaceHref(installId)}>{t("settings.back")}</BackLink>
        <div className="mt-6">
          <ErrorPanel
            title={error instanceof AmError && error.status === 404 ? t("ws.notFound.title") : undefined}
            message={errorMessage(error)}
            onRetry={refresh}
          />
        </div>
      </PageShell>
    );
  }
  if (!detail) return <PageSkeleton />;

  const defaults = detail.defaults ?? [];
  const remembered = defaults.filter(isRemembered);
  const settings = defaults
    .filter((d) => !isRemembered(d))
    .sort((a, b) => {
      const ia = INSTALL_KEYS.indexOf(a.key);
      const ib = INSTALL_KEYS.indexOf(b.key);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });

  const uninstall = async () => {
    setRemoving(true);
    setRemoveError(null);
    try {
      await amApi.removeInstall(installId);
      notifyInstallsChanged();
      router.push("/chat/agents");
    } catch (err) {
      setRemoving(false);
      setRemoveError(errorMessage(err));
    }
  };

  return (
    <PageShell width="max-w-3xl">
      <BackLink href={workspaceHref(installId)}>{t("settings.back")}</BackLink>
      <h1 className="mt-4 text-xl font-semibold tracking-tight text-text-primary">{t("settings.title")}</h1>
      <p className="mt-1 text-sm text-text-secondary">{detail.install.title}</p>
      <p className="mt-3 text-sm text-text-secondary" data-testid="am-budget">
        {t("settings.budget", { spent: formatCredits(detail.budget?.spent_this_month ?? 0), credits: formatCredits(detail.budget?.credits ?? 0) })}
      </p>

      <section className="mt-6" aria-labelledby="am-remembered">
        <h2 id="am-remembered" className="text-sm font-semibold text-text-primary">
          {t("settings.remembered")}
        </h2>
        <p className="mt-0.5 text-xs text-text-muted">{t("settings.rememberedHint")}</p>
        <div className="mt-2.5 flex flex-col gap-2">
          {remembered.length ? (
            remembered.map((d) => <DefaultRow key={d.key} d={d} installId={installId} manifest={detail.manifest} presets={presets} onChanged={refresh} />)
          ) : (
            <p className="rounded-xl border border-border-subtle px-4 py-3 text-sm text-text-muted">{t("settings.none")}</p>
          )}
        </div>
      </section>

      <section className="mt-8" aria-labelledby="am-install-settings">
        <h2 id="am-install-settings" className="text-sm font-semibold text-text-primary">
          {t("settings.installTime")}
        </h2>
        <div className="mt-2.5 flex flex-col gap-2">
          {settings.length ? (
            settings.map((d) => <DefaultRow key={d.key} d={d} installId={installId} manifest={detail.manifest} presets={presets} onChanged={refresh} />)
          ) : (
            <p className="rounded-xl border border-border-subtle px-4 py-3 text-sm text-text-muted">{t("settings.noneInstall")}</p>
          )}
        </div>
      </section>

      <section className="mt-8 rounded-xl border border-status-danger/40 bg-bg-surface px-4 py-4" aria-labelledby="am-danger">
        <h2 id="am-danger" className="text-sm font-semibold text-text-primary">
          {t("settings.dangerTitle")}
        </h2>
        <p className="mt-1 text-sm text-text-secondary">{t("settings.dangerBody")}</p>
        {removeError ? (
          <div className="mt-3">
            <ErrorPanel message={removeError} />
          </div>
        ) : null}
        {confirming ? (
          <div className="mt-3 rounded-lg border border-status-danger/40 bg-status-danger/10 px-3 py-3" role="alertdialog" aria-labelledby="am-uninstall-q">
            <p id="am-uninstall-q" className="text-sm text-text-primary">
              {t("settings.uninstallConfirm")}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void uninstall()}
                disabled={removing}
                className="focus-ring inline-flex items-center gap-2 rounded-lg bg-status-danger-solid px-3.5 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                data-testid="am-uninstall-yes"
              >
                {removing ? <Spin size={14} /> : null}
                <span>{removing ? t("settings.uninstalling") : t("settings.uninstallYes")}</span>
              </button>
              <button type="button" onClick={() => setConfirming(false)} disabled={removing} className={BTN_SECONDARY}>
                {t("common.cancel")}
              </button>
            </div>
          </div>
        ) : (
          <button type="button" onClick={() => setConfirming(true)} className={cls(BTN_DANGER_OUTLINE, "mt-3")} data-testid="am-uninstall">
            {t("settings.uninstall")}
          </button>
        )}
      </section>
    </PageShell>
  );
}

type Editor = "text" | "number" | "currency" | "formats" | "theme" | "language" | null;

function editorFor(key: string): Editor {
  switch (key) {
    case "company":
      return "text";
    case "materiality":
      return "number";
    case "currency":
      return "currency";
    case "formats":
      return "formats";
    case "theme":
      return "theme";
    case "language":
      return "language";
  }
  return null;
}

const SOURCE_KEYS: Record<string, AmKey> = {
  install: "settings.source.install",
  answer: "settings.source.answer",
  settings: "settings.source.settings",
};

function DefaultRow({
  d,
  installId,
  manifest,
  presets,
  onChanged,
}: {
  d: Default;
  installId: string;
  manifest: Manifest;
  presets: ThemePreset[];
  onChanged: () => void;
}) {
  const t = useAmT();
  const locale = useAmLocale();
  const editor = editorFor(d.key);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lines = describeValue(d, t, manifest);
  const label = d.label || d.key;

  const clear = async () => {
    setBusy(true);
    setError(null);
    try {
      await amApi.deleteDefault(installId, d.key);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  const save = async (value: unknown) => {
    setBusy(true);
    setError(null);
    try {
      if (d.key === "theme") {
        const id = (value as { id: string }).id;
        await amApi.putTheme({ id });
      }
      await amApi.putDefault(installId, d.key, value);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-border bg-bg-surface px-4 py-3" data-testid="am-default-row" data-key={d.key}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-text-primary">{label}</div>
          {!editing ? (
            lines.length ? (
              <ul className="mt-1 flex flex-col gap-0.5 text-sm text-text-secondary">
                {lines.map((l, i) => (
                  <li key={i} className="break-words">
                    {l}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-text-muted">{t("settings.empty")}</p>
            )
          ) : (
            <EditValue d={d} editor={editor!} manifest={manifest} presets={presets} busy={busy} onSave={save} onCancel={() => setEditing(false)} />
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-text-muted">
            {/* the server's note already says where a value came from; the
                source badge stands in only when there is no note */}
            {d.note ? <span>{d.note}</span> : SOURCE_KEYS[d.source] ? <Pill>{t(SOURCE_KEYS[d.source]!)}</Pill> : null}
            {d.updated_at ? <span>{t("settings.updated", { when: formatDateTime(d.updated_at, locale) })}</span> : null}
          </div>
          {error ? (
            <p role="alert" className="mt-1.5 text-xs text-status-danger">
              {error}
            </p>
          ) : null}
        </div>
        {!editing ? (
          <div className="flex shrink-0 gap-1.5">
            {editor ? (
              <button type="button" onClick={() => setEditing(true)} disabled={busy} className={BTN_SMALL} aria-label={t("settings.editAria", { label })}>
                <Pencil width={13} height={13} aria-hidden />
              </button>
            ) : null}
            <button type="button" onClick={() => void clear()} disabled={busy} className={BTN_SMALL} aria-label={t("settings.clearAria", { label })} data-testid="am-default-clear">
              {busy ? <Spin size={12} /> : null}
              <span aria-hidden>{t("settings.clear")}</span>
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function EditValue({
  d,
  editor,
  manifest,
  presets,
  busy,
  onSave,
  onCancel,
}: {
  d: Default;
  editor: Exclude<Editor, null>;
  manifest: Manifest;
  presets: ThemePreset[];
  busy: boolean;
  onSave: (value: unknown) => void;
  onCancel: () => void;
}) {
  const t = useAmT();
  const initial = d.value;
  const [text, setText] = useState(() => (typeof initial === "string" || typeof initial === "number" ? String(initial) : ""));
  const [formats, setFormats] = useState<string[]>(() => (Array.isArray(initial) ? initial.map(String) : []));
  const [theme, setTheme] = useState(() => (initial && typeof initial === "object" && "id" in (initial as object) ? String((initial as { id: unknown }).id) : "navy"));
  const numberOk = editor !== "number" || /^\d+(\.\d+)?$/.test(text.trim());
  const valid = editor === "formats" ? formats.length > 0 : editor === "theme" ? !!theme : text.trim().length > 0 && numberOk;

  const value = () => {
    switch (editor) {
      case "formats":
        return (manifest.outputs ?? []).map((o) => o.format).filter((f) => formats.includes(f));
      case "theme":
        return { id: theme };
      default:
        return text.trim();
    }
  };

  const field = "focus-ring w-full rounded-lg border border-border bg-bg-base px-3 py-1.5 text-sm text-text-primary";
  return (
    <form
      className="mt-2 flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !busy) onSave(value());
      }}
    >
      {editor === "text" || editor === "number" ? (
        <input
          autoFocus
          value={text}
          inputMode={editor === "number" ? "decimal" : undefined}
          onChange={(e) => setText(editor === "number" ? e.target.value.replace(/[,\s]/g, "") : e.target.value)}
          aria-label={d.label || d.key}
          className={field}
        />
      ) : editor === "currency" ? (
        <select value={text || "THB"} onChange={(e) => setText(e.target.value)} aria-label={d.label || d.key} className={field}>
          {CURRENCIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : editor === "language" ? (
        <select value={text || "th"} onChange={(e) => setText(e.target.value)} aria-label={d.label || d.key} className={field}>
          <option value="th">{t("lang.th")}</option>
          <option value="en">{t("lang.en")}</option>
        </select>
      ) : editor === "theme" ? (
        <select value={theme} onChange={(e) => setTheme(e.target.value)} aria-label={d.label || d.key} className={field}>
          {(presets.length ? presets : [{ id: theme, name: theme }]).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      ) : (
        <div className="flex flex-wrap gap-2">
          {(manifest.outputs ?? []).map((o) => (
            <label key={o.format} className="flex items-center gap-2 rounded-lg border border-border bg-bg-base px-2.5 py-1.5 text-sm text-text-primary">
              <input
                type="checkbox"
                checked={formats.includes(o.format)}
                onChange={() => setFormats((cur) => (cur.includes(o.format) ? cur.filter((x) => x !== o.format) : [...cur, o.format]))}
                className="h-4 w-4 accent-current"
              />
              <span>{o.label}</span>
            </label>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={!valid || busy} className={BTN_PRIMARY_SMALL}>
          {busy ? <Spin size={12} /> : null}
          <span>{busy ? t("common.saving") : t("common.save")}</span>
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className={BTN_SMALL}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}
