"use client";

// The workspace composer IS the app's composer (AM5: ComposerFrame, the one
// the chat renders) — the same docked box, text area, "+", send, keyboard
// and IME behaviour — configured with what an agent takes: text and files,
// the agent's own upload endpoint and accept list, and its model control.
// What the chat has and an agent does not (voice, slash commands, image and
// video modes, the knowledge base, the desktop's folders and permission
// mode) is simply not passed in.
//
// Files upload the moment they are added; Send posts the text with their
// ids. The server echoes the message into the thread. Typed-but-unsent text
// survives a reload, per agent and chat, in the app's own draft store.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import { ComposerFrame } from "@/components/chat/ComposerFrame";
import { clearDraft, loadDraft, saveDraft } from "@/lib/draftStore";

import { amApi, errorMessage, newClientId } from "../api";
import { useAmT } from "../i18n";
import type { ThreadItem } from "../types";
import { Thai } from "../ui";
import { PhotoNotice, StagedChip } from "./FileChips";
import { DEFAULT_ACCEPT, hasImage, useUploads } from "./useUploads";

const MAX_TEXT = 4000;

export interface ComposerHandle {
  addFiles: (files: File[]) => void;
  /** AM4: open the file picker (the empty state's "เลือกไฟล์"). */
  pickFiles: () => void;
  focus: () => void;
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
  /** AM4: the thread the message goes to, and the model for this round only. */
  threadId?: string | null;
  modelChoice?: string | null;
  /** AM4: the model control, beside send where the chat's model picker sits. */
  tools?: React.ReactNode;
}

/** Where this agent chat's unsent text is kept (the app's draft store). */
export function draftKeyFor(installId: string, threadId?: string | null): string {
  return `agent:${installId}:${threadId || "first"}`;
}

export const Composer = forwardRef<ComposerHandle, Props>(function Composer(props, ref) {
  return (
    <Thai>
      {/* docked at the bottom of the workspace's column: it never gives way to the thread */}
      <div className="shrink-0">
        <ComposerBody {...props} handle={ref} />
      </div>
    </Thai>
  );
});

function ComposerBody({
  installId,
  onSent,
  accept,
  maxMb,
  photoNotice,
  footer,
  threadId,
  modelChoice,
  tools,
  handle,
}: Props & { handle: React.ForwardedRef<ComposerHandle> }) {
  const t = useAmT();
  const acceptList = accept?.length ? accept : DEFAULT_ACCEPT;
  const uploads = useUploads(installId, { accept: acceptList, maxMb });
  const draftKey = draftKeyFor(installId, threadId);
  const [text, setText] = useState(() => loadDraft(draftKey) ?? "");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clientId = useRef(newClientId());
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const addFiles = uploads.add;
  useImperativeHandle(
    handle,
    () => ({ addFiles, pickFiles: () => fileRef.current?.click(), focus: () => taRef.current?.focus() }),
    [addFiles],
  );

  // The draft: saved a second after the last key, and when the page goes.
  const textRef = useRef(text);
  textRef.current = text;
  useEffect(() => {
    const id = window.setTimeout(() => saveDraft(draftKey, text), 1000);
    return () => window.clearTimeout(id);
  }, [text, draftKey]);
  useEffect(() => {
    const flush = () => saveDraft(draftKey, textRef.current);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [draftKey]);

  const trimmed = text.trim();
  const failed = uploads.items.filter((s) => s.status === "error");
  const canSend = !sending && !uploads.uploading && failed.length === 0 && (trimmed.length > 0 || uploads.ids.length > 0);

  const send = useCallback(() => {
    if (!canSend) return;
    setSending(true);
    setError(null);
    amApi
      .message(installId, {
        text: trimmed.slice(0, MAX_TEXT),
        file_ids: uploads.ids,
        client_id: clientId.current,
        thread_id: threadId ?? null,
        ...(modelChoice ? { model_choice: modelChoice } : {}),
      })
      .then(
        (res) => {
          setText("");
          textRef.current = "";
          clearDraft(draftKey);
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
  }, [canSend, installId, trimmed, uploads, onSent, threadId, modelChoice, draftKey]);

  return (
    <ComposerFrame
      textareaRef={taRef}
      value={text}
      onChange={setText}
      maxLength={MAX_TEXT}
      placeholder={t("composer.placeholder")}
      textareaLabel={t("composer.label")}
      textareaTestId="am-composer-input"
      sendTestId="am-composer-send"
      canSend={canSend}
      onSubmit={send}
      pillTop={
        <>
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
          {uploads.items.length ? (
            <div
              role="group"
              aria-label={t("composer.chips")}
              data-testid="composer-chip-row"
              className="flex flex-nowrap gap-2 overflow-x-auto px-3 pt-3 sm:max-h-[248px] sm:flex-wrap sm:overflow-y-auto sm:overflow-x-hidden"
            >
              {uploads.items.map((s) => (
                <StagedChip key={s.localId} item={s} onRemove={() => uploads.remove(s.localId)} onRetry={() => uploads.retry(s.localId)} />
              ))}
            </div>
          ) : null}
          {failed.length ? (
            <div
              role="alert"
              data-testid="send-blocked-files"
              className="mx-4 mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-status-danger/40 bg-status-danger/10 px-3 py-2 text-xs text-text-primary"
            >
              <span className="min-w-0 flex-1">
                {failed.length === 1 ? t("composer.blockedOne", { name: failed[0]!.name }) : t("composer.blockedMany", { n: String(failed.length) })}
              </span>
              <button
                type="button"
                data-testid="remove-failed-files"
                onClick={() => {
                  for (const s of failed) uploads.remove(s.localId);
                }}
                className="focus-ring rounded-md border border-border bg-bg-surface px-2 py-1 text-xs font-medium text-text-primary hover:bg-bg-elevated"
              >
                {t("composer.removeFailed")}
              </button>
            </div>
          ) : null}
          {photoNotice && hasImage(uploads.items) ? <PhotoNotice text={photoNotice} className="px-4 pt-2" /> : null}
          {error ? (
            <p role="alert" className="px-4 pt-2 text-xs text-status-danger">
              {`${t("composer.sendFailed")}: ${error}`}
            </p>
          ) : null}
        </>
      }
      attach={{ label: t("composer.attach"), onClick: () => fileRef.current?.click() }}
      rightTools={tools}
      footer={footer}
    />
  );
}
