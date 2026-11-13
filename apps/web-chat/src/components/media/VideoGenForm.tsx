"use client";

// Direct-dispatch video generation form (Sprint 2A.5). Standalone — does
// NOT create chat messages. Submit → poll the job every 2s → show the
// VideoGeneratingPlaceholder while running → play the result on done.
// Media-gen job state is kept LOCAL here (not in the chat streaming store)
// so the streaming path stays untouched.

import { useEffect, useRef, useState } from "react";

import { Download } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { downloadFromObjectUrl } from "@/lib/fileUtil";
import { tOutside, useT } from "@/lib/i18n";
import type { MediaJobStatus, ModelEntry } from "@/lib/types";

import { mediaOutputFilename, persistMediaOutput } from "./persistOutput";

import { VideoGeneratingPlaceholder } from "../chat/VideoGeneratingPlaceholder";
import { CostConfirmModal } from "./CostConfirmModal";
import { CostPreview } from "./CostPreview";
import {
  MediaGenError,
  type MediaGenErrorCode,
  mediaErrorCodeFromStatus,
} from "./MediaGenError";
import { ProvenanceBadge } from "./ProvenanceBadge";

interface Props {
  model: ModelEntry;
  conversationId?: string | null;
}

// "confirm": the server holds the job in pending_confirm; no provider dispatch
// happens until the owner approves the quote. "cancelled": user
// aborted an in-flight job.
type Phase =
  | "form"
  | "submitting"
  | "confirm"
  | "polling"
  | "done"
  | "failed"
  | "cancelled";

// Heuristic generation time for the ETA fallback when no progress % is
// available yet. Progress-based ETA overrides this once the job reports a %.
const GEN_ETA_FALLBACK_SEC = 60;

export function VideoGenForm({ model, conversationId }: Props) {
  const t = useT();
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState(5);
  const [videoConfig, setVideoConfig] = useState<{ enabled: boolean; reason: string; maxDuration: number } | null>(null);
  const [configFailed, setConfigFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api.getVideoConfig().then(config => {
      if (cancelled) return;
      const limit = config.supported_controls?.duration_sec?.[1];
      const maxDuration = Number.isInteger(limit) && limit > 0 ? Math.min(limit, 60) : 10;
      setVideoConfig({ enabled: config.enabled, reason: config.reason, maxDuration });
      setDuration(value => Math.min(value, maxDuration));
    }).catch(() => { if (!cancelled) setConfigFailed(true); });
    return () => { cancelled = true; };
  }, []);
  const [phase, setPhase] = useState<Phase>("form");
  const [job, setJob] = useState<MediaJobStatus | null>(null);
  const [cost, setCost] = useState<{
    estimateUsd: string;
    balanceAfter: string;
    requiresConfirmation: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<MediaGenErrorCode | null>(null);
  // Job created by submit(), pending confirmation or queued for execution.
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<Date | null>(null);
  // Authed playback: the finished artifact's bytes are fetched through
  // the cookie-authed client and played from an object URL (mirrors
  // AuthedImage/VideoPlayer). A bare <video src={output_url}> can't
  // authenticate — output_url is a gateway-relative path.
  const [outputUrl, setOutputUrl] = useState<string | null>(null);
  const [outputFailed, setOutputFailed] = useState(false);
  // FIX81 ③ — the finished bytes persist as a ConversationFile (once per
  // job), so the clip survives "Generate another" and a reload.
  const [savedName, setSavedName] = useState<string | null>(null);
  const savedForJob = useRef<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin" || user?.role === "owner";

  useEffect(() => {
    if (phase !== "done" || !job?.output_url) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setOutputUrl(null);
    setOutputFailed(false);
    api
      .mediaJobOutputObjectUrl(job.job_id)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        objectUrl = u;
        setOutputUrl(u);
        // Persist once per job, when the form runs inside a conversation.
        if (conversationId && job && savedForJob.current !== job.job_id) {
          savedForJob.current = job.job_id;
          void persistMediaOutput(conversationId, u, prompt, model.id).then((row) => {
            if (!cancelled && row) setSavedName(row.filename);
          });
        }
      })
      .catch(() => {
        if (!cancelled) setOutputFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [phase, job?.job_id, job?.output_url]);

  function stopPolling() {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }
  useEffect(() => stopPolling, []);

  function reset() {
    stopPolling();
    setPhase("form");
    setJob(null);
    setError(null);
    setErrorCode(null);
    setCost(null);
    setPendingJobId(null);
    setStartedAt(null);
    // The playback effect's cleanup revokes the object URL when the
    // phase leaves "done"; just drop the local references here.
    setOutputUrl(null);
    setOutputFailed(false);
    setSavedName(null);
  }

  function startPolling(jobId: string) {
    stopPolling();
    setStartedAt(new Date());
    pollRef.current = window.setInterval(async () => {
      try {
        const j = await api.getMediaJob(jobId);
        setJob(j);
        if (j.status === "done") {
          stopPolling();
          setPhase("done");
        } else if (j.status === "failed") {
          stopPolling();
          setError(j.error_message || tOutside("media.genFailed"));
          setPhase("failed");
        } else if (j.status === "cancelled") {
          stopPolling();
          setPhase("cancelled");
        }
      } catch {
        /* transient poll error — keep polling */
      }
    }, 2000);
  }

  async function submit() {
    if (!prompt.trim() || phase === "submitting" || phase === "polling") return;
    setPhase("submitting");
    setError(null);
    setErrorCode(null);
    try {
      const res = await api.submitVideoGen({
        prompt: prompt.trim(),
        model: model.id,
        duration_sec: duration,
        resolution: "auto",
        has_audio: false,
        conversation_id: conversationId ?? null,
      });
      setCost({
        estimateUsd: res.estimated_cost_usd,
        balanceAfter: res.balance_after_usd,
        requiresConfirmation: res.requires_confirmation,
      });
      setPendingJobId(res.job_id);
      // Sprint 3: premium jobs (server-side $1 threshold → requires_confirmation)
      // pause here for explicit confirm; cheap jobs poll immediately.
      if (res.requires_confirmation) {
        setPhase("confirm");
      } else {
        setPhase("polling");
        startPolling(res.job_id);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        setErrorCode(mediaErrorCodeFromStatus(err.status, err.code));
      } else {
        setError(tOutside("media.submitVideoFailed"));
        setErrorCode("unknown");
      }
      setPhase("failed");
    }
  }

  async function confirmGenerate() {
    if (!pendingJobId) return;
    setPhase("submitting");
    try {
      const confirmed = await api.confirmMediaJob(pendingJobId);
      if (confirmed.status !== "queued" && confirmed.status !== "processing" && confirmed.status !== "done") throw new Error("Video job is no longer available for generation.");
      setPhase("polling");
      startPolling(pendingJobId);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("media.submitVideoFailed"));
      setErrorCode("unknown");
      setPhase("failed");
    }
  }

  async function cancelGenerate() {
    const id = pendingJobId;
    reset();
    if (id) {
      try {
        await api.cancelMediaJob(id);
      } catch {
        /* best-effort abort — the reservation expires server-side regardless */
      }
    }
  }

  // Cancel an actively-polling job: stop polling, show the cancelled state,
  // and abort server-side (best-effort).
  async function cancelInProgress() {
    const id = pendingJobId;
    stopPolling();
    setPhase("cancelled");
    if (id) {
      try {
        await api.cancelMediaJob(id);
      } catch {
        /* best-effort */
      }
    }
  }

  const busy = phase === "submitting" || phase === "polling";

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-border bg-bg-surface p-4">
      {phase === "form" || phase === "failed" ? (
        <>
          <textarea
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("media.video.placeholder")}
            className="w-full resize-none rounded-lg border border-border bg-bg-base px-3 py-2 text-[15px] text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none"
          />
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>{t("media.duration")}</span>
              <input
                type="range"
                min={1}
                max={videoConfig?.maxDuration ?? 10}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
                className="accent-rose-400"
              />
              <span className="w-8 tabular-nums text-text-primary">{t("unit.secShort", { n: String(duration) })}</span>
            </label>
            <p className="text-sm text-text-secondary">{t("media.videoControlsNote")}</p>
          </div>
          {!videoConfig?.enabled && <p role="status" className="text-sm text-text-secondary">{videoConfig?.reason || t(configFailed ? "media.videoUnavailable" : "media.videoChecking")}</p>}
          {phase === "failed" && errorCode ? (
            <MediaGenError
              errorCode={errorCode}
              errorMessage={error ?? ""}
              isAdmin={isAdmin}
              onRetry={submit}
              onDismiss={() => {
                setError(null);
                setErrorCode(null);
                setPhase("form");
              }}
            />
          ) : error ? (
            <p className="text-sm text-status-danger">{error}</p>
          ) : null}
          <button
            type="button"
            onClick={submit}
            disabled={!prompt.trim() || !videoConfig?.enabled}
            className="self-end rounded-full bg-rose-500 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {phase === "failed" ? t("common.retry") : t("media.generateVideo")}
          </button>
        </>
      ) : null}

      {busy ? (
        <div className="flex flex-col items-center gap-3">
          <VideoGeneratingPlaceholder
            progressPct={job?.progress_pct ?? null}
            startedAt={startedAt ?? undefined}
            estimatedDurationSec={GEN_ETA_FALLBACK_SEC}
            onCancel={cancelInProgress}
          />
        </div>
      ) : null}

      {phase === "cancelled" ? (
        <div className="flex flex-col items-center gap-3 py-2">
          <p className="text-sm text-text-secondary">{t("media.cancelled")}</p>
          <button
            type="button"
            onClick={reset}
            className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            {t("media.startOver")}
          </button>
        </div>
      ) : null}

      {cost ? (
        <CostPreview
          estimateUsd={cost.estimateUsd}
          balanceAfter={cost.balanceAfter}
          requiresConfirmation={cost.requiresConfirmation}
        />
      ) : null}

      {phase === "done" && job?.output_url ? (
        <div className="flex flex-col items-center gap-3">
          {/* Bytes come through the authed client (object URL) — never a
              bare gateway URL, which cannot authenticate. */}
          <div className="relative w-full max-w-[480px]">
            {outputUrl ? (
              <video
                src={outputUrl}
                controls
                preload="metadata"
                className="w-full rounded-lg border border-border bg-black"
              />
            ) : outputFailed ? (
              <p className="py-4 text-center text-sm text-status-danger">
                {t("media.loadVideoFailed")}
              </p>
            ) : (
              <div
                className="aspect-video w-full animate-pulse rounded-lg bg-bg-elevated"
                aria-label={t("media.loadingVideo")}
              />
            )}
            {outputUrl && job.provenance ? (
              <div className="absolute bottom-2 right-2">
                <ProvenanceBadge provenance={job.provenance} />
              </div>
            ) : null}
          </div>
          {/* FIX81 ③ — the output is a FILE the user keeps: download from
              the loaded bytes + a note that it is saved in this
              conversation's files (when it is). */}
          {outputUrl ? (
            <div className="flex items-center gap-3">
              <button
                type="button"
                data-testid="media-download"
                onClick={() => downloadFromObjectUrl(outputUrl, mediaOutputFilename(prompt, "video/mp4"))}
                className="flex items-center gap-1.5 rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
              >
                <Download className="h-3.5 w-3.5" aria-hidden />
                {t("common.download")}
              </button>
              {savedName ? (
                <span className="text-xs text-text-muted" data-testid="media-saved-note">
                  {t("media.savedToFiles", { name: savedName })}
                </span>
              ) : null}
            </div>
          ) : null}
          <button
            type="button"
            onClick={reset}
            className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            {t("media.generateAnother")}
          </button>
        </div>
      ) : null}

      <CostConfirmModal
        open={phase === "confirm"}
        estimatedUsd={cost?.estimateUsd ?? "—"}
        balanceAfterUsd={cost?.balanceAfter}
        onConfirm={confirmGenerate}
        onCancel={cancelGenerate}
      />
    </div>
  );
}
