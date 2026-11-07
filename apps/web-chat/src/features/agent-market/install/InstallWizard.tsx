"use client";

// /chat/agents/install/[slug] — the install wizard (board 2).
//
// The install row is created the moment the wizard opens (self_test:false),
// under an idempotency key kept in sessionStorage for this wizard: a reload,
// a double mount or a second tab of the same wizard lands on the SAME install
// instead of creating another. Every later step writes install defaults
// (PUT /installs/{id}/defaults/{key}); the last one runs the self-test on the
// bundled sample data and shows its real files. Which steps there are comes
// from the manifest's install.questions (./wizard.ts): the financial report
// keeps its five, the invoice agent asks company details instead of a source.

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Check } from "lucide-react";

import { Button, ButtonLink } from "@/components/ui/Button";

import { AmError, amApi, errorMessage, newClientId } from "../api";
import { useAmT } from "../i18n";
import type { CatalogItem, InstallResponse, InstallRow, Manifest } from "../types";
import { BackLink, Card, cls, ErrorPanel, FeatureGate, PageShell, PageSkeleton } from "../ui";
import { notifyInstallsChanged } from "../useAgentMarket";
import { workspaceHref } from "../workspace/context";
import { PolicyStep } from "../policy/PolicySetup";
import { AsksPanel, PreviewPanel } from "./panels";
import { CompanyTaxStep, FormatsStep, MappingStep, SelfTestStep, SourceStep, StepWhy, TemplateStep, ThemeStep, type PreviewState } from "./steps";
import { installSteps, STEP_LABEL, themeAsksCompany, type StepId } from "./wizard";

// The most steps any agent has (the stored step is clamped to the agent's own list).
const MAX_STEPS = 8;

// One POST per idempotency key per page, even under StrictMode's double
// mount (two concurrent inserts of one key would race the unique index).
const inflight = new Map<string, Promise<InstallResponse>>();

function session(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

const keyName = (slug: string) => `am.install.${slug}.key`;
const stepName = (slug: string) => `am.install.${slug}.step`;

function readKey(slug: string): string | null {
  try {
    return session()?.getItem(keyName(slug)) ?? null;
  } catch {
    return null;
  }
}

function ensureKey(slug: string): string {
  const existing = readKey(slug);
  if (existing) return existing;
  const k = newClientId();
  try {
    session()?.setItem(keyName(slug), k);
  } catch {
    /* no storage: the key lives for this page only */
  }
  return k;
}

function forgetWizard(slug: string) {
  try {
    session()?.removeItem(keyName(slug));
    session()?.removeItem(stepName(slug));
  } catch {
    /* ignore */
  }
}

export function InstallWizard({ slug }: { slug: string }) {
  return (
    <FeatureGate>
      <Wizard slug={slug} />
    </FeatureGate>
  );
}

type Boot =
  | { phase: "loading" }
  | { phase: "already"; existing: InstallRow }
  | { phase: "installing" }
  | { phase: "ready"; installId: string }
  | { phase: "error"; error: unknown };

function Wizard({ slug }: { slug: string }) {
  const t = useAmT();
  const router = useRouter();
  const [item, setItem] = useState<CatalogItem | null>(null);
  const [boot, setBoot] = useState<Boot>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [again, setAgain] = useState(false);
  const [step, setStepState] = useState(() => {
    try {
      const n = Number(session()?.getItem(stepName(slug)));
      return Number.isInteger(n) && n >= 0 && n < MAX_STEPS ? n : 0;
    } catch {
      return 0;
    }
  });
  const [preview, setPreview] = useState<PreviewState>({ status: "none" });

  const setStep = useCallback(
    (n: number) => {
      setStepState(n);
      try {
        session()?.setItem(stepName(slug), String(n));
      } catch {
        /* ignore */
      }
    },
    [slug],
  );

  // 1 — the agent, and whether it is already installed.
  useEffect(() => {
    let alive = true;
    setBoot({ phase: "loading" });
    Promise.all([amApi.catalogItem(slug), amApi.installs().catch(() => ({ installs: [] as InstallRow[] }))]).then(
      ([it, list]) => {
        if (!alive) return;
        setItem(it);
        const existing = (list?.installs ?? []).find((i) => i.slug === slug);
        // An install from THIS wizard (key in session) is resumed, not warned about.
        if (existing && !readKey(slug) && !again) setBoot({ phase: "already", existing });
        else setBoot({ phase: "installing" });
      },
      (err) => alive && setBoot({ phase: "error", error: err }),
    );
    return () => {
      alive = false;
    };
  }, [slug, attempt, again]);

  // 2 — create (or find again) the install row.
  useEffect(() => {
    if (boot.phase !== "installing") return;
    let alive = true;
    const key = ensureKey(slug);
    let p = inflight.get(key);
    if (!p) {
      p = amApi.install({ slug, idempotency_key: key, self_test: false });
      inflight.set(key, p);
      p.catch(() => inflight.delete(key));
    }
    p.then(
      (res) => {
        if (!alive) return;
        if (res.created) notifyInstallsChanged();
        setBoot({ phase: "ready", installId: res.install.id });
      },
      (err) => alive && setBoot({ phase: "error", error: err }),
    );
    return () => {
      alive = false;
    };
  }, [boot.phase, slug]);

  const manifest: Manifest = item?.manifest ?? {};
  const name = item?.name ?? "";
  const steps = installSteps(manifest);
  const at = Math.min(step, steps.length - 1);
  const current = steps[at]!;
  const indexOf = (id: StepId) => steps.indexOf(id);

  if (boot.phase === "loading" && !item) return <PageSkeleton />;

  if (boot.phase === "error") {
    const err = boot.error;
    return (
      <PageShell width="max-w-3xl">
        <BackLink href="/chat/agents">{t("detail.back")}</BackLink>
        <div className="mt-6">
          <ErrorPanel
            title={t("install.failed")}
            message={errorMessage(err)}
            onRetry={err instanceof AmError && err.status === 404 ? undefined : () => setAttempt((n) => n + 1)}
          />
        </div>
      </PageShell>
    );
  }

  if (boot.phase === "already") {
    return (
      <PageShell width="max-w-xl">
        <BackLink href="/chat/agents">{t("detail.back")}</BackLink>
        <Card className="mt-6">
          <h1 className="text-base font-semibold text-text-primary">{t("install.already.title")}</h1>
          <p className="mt-1 text-sm text-text-secondary">{t("install.already.body")}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <ButtonLink href={workspaceHref(boot.existing.id)}>
              {t("market.open")}
            </ButtonLink>
            <Button type="button" variant="secondary" onClick={() => setAgain(true)}>
              {t("install.already.again")}
            </Button>
          </div>
        </Card>
      </PageShell>
    );
  }

  const installId = boot.phase === "ready" ? boot.installId : null;
  const finish = (href?: string) => {
    forgetWizard(slug);
    notifyInstallsChanged();
    if (installId) router.push(href ?? workspaceHref(installId));
  };

  return (
    <PageShell width="max-w-6xl">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <BackLink href={`/chat/agents/${encodeURIComponent(slug)}`}>{t("common.back")}</BackLink>
        <h1 className="min-w-0 flex-1 truncate text-lg font-semibold text-text-primary sm:text-xl">{t("install.title", { name })}</h1>
        {item ? <span className="text-xs text-text-muted">{t("install.meta", { v: item.version })}</span> : null}
      </div>

      <Stepper labels={steps.map((s) => t(STEP_LABEL[s]))} step={at} onJump={(n) => n < at && setStep(n)} />

      {!installId ? (
        <div role="status" className="mt-6 flex items-center gap-2 text-sm text-text-secondary">
          <span className="skeleton h-4 w-4 rounded-full" aria-hidden />
          <span>{t("install.preparing")}</span>
        </div>
      ) : (
        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card className="min-w-0 px-4 py-5 sm:px-5">
            <StepWhy step={current} />
            {current === "source" ? (
              <SourceStep
                installId={installId}
                preview={preview}
                setPreview={setPreview}
                onNext={() => setStep(indexOf("mapping"))}
                onSkip={() => setStep(indexOf("mapping") + 1)}
              />
            ) : current === "mapping" ? (
              <MappingStep installId={installId} preview={preview} onBack={() => setStep(at - 1)} onNext={() => setStep(at + 1)} />
            ) : current === "company_tax" ? (
              <CompanyTaxStep installId={installId} onNext={() => setStep(at + 1)} />
            ) : current === "policy" ? (
              <PolicyStep installId={installId} manifest={manifest} onBack={at > 0 ? () => setStep(at - 1) : undefined} onNext={() => setStep(at + 1)} />
            ) : current === "theme" ? (
              <ThemeStep installId={installId} askCompany={themeAsksCompany(manifest)} onBack={() => setStep(at - 1)} onNext={() => setStep(at + 1)} />
            ) : current === "formats" ? (
              <FormatsStep installId={installId} manifest={manifest} onBack={() => setStep(at - 1)} onNext={() => setStep(at + 1)} />
            ) : current === "template" ? (
              <TemplateStep installId={installId} manifest={manifest} onBack={() => setStep(at - 1)} onNext={() => setStep(at + 1)} />
            ) : (
              <SelfTestStep installId={installId} manifest={manifest} onBack={() => setStep(at - 1)} onFinish={finish} />
            )}
          </Card>
          <div className="flex min-w-0 flex-col gap-4">
            {current === "source" || current === "mapping" ? <PreviewPanel preview={preview} /> : null}
            <AsksPanel manifest={manifest} />
          </div>
        </div>
      )}
    </PageShell>
  );
}

function Stepper({ labels, step, onJump }: { labels: string[]; step: number; onJump: (n: number) => void }) {
  const t = useAmT();
  return (
    <nav aria-label={t("install.steps")} className="mt-5">
      <p className="text-xs text-text-muted sm:hidden">{`${t("install.stepOf", { n: String(step + 1), total: String(labels.length) })} · ${labels[step]}`}</p>
      <ol className="mt-2 flex items-center gap-2 sm:mt-0">
        {labels.map((label, i) => {
          const done = i < step;
          const current = i === step;
          return (
            <li key={label} className={cls("flex min-w-0 items-center gap-2", i < labels.length - 1 ? "flex-1" : "")}>
              <button
                type="button"
                disabled={!done}
                onClick={() => onJump(i)}
                aria-current={current ? "step" : undefined}
                aria-label={label}
                className={cls(
                  "focus-ring flex min-w-0 items-center gap-2 rounded-md text-sm disabled:cursor-default",
                  current ? "font-medium text-text-primary" : done ? "text-text-secondary hover:text-text-primary" : "text-text-muted",
                )}
              >
                <span
                  className={cls(
                    "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] tabular-nums",
                    done ? "border-status-success/50 bg-status-success/10 text-status-success" : current ? "border-text-primary" : "border-border",
                  )}
                  aria-hidden
                >
                  {done ? <Check width={13} height={13} strokeWidth={2.5} /> : i + 1}
                </span>
                {/* every label from lg; only the current one from sm; none on a phone (the line above says it) */}
                <span className={cls("truncate", current ? "hidden sm:inline" : "hidden lg:inline")} aria-hidden>
                  {label}
                </span>
              </button>
              {i < labels.length - 1 ? <span className="h-px min-w-3 flex-1 bg-border" aria-hidden /> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
