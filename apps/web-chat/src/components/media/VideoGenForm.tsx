"use client";

// Direct-dispatch video generation form (Sprint 2A.5). Standalone — does
// NOT create chat messages. Submit → poll the job every 2s → show the
// VideoGeneratingPlaceholder while running → play the result on done.
// Media-gen job state is kept LOCAL here (not in the chat streaming store)
// so the streaming path stays untouched.

import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import type { MediaJobStatus, ModelEntry } from "@/lib/types";

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

// "confirm" (Sprint 3): the job is reserved but NOT polling yet, awaiting the
// user's premium-cost confirmation (post-submit flow). "cancelled": user
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
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState(5);
  const [resolution, setResolution] = useState("1080p");
  const [hasAudio, setHasAudio] = useState(false);
  const [phase, setPhase] = useState<Phase>("form");
  const [job, setJob] = useState<MediaJobStatus | null>(null);
  const [cost, setCost] = useState<{
    estimateUsd: string;
    balanceAfter: string;
    requiresConfirmation: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<MediaGenErrorCode | null>(null);
  // Job reserved by submit() and awaiting confirm (or being polled).
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<Date | null>(null);
  const pollRef = useRef<number | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin" || user?.role === "owner";

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
          setError(j.error_message || "Generation failed.");
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
        resolution,
        has_audio: hasAudio,
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
        setError("Failed to submit video job.");
        setErrorCode("unknown");
      }
      setPhase("failed");
    }
  }

  function confirmGenerate() {
    if (!pendingJobId) return;
    setPhase("polling");
    startPolling(pendingJobId);
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
            placeholder="Describe the video you want to generate…"
            className="w-full resize-none rounded-lg border border-border bg-bg-base px-3 py-2 text-[15px] text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none"
          />
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>Duration</span>
              <input
                type="range"
                min={1}
                max={10}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
                className="accent-rose-400"
              />
              <span className="w-8 tabular-nums text-text-primary">{duration}s</span>
            </label>
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>Resolution</span>
              <select
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
                className="rounded-md border border-border bg-bg-base px-2 py-1 text-sm text-text-primary"
              >
                <option value="720p">720p</option>
                <option value="1080p">1080p</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <input
                type="checkbox"
                checked={hasAudio}
                onChange={(e) => setHasAudio(e.target.checked)}
                className="accent-rose-400"
              />
              <span>Audio</span>
            </label>
          </div>
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
            disabled={!prompt.trim()}
            className="self-end rounded-full bg-rose-500 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {phase === "failed" ? "Retry" : "Generate video"}
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
          <p className="text-sm text-text-secondary">Generation cancelled.</p>
          <button
            type="button"
            onClick={reset}
            className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            Start over
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
          {/* PROXY-PENDING: output_url is a backend URL; auth/proxy wiring
              for the direct-dispatch download lands in the E2E phase. */}
          <div className="relative w-full max-w-[480px]">
            <video
              src={job.output_url}
              controls
              preload="metadata"
              className="w-full rounded-lg border border-border bg-black"
            />
            {job.provenance ? (
              <div className="absolute bottom-2 right-2">
                <ProvenanceBadge provenance={job.provenance} />
              </div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={reset}
            className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            Generate another
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
