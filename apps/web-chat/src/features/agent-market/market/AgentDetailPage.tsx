"use client";

// /chat/agents/[slug] — an agent's page, written for someone who has never
// seen it (AM3). In order: who it is for; how using it goes; which files it
// needs and what a good one looks like, with the real sample files to
// download; what it produces, with the real outputs it made from them;
// roughly what a run costs and how long it takes; which models see your
// documents and whether each runs inside the organisation (next to the
// install button, before it is pressed); what it will never do; what it asks
// and what happens with a wrong file; and the data sheet.

import { useEffect, useState } from "react";
import { CircleCheck, CircleX, Download, FileText, ShieldCheck, TriangleAlert } from "lucide-react";

import { ButtonLink } from "@/components/ui/Button";
import { FileTypeBadge } from "@/components/ui/FileTypeBadge";
import { Spinner } from "@/components/ui/Spinner";
import { formatBytes } from "@/lib/fileUtil";

import { AmError, amApi, errorMessage } from "../api";
import { useAmT, type AmKey } from "../i18n";
import { extensionOf } from "../format";
import type { AmFile, Artifact, CatalogItem, InstallRow, Manifest } from "../types";
import { QuickInstall } from "../install/QuickInstall";
import { AgentTile, BackLink, Card, CheckLine, ErrorPanel, FeatureGate, PageShell, PageSkeleton, Pill } from "../ui";
import { useAgentMarket } from "../useAgentMarket";
import { runHref, workspaceHref } from "../workspace/context";
import { formatsLabel } from "./MarketPage";
import { CostLines, ModelList, ScopeLine, useDatasheet, useSample } from "./trust";

const DENY_KEYS = new Set(["email_send", "accounting_write", "external_share", "payment", "computer_use", "schedule"]);

export function AgentDetailPage({ slug }: { slug: string }) {
  return (
    <FeatureGate>
      <Detail slug={slug} />
    </FeatureGate>
  );
}

const base = (p: string) => p.split("/").pop() ?? p;

/** The sample files a role's guide names, by file name. */
export function sampleFilesFor(files: AmFile[], names: string[] | undefined): AmFile[] {
  if (!names?.length) return [];
  const want = new Set(names.map(base));
  return files.filter((f) => want.has(f.name));
}

function Detail({ slug }: { slug: string }) {
  const t = useAmT();
  const snap = useAgentMarket();
  const [item, setItem] = useState<CatalogItem | null>(null);
  const [installed, setInstalled] = useState<InstallRow | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const { sheet } = useDatasheet(slug);
  const { sample } = useSample(slug);

  useEffect(() => {
    let alive = true;
    setError(null);
    amApi.catalogItem(slug).then(
      (r) => alive && setItem(r),
      (err) => alive && setError(err),
    );
    amApi.installs().then(
      (r) => alive && setInstalled((r?.installs ?? []).find((i) => i.slug === slug) ?? null),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [slug, reload]);

  if (error) {
    const missing = error instanceof AmError && error.status === 404;
    return (
      <PageShell width="max-w-3xl">
        <BackLink href="/chat/agents">{t("detail.back")}</BackLink>
        <div className="mt-6">
          <ErrorPanel title={missing ? t("detail.notFound") : undefined} message={errorMessage(error)} onRetry={missing ? undefined : () => setReload((n) => n + 1)} />
        </div>
      </PageShell>
    );
  }
  if (!item) return <PageSkeleton />;

  const m: Manifest = item.manifest ?? {};
  const g = m.guide ?? {};
  const status = item.status ?? "published";
  const disabled = status === "disabled";
  const unpublished = status === "unpublished";
  const formats = formatsLabel({ slug: item.slug, version: item.version, name: item.name, manifest: m });
  const sampleFiles = sample?.files ?? [];
  const sampleArts = sample?.artifacts ?? [];
  const usedSamples = new Set<string>();

  return (
    <PageShell width="max-w-6xl">
      <BackLink href="/chat/agents">{t("detail.back")}</BackLink>

      <div className="mt-5 flex items-start gap-4">
        <AgentTile icon={m.icon} size={48} />
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">{item.name}</h1>
          <p className="mt-0.5 text-xs text-text-muted">
            {t("detail.version", { v: item.version })}
            {formats ? ` · ${formats}` : null}
          </p>
          <ScopeLine scope={item.scope ?? m.scope} jurisdiction={item.jurisdiction ?? m.jurisdiction} className="mt-0.5" />
          {m.summary ? <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary">{m.summary}</p> : null}
        </div>
      </div>

      {disabled ? (
        <div role="alert" className="mt-4 flex items-start gap-2.5 rounded-xl border border-status-warning/40 bg-status-warning/10 px-4 py-3 text-sm text-text-primary" data-testid="am-detail-disabled">
          <TriangleAlert width={17} height={17} className="mt-0.5 shrink-0 text-status-warning" aria-hidden />
          <span>{item.status_reason ? t("guide.disabledReason", { reason: item.status_reason }) : t("guide.disabled")}</span>
        </div>
      ) : null}
      {unpublished ? <UnpublishedBanner /> : null}

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-4 lg:order-1">
          {g.for ? (
            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("guide.for")}</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-text-secondary">{g.for}</p>
            </Card>
          ) : null}

          {g.how?.length ? (
            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("guide.how")}</h2>
              <ol className="mt-2.5 flex flex-col gap-2" data-testid="am-guide-how">
                {g.how.map((s, i) => (
                  <li key={`${i}-${s}`} className="flex items-start gap-2.5 text-sm text-text-secondary">
                    <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border text-[11px] tabular-nums text-text-primary">
                      {i + 1}
                    </span>
                    <span className="min-w-0">{s}</span>
                  </li>
                ))}
              </ol>
            </Card>
          ) : null}

          <Card>
            <h2 className="text-sm font-semibold text-text-primary">{t("guide.needs")}</h2>
            <ul className="mt-2.5 flex flex-col gap-4" data-testid="am-guide-inputs">
              {(m.inputs ?? []).map((i) => {
                const gi = g.inputs?.[i.role];
                const files = sampleFilesFor(sampleFiles, gi?.sample);
                files.forEach((f) => usedSamples.add(f.id));
                return (
                  <li key={i.role} className="text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-text-primary">{i.label}</span>
                      <Pill tone={i.required ? "warning" : "neutral"}>{i.required ? t("common.required") : t("common.optional")}</Pill>
                    </div>
                    {i.note ? <div className="mt-0.5 text-xs text-text-secondary">{i.note}</div> : null}
                    {i.accept?.length ? <div className="mt-0.5 text-[11px] text-text-muted">{t("detail.accept", { types: i.accept.join(" ") })}</div> : null}
                    {gi?.good?.length ? (
                      <div className="mt-2">
                        <div className="text-xs font-medium text-text-primary">{t("guide.good")}</div>
                        <ul className="mt-1 flex flex-col gap-1">
                          {gi.good.map((x) => (
                            <li key={x} className="flex items-start gap-1.5 text-xs text-text-secondary">
                              <CircleCheck width={13} height={13} className="mt-px shrink-0 text-status-success" aria-hidden />
                              <span>{x}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {gi?.avoid?.length ? (
                      <div className="mt-2">
                        <div className="text-xs font-medium text-text-primary">{t("guide.avoid")}</div>
                        <ul className="mt-1 flex flex-col gap-1">
                          {gi.avoid.map((x) => (
                            <li key={x} className="flex items-start gap-1.5 text-xs text-text-secondary">
                              <CircleX width={13} height={13} className="mt-px shrink-0 text-status-warning" aria-hidden />
                              <span>{x}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {files.length ? (
                      <div className="mt-2">
                        <div className="text-xs text-text-muted">{t("guide.sampleInputs")}</div>
                        <ul className="mt-1 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle">
                          {files.map((f) => (
                            <SampleFileRow key={f.id} file={f} />
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            {sampleFiles.some((f) => !usedSamples.has(f.id)) ? (
              <div className="mt-4">
                <div className="text-xs text-text-muted">{t("guide.sampleInputsAll")}</div>
                <ul className="mt-1 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle" data-testid="am-sample-inputs">
                  {sampleFiles
                    .filter((f) => !usedSamples.has(f.id))
                    .map((f) => (
                      <SampleFileRow key={f.id} file={f} />
                    ))}
                </ul>
              </div>
            ) : null}
          </Card>

          <Card>
            <h2 className="text-sm font-semibold text-text-primary">{t("guide.produces")}</h2>
            <ul className="mt-2.5 flex flex-col gap-3" data-testid="am-guide-outputs">
              {(m.outputs ?? []).map((o) => (
                <li key={o.format} className="text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-text-primary">{o.label}</span>
                    {o.default ? <Pill>{t("detail.defaultOutput")}</Pill> : null}
                  </div>
                  {g.outputs?.[o.format] ? <p className="mt-0.5 text-xs text-text-secondary">{g.outputs[o.format]}</p> : null}
                </li>
              ))}
            </ul>
            {sampleArts.length ? (
              <div className="mt-4">
                <div className="text-xs text-text-muted">{t("guide.sampleOutputs")}</div>
                <ul className="mt-1 flex flex-col divide-y divide-border-subtle rounded-lg border border-border-subtle" data-testid="am-sample-outputs">
                  {sampleArts.map((a) => (
                    <SampleArtifactRow key={a.id} artifact={a} />
                  ))}
                </ul>
              </div>
            ) : (
              <p className="mt-3 text-xs text-text-muted">{sample === null ? t("guide.sampleLoading") : t("guide.sampleNone")}</p>
            )}
          </Card>

          <Card>
            <div className="flex items-center gap-2">
              <ShieldCheck width={16} height={16} className="text-status-success" aria-hidden />
              <h2 className="text-sm font-semibold text-text-primary">{t("guide.never")}</h2>
            </div>
            <ul className="mt-2.5 flex flex-col gap-1.5" data-testid="am-guide-never">
              <CheckLine>{t("detail.readOnly")}</CheckLine>
              {(g.never ?? []).map((n) => (
                <CheckLine key={n} ok={false}>
                  {n}
                </CheckLine>
              ))}
              {(m.permissions?.denied ?? [])
                .filter((d) => DENY_KEYS.has(d))
                .map((d) => (
                  <CheckLine key={d} ok={false}>
                    {t(`deny.${d}` as AmKey)}
                  </CheckLine>
                ))}
            </ul>
            {m.cannot ? <p className="mt-3 text-sm text-text-primary">{m.cannot}</p> : null}
          </Card>

          {g.questions || g.wrong_file ? (
            <Card>
              <h2 className="text-sm font-semibold text-text-primary">{t("guide.asks")}</h2>
              {g.questions ? <p className="mt-1.5 text-sm leading-relaxed text-text-secondary">{g.questions}</p> : null}
              {g.wrong_file ? (
                <>
                  <h3 className="mt-3 text-xs font-medium text-text-primary">{t("guide.wrongFile")}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-text-secondary">{g.wrong_file}</p>
                </>
              ) : null}
            </Card>
          ) : null}
        </div>

        {/* Before you install: who sees your documents, what it costs — next to the button. */}
        <aside className="flex min-w-0 flex-col gap-4 lg:order-2" aria-label={t("guide.beforeInstall")}>
          <div className="rounded-xl border border-border-strong bg-bg-surface px-4 py-4 lg:sticky lg:top-4" data-testid="am-before-install">
            <h2 className="text-sm font-semibold text-text-primary">{t("guide.whoSees")}</h2>
            <p className="mt-0.5 text-xs text-text-muted">{t("guide.whoSeesBody")}</p>
            <div className="mt-3">
              {sheet ? (
                <ModelList models={sheet.models ?? []} noModel={sheet.no_model ?? []} compact />
              ) : (
                <div className="flex items-center gap-2 text-xs text-text-muted">
                  <Spinner size={12} />
                  <span>{t("common.loading")}</span>
                </div>
              )}
            </div>
            <h2 className="mt-5 text-sm font-semibold text-text-primary">{t("guide.cost")}</h2>
            <div className="mt-1.5">
              <CostLines manifest={m} sample={item.sample ?? sample?.sample} stats={item.stats} pricing={snap.pricing} />
            </div>
            <div className="mt-5 flex flex-col gap-2">
              {installed ? (
                <ButtonLink href={workspaceHref(installed.id)}>
                  {t("market.open")}
                </ButtonLink>
              ) : disabled ? (
                <p className="text-xs text-text-secondary">{t("guide.cannotInstallDisabled")}</p>
              ) : (
                <QuickInstall slug={item.slug} />
              )}
              <ButtonLink href={`/chat/agents/${encodeURIComponent(item.slug)}/datasheet`} variant="secondary" data-testid="am-datasheet-link">
                <FileText width={15} height={15} aria-hidden />
                <span>{t("guide.datasheet")}</span>
              </ButtonLink>
              {installed?.self_test_run_id ? (
                <ButtonLink href={runHref(installed.self_test_run_id)} variant="secondary" size="sm">
                  {t("detail.sampleOpen")}
                </ButtonLink>
              ) : null}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-text-muted">{t("guide.installNote")}</p>
          </div>
        </aside>
      </div>
    </PageShell>
  );
}

// AM7: an unpublished agent is shown to the people who try agents before they
// are published; publishing it is done in the admin app, not here.
function UnpublishedBanner() {
  const t = useAmT();
  return (
    <div className="mt-4 rounded-xl border border-border bg-bg-elevated px-4 py-3 text-sm text-text-primary" data-testid="am-detail-unpublished">
      <p>{t("guide.unpublished")}</p>
    </div>
  );
}

function SampleFileRow({ file }: { file: AmFile }) {
  const t = useAmT();
  return <DownloadRow name={file.name} size={file.size} ext={file.name.slice(file.name.lastIndexOf(".")).toLowerCase()} label={t("guide.sampleInput")} download={() => amApi.downloadSampleFile(file.id, file.name)} />;
}

function SampleArtifactRow({ artifact }: { artifact: Artifact }) {
  const t = useAmT();
  return (
    <DownloadRow
      name={artifact.name}
      size={artifact.size}
      ext={extensionOf(artifact.format)}
      label={t("guide.sampleOutput")}
      download={() => amApi.downloadSampleArtifact(artifact.id, artifact.name)}
    />
  );
}

export function DownloadRow({ name, size, ext, label, download }: { name: string; size: number; ext: string; label: string; download: () => Promise<void> }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <li className="flex items-center gap-3 px-3 py-2" data-testid="am-download-row">
      <FileTypeBadge extension={ext} filename={name} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-text-primary" title={name}>
          {name}
        </div>
        <div className="text-[11px] text-text-muted">{`${label} · ${formatBytes(size)}`}</div>
        {error ? (
          <div role="alert" className="text-[11px] text-status-danger">
            {error}
          </div>
        ) : null}
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setError(null);
          download().then(
            () => setBusy(false),
            (err) => {
              setBusy(false);
              setError(errorMessage(err));
            },
          );
        }}
        aria-label={t("common.downloadNamed", { name })}
        className="focus-ring tap-44 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-text-secondary hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
      >
        {busy ? <Spinner size={15} /> : <Download width={16} height={16} aria-hidden />}
      </button>
    </li>
  );
}
