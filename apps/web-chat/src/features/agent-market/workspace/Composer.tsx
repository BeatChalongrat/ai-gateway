"use client";

// The workspace composer — the app composer's look (rounded surface pill,
// auto-growing text box, + to attach, round send), scoped to what an agent
// takes: text and files. Files upload the moment they are added; Send posts
// the text with their ids. The server echoes the message into the thread.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import { useComposerHeightProbe } from "@/hooks/useBottomInset";

import { amApi, errorMessage, newClientId } from "../api";
import { useAmT } from "../i18n";
import type { ThreadItem } from "../types";
import { cls } from "../ui";
import { PhotoNotice, StagedChip } from "./FileChips";
import { DEFAULT_ACCEPT, hasImage, useUploads } from "./useUploads";

const MAX_TEXT = 4000;
const MAX_HEIGHT = 200;

export interface ComposerHandle {
  addFiles: (files: File[]) => void;
}

interface Props {
  installId: string;
  onSent: (item: ThreadItem | undefined) => void;
  accept?: string[];
  maxMb?: number;
  /** The agent's note on photos (manifest photo_notice): shown under the chips while one is attached. */
  photoNotice?: string;
  /** Footer line under the pill: what the agent cannot do + the model note. */
  footer?: React.ReactNode;
}

export const Composer = forwardRef<ComposerHandle, Props>(function Composer({ installId, onSent, accept, maxMb, photoNotice, footer }, ref) {
  const t = useAmT();
  const acceptList = accept?.length ? accept : DEFAULT_ACCEPT;
  const uploads = useUploads(installId, { accept: acceptList, maxMb });
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clientId = useRef(newClientId());
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const composing = useRef(false);

  // The credits badge and toasts float above the docked composer (FIX86).
  useComposerHeightProbe(rootRef);

  const addFiles = uploads.add;
  useImperativeHandle(ref, () => ({ addFiles }), [addFiles]);

  // Auto-grow up to MAX_HEIGHT, then scroll inside.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, MAX_HEIGHT)}px`;
  }, [text]);

  const trimmed = text.trim();
  const canSend = !sending && !uploads.uploading && !uploads.failed && (trimmed.length > 0 || uploads.ids.length > 0);

  const send = useCallback(() => {
    if (!canSend) return;
    setSending(true);
    setError(null);
    amApi
      .message(installId, { text: trimmed.slice(0, MAX_TEXT), file_ids: uploads.ids, client_id: clientId.current })
      .then(
        (res) => {
          setText("");
          uploads.clear();
          clientId.current = newClientId();
          setSending(false);
          onSent(res?.item);
          taRef.current?.focus();
        },
        (err) => {
          // The draft and its client id stay: a retry is the same message.
          setSending(false);
          setError(errorMessage(err));
        },
      );
  }, [canSend, installId, trimmed, uploads, onSent]);

  return (
    <div ref={rootRef} className="safe-bottom shrink-0 bg-bg-base px-3 pb-3 pt-2 md:px-6 md:pb-4">
      <div className="mx-auto flex w-full max-w-chat flex-col gap-2">
        <div
          className="relative flex flex-col border border-border bg-bg-surface transition-colors focus-within:border-border-strong"
          style={{ borderRadius: 24, boxShadow: "0 2px 12px rgba(0, 0, 0, 0.18)" }}
        >
          {uploads.items.length ? (
            <div role="group" aria-label={t("composer.chips")} className="flex flex-wrap gap-1.5 px-3 pt-3" data-testid="am-composer-chips">
              {uploads.items.map((s) => (
                <StagedChip key={s.localId} item={s} onRemove={() => uploads.remove(s.localId)} />
              ))}
            </div>
          ) : null}
          {photoNotice && hasImage(uploads.items) ? <PhotoNotice text={photoNotice} className="px-4 pt-2" /> : null}
          <div className="px-4 pt-3">
            <textarea
              ref={taRef}
              rows={1}
              value={text}
              maxLength={MAX_TEXT}
              aria-label={t("composer.label")}
              placeholder={t("composer.placeholder")}
              onChange={(e) => setText(e.target.value)}
              onCompositionStart={() => {
                composing.current = true;
              }}
              onCompositionEnd={() => {
                composing.current = false;
              }}
              onKeyDown={(e) => {
                if (e.key !== "Enter" || e.shiftKey) return;
                // An IME is still composing (Thai input methods, CJK): Enter is theirs.
                if (composing.current || e.nativeEvent.isComposing || e.keyCode === 229) return;
                e.preventDefault();
                send();
              }}
              className="scrollbar-thin block w-full resize-none bg-transparent text-[15px] leading-relaxed text-text-primary placeholder:text-text-muted focus:outline-none"
              style={{ maxHeight: MAX_HEIGHT }}
              data-testid="am-composer-input"
            />
          </div>
          <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
            <input
              ref={fileRef}
              type="file"
              multiple
              accept={acceptList.join(",")}
              className="hidden"
              onChange={(e) => {
                const files = Array.from(e.target.files ?? []);
                e.target.value = "";
                if (files.length) uploads.add(files);
              }}
              data-testid="am-composer-file"
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              aria-label={t("composer.attach")}
              title={t("composer.attach")}
              className="focus-ring tap-44 inline-flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary"
            >
              <svg width={18} height={18} viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
            <button
              type="button"
              onClick={send}
              disabled={!canSend}
              aria-label={t("composer.send")}
              data-testid="am-composer-send"
              className={cls(
                "focus-ring tap-44 flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors duration-150",
                canSend ? "composer-send-fill" : "cursor-not-allowed",
              )}
              style={canSend ? undefined : { background: "var(--bg-surface-hover)", color: "var(--text-tertiary)" }}
            >
              <svg width={16} height={16} viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M8 14V2M8 2L3 7M8 2L13 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
        </div>
        {error ? (
          <p role="alert" className="px-2 text-xs text-status-danger">
            {`${t("composer.sendFailed")}: ${error}`}
          </p>
        ) : null}
        {footer}
      </div>
    </div>
  );
});
