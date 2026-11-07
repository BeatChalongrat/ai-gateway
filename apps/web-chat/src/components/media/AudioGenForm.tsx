"use client";

// Direct-dispatch audio generation form (Sprint 2A.5). mode="tts" speaks
// text via a chosen voice; mode="music" generates a track from a prompt.
// Standalone — no chat messages. Submit → poll every 2s → play on done.
// Job state is LOCAL (the streaming store stays untouched).

import { useEffect, useRef, useState } from "react";

import { Download } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { downloadFromObjectUrl } from "@/lib/fileUtil";
import { tOutside, useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";
import type { MediaJobStatus, ModelEntry } from "@/lib/types";

import { mediaOutputFilename, persistMediaOutput } from "./persistOutput";

import { CostPreview } from "./CostPreview";
import { ProvenanceBadge } from "./ProvenanceBadge";
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
const FALLBACK_VOICES: { id: string; labelKey: DictKey }[] = [
  { id: "default", labelKey: "media.voice.default" },
  { id: "narrator", labelKey: "media.voice.narrator" },
];

export function AudioGenForm({ model, mode, conversationId }: Props) {
  const t = useT();
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
  // Authed playback: fetch the finished artifact through the cookie-
  // authed client and play from an object URL (mirrors AuthedImage /
  // VideoPlayer). A bare <audio src={output_url}> can't authenticate —
  // output_url is a gateway-relative path.
  const [outputUrl, setOutputUrl] = useState<string | null>(null);
  const [outputFailed, setOutputFailed] = useState(false);
  // FIX81 ③ — the finished bytes persist as a ConversationFile (once per
  // job), so the track survives "Generate another" and a reload.
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
    // The playback effect's cleanup revokes the object URL when the
    // phase leaves "done"; just drop the local references here.
    setOutputUrl(null);
    setOutputFailed(false);
    setSavedName(null);
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
          setError(j.error_message || tOutside("media.genFailed"));
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
        setError(tOutside("media.submitAudioFailed"));
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
              isTts ? t("media.tts.placeholder") : t("media.music.placeholder")
            }
            className="w-full resize-none rounded-lg border border-border bg-bg-base px-3 py-2 text-[15px] text-text-primary placeholder:text-text-placeholder focus:border-border-strong focus:outline-none"
          />
          {isTts ? (
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>{t("media.voice")}</span>
              <select
                value={voiceId}
                onChange={(e) => setVoiceId(e.target.value)}
                className="rounded-md border border-border bg-bg-base px-2 py-1 text-sm text-text-primary"
              >
                {FALLBACK_VOICES.map((v) => (
                  <option key={v.id} value={v.id}>
                    {t(v.labelKey)}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="flex items-center gap-2 text-sm text-text-secondary">
              <span>{t("media.duration")}</span>
              <input
                type="range"
                min={10}
                max={60}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
                className="accent-emerald-400"
              />
              <span className="w-10 tabular-nums text-text-primary">{t("unit.secShort", { n: String(duration) })}</span>
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
            {phase === "failed"
              ? t("common.retry")
              : isTts
                ? t("media.generateSpeech")
                : t("media.generateMusic")}
          </button>
        </>
      ) : null}

      {busy ? (
        <div className="flex items-center gap-3 text-sm text-text-secondary">
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-text-muted border-t-transparent" />
          {isTts ? t("media.generatingSpeech") : t("media.generatingMusic")}
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
          {/* Bytes come through the authed client (object URL) — never a
              bare gateway URL, which cannot authenticate. */}
          {outputUrl ? (
            <audio src={outputUrl} controls className="w-full max-w-[480px]" />
          ) : outputFailed ? (
            <p className="text-sm text-status-danger">
              {t("media.loadAudioFailed")}
            </p>
          ) : (
            <div
              className="h-12 w-full max-w-[480px] animate-pulse rounded-lg bg-bg-elevated"
              aria-label={t("media.loadingAudio")}
            />
          )}
          {outputUrl && job.provenance ? <ProvenanceBadge provenance={job.provenance} /> : null}
          {/* FIX81 ③ — the output is a FILE the user keeps, not only a
              player: a download from the loaded bytes, and a note that it
              is saved in this conversation's files (when it is). */}
          {outputUrl ? (
            <div className="flex items-center gap-3">
              <button
                type="button"
                data-testid="media-download"
                onClick={() => downloadFromObjectUrl(outputUrl, mediaOutputFilename(prompt, "audio/mpeg"))}
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
    </div>
  );
}
