"use client";

// Direct-dispatch audio generation form (Sprint 2A.5). mode="tts" speaks
// text via a chosen voice; mode="music" generates a track from a prompt.
// Standalone — no chat messages. Submit → poll every 2s → play on done.
// Job state is LOCAL (the streaming store stays untouched).

import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import type { MediaJobStatus, ModelEntry } from "@/lib/types";

import { CostPreview } from "./CostPreview";
import {
  MediaGenError,
  type MediaGenErrorCode,
  mediaErrorCodeFromStatus,
} from "./MediaGenError";

interface Props {
  model: ModelEntry;
  mode: "tts" | "music";
  conversationId?: string | null;
}

type Phase = "form" | "submitting" | "polling" | "done" | "failed";

// Placeholder voices until model capability_metadata exposes a real list.
const FALLBACK_VOICES = [
  { id: "default", label: "Default" },
  { id: "narrator", label: "Narrator" },
];

export function AudioGenForm({ model, mode, conversationId }: Props) {
  const [prompt, setPrompt] = useState("");
  const [voiceId, setVoiceId] = useState(FALLBACK_VOICES[0].id);
  const [duration, setDuration] = useState(30);
  const [phase, setPhase] = useState<Phase>("form");
  const [job, setJob] = useState<MediaJobStatus | null>(null);
  const [cost, setCost] = useState<{
    estimateUsd: string;
    balanceAfter: string;
    requiresConfirmation: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<MediaGenErrorCode | null>(null);
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
  }

  function startPolling(jobId: string) {
    stopPolling();
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
        }
      } catch {
        /* transient — keep polling */
      }
    }, 2000);
  }

  async function submit() {
    if (!prompt.trim() || phase === "submitting" || phase === "polling") return;
    setPhase("submitting");
    setError(null);
    setErrorCode(null);
    try {
      const res = await api.submitAudioGen({
        prompt: prompt.trim(),
        model: model.id,
        mode,
        voice_id: mode === "tts" ? voiceId : undefined,
        duration_sec: mode === "music" ? duration : undefined,
        conversation_id: conversationId ?? null,
      });
      setCost({
        estimateUsd: res.estimated_cost_usd,
        balanceAfter: res.balance_after_usd,
        requiresConfirmation: res.requires_confirmation,
      });
      setPhase("polling");
      startPolling(res.job_id);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        setErrorCode(mediaErrorCodeFromStatus(err.status, err.code));
      } else {
        setError("Failed to submit audio job.");
        setErrorCode("unknown");
      }
      setPhase("failed");
    }
  }

  const busy = phase === "submitting" || phase === "polling";
  const isTts = mode === "tts";

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-border bg-bg-surface p-4">
      {phase === "form" || phase === "failed" ? (
        <>
          <textarea
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={
              isTts ? "Text to speak…" : "Describe the music to generate…"
            }
            className="w-full resize-none rounded-lg border border-border bg-bg-base px-3 py-2 text-[15px] text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none"
          />
          {isTts ? (
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>Voice</span>
              <select
                value={voiceId}
                onChange={(e) => setVoiceId(e.target.value)}
                className="rounded-md border border-border bg-bg-base px-2 py-1 text-sm text-text-primary"
              >
                {FALLBACK_VOICES.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>Duration</span>
              <input
                type="range"
                min={10}
                max={60}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
                className="accent-emerald-400"
              />
              <span className="w-10 tabular-nums text-text-primary">{duration}s</span>
            </label>
          )}
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
            className="self-end rounded-full bg-emerald-500 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {phase === "failed" ? "Retry" : isTts ? "Generate speech" : "Generate music"}
          </button>
        </>
      ) : null}

      {busy ? (
        <div className="flex items-center gap-3 text-sm text-text-secondary">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
          {isTts ? "Generating speech…" : "Generating music…"}
          {job?.progress_pct != null ? <span>{job.progress_pct}%</span> : null}
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
        <div className="flex flex-col items-start gap-3">
          {/* PROXY-PENDING: output_url is a backend URL; auth/proxy wiring
              for the direct-dispatch download lands in the E2E phase. */}
          <audio src={job.output_url} controls className="w-full max-w-[480px]" />
          <button
            type="button"
            onClick={reset}
            className="rounded-full border border-border px-4 py-1.5 text-sm text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
          >
            Generate another
          </button>
        </div>
      ) : null}
    </div>
  );
}
