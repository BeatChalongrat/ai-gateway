"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useT, tOutside } from "@/lib/i18n";
import type { MediaJobStatus } from "@/lib/types";
import { persistMediaOutput } from "./persistOutput";

export type ConversationVideoJob = MediaJobStatus & { model: string; estimated_cost_usd: string };

export function VideoJobsPanel({ conversationId }: { conversationId: string }) {
  const t = useT();
  const [jobs, setJobs] = useState<ConversationVideoJob[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setJobs([]); setError("");
    async function poll() {
      try {
        const result = await api.listVideoJobs(conversationId);
        if (active) { setJobs(result.jobs); setError(""); }
      } catch { if (active) setError(tOutside("videoJobs.unavailable")); }
      finally { if (active) timer = setTimeout(poll, 10000); }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [conversationId]);
  if (!jobs.length) return null;
  return <details className="mx-3 my-1 rounded border border-border-subtle bg-bg-base p-2 text-xs text-text-primary">
    <summary>{t("videoJobs.title")} ({jobs.length})</summary>
    {error && <p role="alert">{error}</p>}
    <div className="max-h-72 space-y-3 overflow-auto">{jobs.map(job => <VideoJobRow key={`${conversationId}:${job.job_id}`} job={job} conversationId={conversationId} onUpdated={updated => setJobs(old => old.map(value => value.job_id === updated.job_id ? { ...value, ...updated } : value))} />)}</div>
  </details>;
}

function VideoJobRow({ job, conversationId, onUpdated }: { job: ConversationVideoJob; conversationId: string; onUpdated: (job: MediaJobStatus) => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [url, setUrl] = useState<string>();
  const [saved, setSaved] = useState(false);
  const [playing, setPlaying] = useState(false);
  const cancellable = ["pending_confirm", "queued", "processing"].includes(job.status);
  useEffect(() => {
    let active = true; let objectUrl: string | undefined;
    setUrl(undefined);
    if (playing && job.status === "done") api.mediaJobOutputObjectUrl(job.job_id).then(value => { objectUrl = value; if (active) setUrl(value); else URL.revokeObjectURL(value); }).catch(() => { if (active) setError(tOutside("videoJobs.unavailable")); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [job.job_id, job.status, playing]);
  async function act(action: "confirm" | "cancel" | "save") {
    setBusy(true); setError("");
    try {
      if (action === "save" && url) {
        const result = await persistMediaOutput(conversationId, url, `video-${job.job_id}`, job.model);
        if (!result) throw new Error(t("videoJobs.unavailable")); setSaved(true);
      } else if (action === "confirm") onUpdated(await api.confirmMediaJob(job.job_id));
      else if (action === "cancel") onUpdated(await api.cancelMediaJob(job.job_id));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <article className="space-y-2 border-t border-border-subtle pt-2">
    <p>{job.model} · {job.status} {job.progress_pct != null ? `${job.progress_pct}%` : ""}</p>
    <p>{t("videoJobs.estimate")} ${job.estimated_cost_usd} · <span className="select-all">{job.job_id}</span></p>
    {job.status === "pending_confirm" && <button disabled={busy} onClick={() => void act("confirm")} className="mr-3 rounded border border-border-subtle p-1">{t("videoJobs.confirm")}</button>}
    {cancellable && <button disabled={busy} onClick={() => void act("cancel")}>{t("videoJobs.cancel")}</button>}
    {job.status === "done" && <button disabled={busy} onClick={() => setPlaying(value => !value)}>{t(playing ? "videoJobs.hide" : "videoJobs.play")}</button>}
    {url && <><video controls preload="metadata" src={url} className="max-h-40 max-w-full" /><a href={url} download={`video-${job.job_id}.mp4`}>{t("preview.download")}</a><button disabled={busy || saved} className="ml-3" onClick={() => void act("save")}>{t(saved ? "videoJobs.saved" : "videoJobs.save")}</button></>}
    {(error || job.error_message) && <p role="alert">{error || job.error_message}</p>}
  </article>;
}
