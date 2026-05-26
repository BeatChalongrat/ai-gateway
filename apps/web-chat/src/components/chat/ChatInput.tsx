"use client";

import { useEffect, useRef, useState } from "react";

import { ModelSelector } from "./ModelSelector";

interface Props {
  disabled: boolean;
  streaming: boolean;
  /** Currently-active model. The conversation page passes the
   *  conversation's model_id; the landing page passes the global
   *  default (omit → ModelSelector falls back to its own store). */
  activeModelId?: string | null;
  /** Called when the user picks a different model. Optional —
   *  omitted on the landing page so the global default flows. */
  onModelPick?: (modelId: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  // When set, the parent injected an outside value (e.g. from a
  // prompt pill click). The input adopts it and the ref is cleared.
  injectedText?: string | null;
  onInjectedTextConsumed?: () => void;
}

// 6 lines × ~24px line-height + vertical padding.
const MAX_TEXTAREA_HEIGHT = 180;

export function ChatInput({
  disabled,
  streaming,
  activeModelId,
  onModelPick,
  onSend,
  onStop,
  injectedText,
  onInjectedTextConsumed,
}: Props) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow up to 6 lines.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }, [text]);

  // Parent (empty-state prompt pill) injected text → adopt + focus.
  useEffect(() => {
    if (!injectedText) return;
    setText(injectedText);
    onInjectedTextConsumed?.();
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (ta) {
        ta.focus();
        const end = ta.value.length;
        ta.setSelectionRange(end, end);
      }
    });
  }, [injectedText, onInjectedTextConsumed]);

  function submit() {
    if (streaming || disabled) return;
    const t = text.trim();
    if (!t) return;
    onSend(t);
    setText("");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="border-t border-border bg-bg-base/95 px-4 py-4 md:px-10">
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        <div className="flex items-end gap-2 rounded-2xl border border-border bg-bg-secondary px-3 py-2 transition-colors focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/30">
          <div className="pb-0.5">
            <ModelSelector
              activeModelId={activeModelId}
              disabled={streaming}
              compact
              onPick={
                onModelPick ? (m) => onModelPick(m.id) : undefined
              }
            />
          </div>
          <textarea
            ref={taRef}
            rows={1}
            value={text}
            disabled={disabled}
            placeholder={
              streaming ? "Streaming response…" : "Message SRAN Chat…"
            }
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            className="flex-1 resize-none bg-transparent px-1 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none disabled:opacity-60"
            style={{
              maxHeight: MAX_TEXTAREA_HEIGHT,
              transition: "height 100ms ease-out",
            }}
          />
          <div className="relative flex h-9 w-9 items-center justify-center">
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generation"
              tabIndex={streaming ? 0 : -1}
              className={`focus-ring absolute inset-0 flex items-center justify-center rounded-lg bg-status-danger/85 text-white transition-all duration-150 hover:bg-status-danger ${
                streaming
                  ? "scale-100 opacity-100"
                  : "pointer-events-none scale-90 opacity-0"
              }`}
            >
              <svg width={14} height={14} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={disabled || !text.trim()}
              aria-label="Send"
              tabIndex={streaming ? -1 : 0}
              className={`focus-ring absolute inset-0 flex items-center justify-center rounded-lg bg-accent text-white transition-all duration-150 hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40 ${
                streaming
                  ? "pointer-events-none scale-90 opacity-0"
                  : "scale-100 opacity-100"
              }`}
            >
              <svg width={14} height={14} viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M12 19V5M5 12l7-7 7 7"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        </div>
        <div className="text-center text-[10px] text-text-muted">
          Press <kbd className="rounded bg-bg-elevated px-1">Enter</kbd> to send,{" "}
          <kbd className="rounded bg-bg-elevated px-1">Shift</kbd>+
          <kbd className="rounded bg-bg-elevated px-1">Enter</kbd> for new line.
        </div>
      </div>
    </div>
  );
}
