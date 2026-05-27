"use client";

// VoiceSettingsModal — input method, language, output voice, speed,
// auto-read. Accessed from the sidebar footer's Account menu.
//
// The store is the single source of truth; this component is a
// thin form binding. Every change writes through to localStorage
// via the store setters — there is no explicit "save" step. The
// Done button just closes the modal.
//
// Design language (matches CustomInstructionsModal):
//
//   Modal:           bg-bg-surface  rounded-2xl  p-6  shadow-2xl
//   Section card:    bg-bg-base     rounded-xl   p-5
//   Section title:   text-xs uppercase tracking-widest text-text-muted
//   Selection card:  2-up grid, 2px text-primary border + ✓ on the
//                    active option (1px → 2px shift compensated with
//                    asymmetric padding so the grid doesn't reflow)
//   Toggle switch:   44×24, white track when on, gray when off,
//                    knob colour inverts so it stays visible.

import { useEffect, useState } from "react";

import { Modal } from "@/components/ui/Modal";
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
      description="Configure speech-to-text and text-to-speech"
      maxWidth={520}
      footer={<DoneButton onClick={onClose} />}
    >
      <div className="flex flex-col gap-4">
        <SettingsCard title="Voice input">
          <FieldLabel>Method</FieldLabel>
          <SelectionGrid
            value={inputMethod}
            onChange={(v) => setInputMethod(v as typeof inputMethod)}
            options={[
              {
                value: "web-speech",
                label: "Browser",
                hint: "Free, real-time. Works in Chrome, Edge, Safari.",
              },
              {
                value: "whisper",
                label: "Whisper",
                hint: "Higher accuracy. Requires server key.",
              },
            ]}
          />
          <FieldLabel className="mt-4">Language</FieldLabel>
          <Dropdown
            value={inputLang}
            onChange={(v) => setInputLang(v)}
            options={INPUT_LANGS}
          />
        </SettingsCard>

        <SettingsCard title="Voice output">
          <FieldLabel>Source</FieldLabel>
          <SelectionGrid
            value={outputMode}
            onChange={(v) => setOutputMode(v as typeof outputMode)}
            options={[
              {
                value: "browser",
                label: "Browser",
                hint: "System voices. No server round-trip.",
              },
              {
                value: "openai",
                label: "OpenAI TTS",
                hint: "Premium voices. Requires server key.",
              },
            ]}
          />
          <div className="mt-4 grid grid-cols-2 gap-4">
            <div>
              <FieldLabel>Voice</FieldLabel>
              {outputMode === "browser" ? (
                <Dropdown
                  value={browserVoiceName ?? ""}
                  onChange={(v) => setBrowserVoice(v || null)}
                  options={[
                    { value: "", label: "System default" },
                    ...voices.map((v) => ({
                      value: v.name,
                      label: `${v.name} — ${v.lang}`,
                    })),
                  ]}
                />
              ) : (
                <Dropdown
                  value={openaiVoice}
                  onChange={(v) => setOpenAIVoice(v as OpenAIVoice)}
                  options={OPENAI_VOICES.map((v) => ({
                    value: v,
                    label: v.charAt(0).toUpperCase() + v.slice(1),
                  }))}
                />
              )}
            </div>
            <div>
              <FieldLabel>
                Speed{" "}
                <span className="text-text-secondary">
                  {rate.toFixed(1)}×
                </span>
              </FieldLabel>
              <SpeedSlider value={rate} onChange={setRate} />
            </div>
          </div>
          <div className="mt-4">
            <PreviewButton
              active={previewing && speakingMessageId === "__voice_preview__"}
              onClick={preview}
            />
          </div>
        </SettingsCard>

        <SettingsCard title="Behaviour">
          <ToggleRow
            checked={autoRead}
            onChange={setAutoRead}
            label="Auto-read AI responses"
            hint="Automatically read new replies aloud."
          />
        </SettingsCard>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Shared settings primitives — also imported by CustomInstructionsModal so
// both panels share spacing, type, card style, and button language.
// ---------------------------------------------------------------------------

export function SettingsCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl bg-bg-base p-5">
      <h3 className="mb-4 text-[11px] font-medium uppercase tracking-widest text-text-muted">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function FieldLabel({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label
      className={`mb-2 block text-sm font-medium text-text-primary ${className}`}
    >
      {children}
    </label>
  );
}

export function DoneButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="focus-ring rounded-lg bg-text-primary px-6 py-2 text-sm font-medium text-bg-base transition-opacity hover:opacity-90"
    >
      Done
    </button>
  );
}

function SelectionGrid<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; hint: string }[];
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            // 1px → 2px border shift compensated with -1px padding
            // on each axis so clicking between cards doesn't shift
            // the grid layout. transition-colors only — not -all —
            // to keep padding stable.
            className={`relative rounded-lg text-left transition-colors duration-150 ${
              active
                ? "border-2 border-text-primary bg-bg-surface p-[calc(1rem-1px)]"
                : "border border-border bg-transparent p-4 hover:border-border-strong"
            }`}
          >
            {active ? (
              <span
                aria-hidden
                className="absolute right-2 top-2 inline-flex h-4 w-4 items-center justify-center rounded-full bg-text-primary text-bg-base"
              >
                <CheckIcon />
              </span>
            ) : null}
            <div className="text-sm font-medium text-text-primary">
              {o.label}
            </div>
            <div className="mt-1 text-xs text-text-muted">{o.hint}</div>
          </button>
        );
      })}
    </div>
  );
}

function Dropdown({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        // Native <select>; appearance-none strips the system arrow
        // so we can render our own chevron on the right (consistent
        // with the rest of the design — no platform-flavoured arrow).
        className="focus-ring w-full appearance-none rounded-lg border border-border bg-bg-base px-3 py-2.5 pr-8 text-sm text-text-primary transition-colors hover:border-border-strong focus:border-border-strong focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <span
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted"
      >
        <ChevronDownIcon />
      </span>
    </div>
  );
}

function SpeedSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <input
      type="range"
      min={0.5}
      max={2}
      step={0.1}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      // ``accent-color`` paints the filled track + thumb a single
      // colour across modern browsers (Chrome / Edge / Safari /
      // Firefox). It's the only cross-platform way to colour a
      // native range without a full custom slider component.
      className="block h-8 w-full cursor-pointer accent-[var(--text-primary)]"
    />
  );
}

function PreviewButton({
  active,
  onClick,
}: {
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="focus-ring inline-flex items-center gap-2 rounded-lg border border-border bg-bg-surface px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-elevated"
    >
      {active ? (
        <>
          <StopIcon />
          Stop preview
        </>
      ) : (
        <>
          <PlayIcon />
          Preview voice
        </>
      )}
    </button>
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
      className="flex w-full items-start justify-between gap-4 text-left"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-text-primary">
          {label}
        </span>
        <span className="mt-1 block text-xs text-text-muted">{hint}</span>
      </span>
      <span
        aria-hidden
        className={`mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 ${
          checked ? "bg-text-primary" : "bg-border"
        }`}
      >
        <span
          className={`inline-block h-5 w-5 transform rounded-full shadow-md transition-transform duration-200 ${
            checked
              ? "translate-x-[22px] bg-bg-base"
              : "translate-x-0.5 bg-text-primary"
          }`}
        />
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function CheckIcon() {
  return (
    <svg
      width={10}
      height={10}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}
