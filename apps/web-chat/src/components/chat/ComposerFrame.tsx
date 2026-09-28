"use client";

// The composer, once. Everything a person recognises as "the box I type
// in" lives here — the docked root that floats above a soft keyboard, the
// rounded pill, the text box that grows to about half the window, the "+"
// to attach, the tools beside it, the one send/stop button, the footer —
// and every surface that has a composer renders this component: the chat
// (ChatInput) with its slash menu, voice, modes and model picker, and an
// agent's workspace with its own uploads and model control. What differs
// between them is passed in; what a person sees is not rebuilt.
//
// Send/stop is ONE persistent button whose command, label and icon swap on
// ``streaming`` — never two sibling elements that mount/unmount (swapping
// elements dropped keyboard focus to <body> for mouse-click senders). Both
// the send-ready and the stop state take the ``composer-send-fill`` class
// (globals.css) so they cannot drift apart; while streaming ``stop-halo``
// adds a breathing box-shadow ring (FIX75). The disabled state's inline
// style references the RAW CSS variables globals.css defines:
// ``--bg-base`` / ``--bg-elevated`` only exist as Tailwind aliases, so it
// uses ``--bg-surface-hover``.

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { useComposerHeightProbe } from "@/hooks/useBottomInset";
import { maxTextareaHeight } from "@/lib/composerShortcuts";
import { useT } from "@/lib/i18n";
import { shouldSendOnEnter } from "@/lib/keyboard";

export interface ComposerAttach {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** The "+" opens a menu (the desktop's attach menu) rather than the picker. */
  menu?: { open: boolean; content: ReactNode; containerRef: RefObject<HTMLDivElement> };
}

interface Props {
  textareaRef: RefObject<HTMLTextAreaElement>;
  value: string;
  onChange: (text: string) => void;
  placeholder: string;
  disabled?: boolean;
  maxLength?: number;
  /** An answer is being generated: the button stops it. */
  streaming?: boolean;
  canSend: boolean;
  onSubmit: () => void;
  onStop?: () => void;
  /** Keys the surface handles before Enter-to-send (a menu's arrows, ↑ to
   *  edit). `composing` is the tracked IME state. Return true when handled. */
  onKeyDownFirst?: (e: React.KeyboardEvent<HTMLTextAreaElement>, composing: boolean) => boolean;
  /** Above the pill, in the same column (the recording bar). */
  above?: ReactNode;
  /** The pill is hidden, not unmounted (recording replaces it). */
  pillHidden?: boolean;
  /** Inside the pill, above the text box: menus, the file input, chips, notices. */
  pillTop?: ReactNode;
  attach?: ComposerAttach;
  /** Beside the "+": what is brought INTO the turn and under what rules. */
  leftTools?: ReactNode;
  /** Beside send: how the turn goes OUT (model, modes, voice). */
  rightTools?: ReactNode;
  footer?: ReactNode;
  /** After the column, inside the root (dialogs). */
  after?: ReactNode;
  /** Names the text box for a screen reader where the placeholder does not. */
  textareaLabel?: string;
  textareaTestId?: string;
  sendTestId?: string;
}

export function ComposerFrame({
  textareaRef,
  value,
  onChange,
  placeholder,
  disabled = false,
  maxLength,
  streaming = false,
  canSend,
  onSubmit,
  onStop,
  onKeyDownFirst,
  above,
  pillHidden = false,
  pillTop,
  attach,
  leftTools,
  rightTools,
  footer,
  after,
  textareaLabel,
  textareaTestId,
  sendTestId,
}: Props) {
  const t = useT();
  // FIX86 — the ONE producer of the vertical axis: whatever this box
  // measures, every bottom-floating element sits above it.
  const rootRef = useRef<HTMLDivElement>(null);
  useComposerHeightProbe(rootRef);
  // Explicit IME composition tracking (Phase 4, Thai IME). WebView2's native
  // KeyboardEvent.isComposing can be unreliable in an embedded control, so we
  // also track composition via compositionstart/end and OR it into the
  // Enter-to-send guard. This guarantees Enter during composition commits the
  // candidate rather than submitting a half-formed word.
  const composingRef = useRef(false);
  const [isMobile, setIsMobile] = useState(false);
  // Distance (px) the on-screen keyboard is occluding the bottom of
  // the layout viewport. Updated from visualViewport.resize so the
  // composer floats just above the keyboard on iOS / Android.
  const [keyboardInset, setKeyboardInset] = useState(0);
  // FIX89 Part A — the cap is a relationship, not two magic numbers: about
  // half of the window the composer is actually in, minus the composer's own
  // chrome, floored at what the cap used to be so it can only grow. Measured
  // here (not assumed) and re-measured whenever the window or the chrome
  // changes; `visualViewport` is what shrinks when a soft keyboard opens.
  const [viewportHeight, setViewportHeight] = useState(0);
  const [chromeHeight, setChromeHeight] = useState(0);
  const maxTextareaHeightPx = maxTextareaHeight(viewportHeight, chromeHeight, isMobile);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    const sync = () => setViewportHeight(vv?.height ?? window.innerHeight);
    sync();
    window.addEventListener("resize", sync);
    vv?.addEventListener("resize", sync);
    return () => {
      window.removeEventListener("resize", sync);
      vv?.removeEventListener("resize", sync);
    };
  }, []);

  // The chrome around the text area (toolbar row, padding, chips): the box's
  // height minus the text area's. A ResizeObserver keeps it live, so a second
  // row of attachment chips takes its room out of the text area's share
  // rather than pushing the box past half the window.
  useEffect(() => {
    const root = rootRef.current;
    const ta = textareaRef.current;
    if (!root || !ta || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const next = Math.max(0, Math.round(root.getBoundingClientRect().height - ta.getBoundingClientRect().height));
      setChromeHeight((prev) => (Math.abs(prev - next) > 1 ? next : prev));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(root);
    ro.observe(ta);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia("(max-width: 639px)");
    const sync = () => setIsMobile(mql.matches);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, []);

  // visualViewport tracks the layout-viewport overlap with the
  // soft keyboard on mobile. When it shrinks, the difference vs
  // window.innerHeight is the keyboard overlay height — push the
  // composer up by that amount so the textarea stays visible.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    if (!vv) return;
    const sync = () => {
      const overlap = window.innerHeight - vv.height - vv.offsetTop;
      setKeyboardInset(Math.max(0, overlap));
    };
    sync();
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    return () => {
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
    };
  }, []);

  // Auto-grow up to the device's max line count.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, maxTextareaHeightPx)}px`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, maxTextareaHeightPx]);

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (onKeyDownFirst?.(e, composingRef.current)) return;
    // Phase 7 — the IME composition guard lives in shouldSendOnEnter:
    // during composition Enter commits the candidate and must NOT send.
    const native = e.nativeEvent as KeyboardEvent;
    if (
      shouldSendOnEnter({
        key: e.key,
        shiftKey: e.shiftKey,
        // OR in the explicitly-tracked composition state (WebView2 robustness).
        isComposing: native.isComposing || composingRef.current,
        keyCode: native.keyCode,
      })
    ) {
      e.preventDefault();
      onSubmit();
    }
  }

  return (
    <div
      ref={rootRef}
      data-testid="composer-root"
      className="safe-bottom bg-bg-base px-3 py-3 md:px-6 md:py-4"
      style={
        keyboardInset > 0
          ? { transform: `translateY(-${keyboardInset}px)`, transition: "transform 150ms ease-out" }
          : undefined
      }
    >
      <div className="mx-auto flex w-full max-w-chat flex-col gap-2">
        {above}

        {/* Composer pill — Claude-style.
            Pending files (when any) sit at the top of the pill so the
            container visibly grows when the user attaches something.
            Row textarea → row toolbar follows underneath.
            Decoupling the buttons from the textarea row sidesteps
            every "send is misaligned with first line" problem — the
            send button now sits in its own row with its own padding
            and only competes with the model pill for vertical
            alignment. */}
        <div
          className="relative flex flex-col border border-border bg-bg-surface transition-colors focus-within:border-border-strong"
          style={{
            borderRadius: 24,
            boxShadow: "0 2px 12px rgba(0, 0, 0, 0.18)",
            display: pillHidden ? "none" : undefined,
          }}
        >
          {pillTop}

          {/* Row 1: textarea, full width */}
          <div className="px-4 pt-3">
            <textarea
              ref={textareaRef}
              data-shortcut="composer"
              data-testid={textareaTestId}
              aria-label={textareaLabel}
              rows={1}
              value={value}
              disabled={disabled}
              maxLength={maxLength}
              placeholder={placeholder}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={onKeyDown}
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onCompositionEnd={() => {
                composingRef.current = false;
              }}
              className="w-full resize-none bg-transparent text-[15px] leading-relaxed text-text-primary placeholder:text-text-placeholder focus:outline-none disabled:opacity-60"
              style={{
                maxHeight: maxTextareaHeightPx,
                transition: "height 100ms ease-out",
              }}
            />
          </div>

          {/* Row 2: attach · what comes into the turn (left) · how it goes out + send (right). */}
          <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
            <div className="flex min-w-0 items-center gap-1.5">
              {attach ? (
                <div className="relative" ref={attach.menu?.containerRef}>
                  <button
                    type="button"
                    onClick={attach.onClick}
                    aria-label={attach.label}
                    title={attach.label}
                    aria-haspopup={attach.menu ? "menu" : undefined}
                    aria-expanded={attach.menu ? attach.menu.open : undefined}
                    disabled={attach.disabled}
                    data-testid="attach-button"
                    className="focus-ring tap-44 inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
                      <path
                        d="M12 5v14M5 12h14"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                  {attach.menu?.open ? attach.menu.content : null}
                </div>
              ) : null}
              {leftTools}
            </div>

            {/* FIX128 D4 — min-w-0 lets this group give way on a 360 px
                screen (the model label truncates) instead of pushing the
                send button past the composer's clipped edge. */}
            <div className="flex min-w-0 items-center gap-1.5">
              {rightTools}
              <button
                type="button"
                onClick={streaming ? onStop : onSubmit}
                disabled={!streaming && !canSend}
                aria-label={streaming ? t("input.stop") : t("input.send")}
                data-testid={sendTestId}
                className={`focus-ring tap-44 flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors duration-150 ${
                  streaming
                    ? "composer-send-fill stop-halo"
                    : canSend
                      ? "composer-send-fill"
                      : "disabled:cursor-not-allowed"
                }`}
                style={
                  streaming || canSend
                    ? undefined
                    : {
                        background: "var(--bg-surface-hover)",
                        color: "var(--text-tertiary)",
                      }
                }
              >
                {streaming ? (
                  <svg width={13} height={13} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                    <rect x="6.5" y="6.5" width="11" height="11" rx="3" />
                  </svg>
                ) : (
                  <svg
                    width={16}
                    height={16}
                    viewBox="0 0 16 16"
                    fill="none"
                    aria-hidden
                  >
                    <path
                      d="M8 14V2M8 2L3 7M8 2L13 7"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
              </button>
            </div>
          </div>
        </div>

        {footer}
      </div>

      {after}
    </div>
  );
}
