"use client";

// VoiceSettingsModal — input method, language, output voice, speed,
// auto-read. Accessed from the sidebar footer's "Voice" button.
//
// The store is the single source of truth; this component is a
// thin form binding. Saving is implicit — every change writes
// through to localStorage via the store setters.

import { useEffect, useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { useVoiceOutput } from "@/hooks/useVoiceOutput";
import { OPENAI_VOICES, useVoiceStore, type OpenAIVoice } from "@/stores/voiceStore";

interface Props {
  open: boolean;
  onClose: () => void;
}

const INPUT_LANGS: { value: string; label: string }[] = [
  { value: "auto", label: "Auto-detect (browser locale)" },
  { value: "en-US", label: "English (US)" },
  { value: "en-GB", label: "English (UK)" },
  { value: "th-TH", label: "Thai" },
  { value: "ja-JP", label: "Japanese" },
  { value: "zh-CN", label: "Chinese (Mandarin)" },
  { value: "es-ES", label: "Spanish" },
  { value: "fr-FR", label: "French" },
  { value: "de-DE", label: "German" },
];

export function VoiceSettingsModal({ open, onClose }: Props) {
  const inputMethod = useVoiceStore((s) => s.inputMethod);
  const inputLang = useVoiceStore((s) => s.inputLang);
  const outputMode = useVoiceStore((s) => s.outputMode);
  const browserVoiceName = useVoiceStore((s) => s.browserVoiceName);
  const openaiVoice = useVoiceStore((s) => s.openaiVoice);
  const rate = useVoiceStore((s) => s.rate);
  const autoRead = useVoiceStore((s) => s.autoRead);
  const setInputMethod = useVoiceStore((s) => s.setInputMethod);
  const setInputLang = useVoiceStore((s) => s.setInputLang);
  const setOutputMode = useVoiceStore((s) => s.setOutputMode);
  const setBrowserVoice = useVoiceStore((s) => s.setBrowserVoice);
  const setOpenAIVoice = useVoiceStore((s) => s.setOpenAIVoice);
  const setRate = useVoiceStore((s) => s.setRate);
  const setAutoRead = useVoiceStore((s) => s.setAutoRead);

  const { voices, speak, stop, speakingMessageId } = useVoiceOutput();
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    if (!open) setPreviewing(false);
  }, [open]);

  function preview() {
    if (previewing && speakingMessageId === "__voice_preview__") {
      stop();
      setPreviewing(false);
      return;
    }
    setPreviewing(true);
    void speak(
      "This is a preview of the current voice settings. Adjust the voice and speed sliders to taste.",
      "__voice_preview__",
    );
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        stop();
        onClose();
      }}
      title="Voice settings"
      description="Speech-to-text and text-to-speech"
      maxWidth={520}
      footer={
        <Button onClick={onClose}>Done</Button>
      }
    >
      <div className="flex flex-col gap-5">
        <Section title="Voice input">
          <Field label="Method">
            <SegmentedToggle
              value={inputMethod}
              onChange={(v) => setInputMethod(v as typeof inputMethod)}
              options={[
                {
                  value: "web-speech",
                  label: "Browser",
                  hint: "Free, real-time. Chrome / Edge / Safari.",
                },
                {
                  value: "whisper",
                  label: "Whisper",
                  hint: "Higher accuracy. Requires server config.",
                },
              ]}
            />
          </Field>
          <Field label="Language">
            <select
              value={inputLang}
              onChange={(e) => setInputLang(e.target.value)}
              className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-sm text-text-primary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
            >
              {INPUT_LANGS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </Field>
        </Section>

        <Section title="Voice output">
          <Field label="Source">
            <SegmentedToggle
              value={outputMode}
              onChange={(v) => setOutputMode(v as typeof outputMode)}
              options={[
                {
                  value: "browser",
                  label: "Browser",
                  hint: "System voices, no round-trip.",
                },
                {
                  value: "openai",
                  label: "OpenAI TTS",
                  hint: "Premium voices. Requires server config.",
                },
              ]}
            />
          </Field>
          {outputMode === "browser" ? (
            <Field label="Voice">
              <select
                value={browserVoiceName ?? ""}
                onChange={(e) =>
                  setBrowserVoice(e.target.value || null)
                }
                className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-sm text-text-primary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              >
                <option value="">System default (English)</option>
                {voices.map((v) => (
                  <option key={`${v.name}-${v.lang}`} value={v.name}>
                    {v.name} — {v.lang}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <Field label="Voice">
              <select
                value={openaiVoice}
                onChange={(e) => setOpenAIVoice(e.target.value as OpenAIVoice)}
                className="rounded-md border border-border bg-bg-secondary px-3 py-2 text-sm text-text-primary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              >
                {OPENAI_VOICES.map((v) => (
                  <option key={v} value={v}>
                    {v.charAt(0).toUpperCase() + v.slice(1)}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field label={`Speed (${rate.toFixed(1)}×)`}>
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.1}
              value={rate}
              onChange={(e) => setRate(Number(e.target.value))}
              className="w-full accent-[var(--accent)]"
            />
          </Field>
          <div className="pt-1">
            <Button variant="secondary" onClick={preview}>
              {previewing && speakingMessageId === "__voice_preview__"
                ? "Stop preview"
                : "Preview voice"}
            </Button>
          </div>
        </Section>

        <Section title="Behaviour">
          <ToggleRow
            checked={autoRead}
            onChange={setAutoRead}
            label="Auto-read AI responses"
            hint="Speak each completed reply out loud automatically."
          />
        </Section>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Small primitives
// ---------------------------------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-[11px] font-medium uppercase tracking-wider text-text-muted">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs text-text-secondary">{label}</label>
      {children}
    </div>
  );
}

function SegmentedToggle<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; hint: string }[];
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={`rounded-md border px-3 py-2 text-left transition-colors ${
              active
                ? "border-accent bg-accent/10 text-text-primary"
                : "border-border bg-bg-secondary text-text-secondary hover:border-border-strong hover:text-text-primary"
            }`}
          >
            <span className="block text-sm font-medium">{o.label}</span>
            <span className="mt-0.5 block text-[11px] text-text-muted">
              {o.hint}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ToggleRow({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex items-start justify-between gap-3 rounded-md border border-border bg-bg-secondary px-3 py-2.5 text-left transition-colors hover:bg-bg-elevated"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-text-primary">{label}</span>
        <span className="mt-0.5 block text-[11px] text-text-muted">{hint}</span>
      </span>
      <span
        aria-hidden
        className={`mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
          checked ? "bg-accent" : "bg-border"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${
            checked ? "translate-x-4" : "translate-x-0.5"
          }`}
        />
      </span>
    </button>
  );
}
