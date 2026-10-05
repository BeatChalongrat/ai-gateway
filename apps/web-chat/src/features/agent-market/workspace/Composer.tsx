"use client";

// The workspace composer IS the app's composer (AM5: ComposerFrame, the one
// the chat renders) — the same docked box, text area, "+", send, keyboard
// and IME behaviour — configured with what an agent takes: text and files,
// the agent's own upload endpoint and accept list, and its model control.
//
// AM6 — what the chat's composer has was decided one by one:
//   kept      attach, dictation (the chat's own, ./Dictation), the model
//             control, send;
//   added     the knowledge chip (the chat's KBSelector), in ask mode only —
//             while no file is staged. Knowledge answers a question; it is
//             never the source of a number in a deliverable, so a message
//             with files never names a knowledge base;
//   left out  folders and projects, and what an agent has no use for (slash
//             commands, image and video modes, the permission mode).
// The placeholder is short, like the chat's. What it used to spell out is a
// row of suggestions above the box, which go once a round is underway.
//
// Files upload the moment they are added; Send posts the text with their
// ids. The server echoes the message into the thread. Typed-but-unsent text
// survives a reload, per agent and chat, in the app's own draft store.

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

import { ComposerFrame } from "@/components/chat/ComposerFrame";
import { DictationButton, useDictation } from "@/components/chat/Dictation";
import { KBSelector } from "@/components/chat/KBSelector";
import { Button, ButtonLink } from "@/components/ui/Button";
import { useKnowledgeAvailable } from "@/hooks/useKnowledgeAvailable";
import { clearDraft, loadDraft, saveDraft } from "@/lib/draftStore";
import { useT } from "@/lib/i18n";

import { amApi, errorMessage, newClientId } from "../api";
import { useAmT } from "../i18n";
import type { ThreadItem } from "../types";
import { Thai } from "../ui";
import { EstimateLine } from "./Estimate";
import { PhotoNotice, StagedChip } from "./FileChips";
import { useAgentKnowledge } from "./knowledge";
import { DEFAULT_ACCEPT, formChoice, hasImage, useUploads } from "./useUploads";

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
  /** AM4: the thread the message goes to. */
  threadId?: string | null;
  /** AM10: the kinds a form for this round can be (empty: the agent fills no forms). */
  formAccept?: string[];
  /** AM4: the model control, beside send where the chat's model picker sits. */
  tools?: React.ReactNode;
  /** AM6: the agent's short name, for the placeholder. */
  agentName?: string;
  /** AM6: the agent is at work in this thread: no suggestions. */
  roundUnderway?: boolean;
  /** AM6: the person's latest round has ended and left files: there is something to ask about. */
  finishedRound?: boolean;
  /** AM6: what a round costs, said above the box while files are staged and no round is underway. */
  estimate?: string | null;
  /** AM6: the install's self-test round whose files are in the thread: before a round of the person's own,
   *  a chip shows them. */
  sampleRunId?: string | null;
  /** AM6: the agent's data sheet (what it reads): the chip in its place when there is no sample. */
  datasheetHref?: string | null;
}

/** Where this agent chat's unsent text is kept (the app's draft store). */
export function draftKeyFor(installId: string, threadId?: string | null): string {
  return `agent:${installId}:${threadId || "first"}`;
}

/** A file is going up: the chat's own words for it, from the app's dictionary. */
function useUploadingPlaceholder(): string {
  const t = useT();
  return t("input.placeholder.uploading");
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
  formAccept,
  tools,
  agentName,
  roundUnderway = false,
  finishedRound = false,
  estimate,
  sampleRunId = null,
  datasheetHref = null,
  handle,
}: Props & { handle: React.ForwardedRef<ComposerHandle> }) {
  const t = useAmT();
  const uploadingPlaceholder = useUploadingPlaceholder();
  const inputAccept = accept?.length ? accept : DEFAULT_ACCEPT;
  // AM10: an agent that fills forms takes a form dropped into the round, beside its inputs
  const forms = formAccept ?? [];
  const acceptList = useMemo(() => [...inputAccept, ...forms.filter((f) => !inputAccept.includes(f))], [inputAccept, forms]);
  const uploads = useUploads(installId, { accept: acceptList, maxMb });
  // which staged files are this round's form: the person's mark, else what the file can only be
  const [formMarks, setFormMarks] = useState<Record<string, boolean>>({});
  const formOf = (s: { localId: string; name: string }) => {
    const c = formChoice(s.name, inputAccept, forms);
    return { can: c.can, on: c.can && (formMarks[s.localId] ?? c.byDefault) };
  };
  const draftKey = draftKeyFor(installId, threadId);
  const [text, setText] = useState(() => loadDraft(draftKey) ?? "");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clientId = useRef(newClientId());
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const dictation = useDictation({ setText, textareaRef: taRef });

  // Ask mode: the message being composed has no file. Only then is there a
  // knowledge chip, and only then does the message name a knowledge base.
  const kbAvailable = useKnowledgeAvailable();
  const [kbId, pickKb] = useAgentKnowledge(installId);
  const asking = uploads.items.length === 0;
  const kbOffered = kbAvailable || !!kbId;

  const addFiles = uploads.add;
  const pickFiles = useCallback(() => fileRef.current?.click(), []);
  useImperativeHandle(handle, () => ({ addFiles, pickFiles, focus: () => taRef.current?.focus() }), [addFiles, pickFiles]);

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

  const formIds = uploads.items.filter((s) => s.status === "done" && s.fileId && formOf(s).on).map((s) => s.fileId as string);

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
        ...(formIds.length ? { form_file_ids: formIds } : {}),
        ...(asking && kbId ? { knowledge_base_id: kbId } : {}),
      })
      .then(
        (res) => {
          setText("");
          textRef.current = "";
          clearDraft(draftKey);
          uploads.clear();
          setFormMarks({});
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canSend, installId, trimmed, uploads, onSent, threadId, draftKey, asking, kbId, formIds.join(",")]);

  /** A suggestion's sentence goes into the box, to be edited before it is sent. */
  const suggest = (sentence: string) => {
    setText(sentence);
    const ta = taRef.current;
    if (!ta) return;
    ta.focus();
    requestAnimationFrame(() => ta.setSelectionRange(ta.value.length, ta.value.length));
  };

  const suggesting = !roundUnderway && asking && !trimmed && !dictation.active;
  /** The sample's files card, in the thread above. */
  const showSample = () => {
    const el = sampleRunId ? document.querySelector<HTMLElement>(`[data-testid="am-outputs"][data-run="${CSS.escape(sampleRunId)}"]`) : null;
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const name = agentName?.trim();

  return (
    <ComposerFrame
      textareaRef={taRef}
      value={text}
      onChange={setText}
      maxLength={MAX_TEXT}
      placeholder={uploads.uploading ? uploadingPlaceholder : name ? t("composer.placeholderTo", { name }) : t("composer.placeholderAgent")}
      textareaLabel={t("composer.label")}
      textareaTestId="am-composer-input"
      sendTestId="am-composer-send"
      canSend={canSend}
      onSubmit={send}
      above={
        <>
          {dictation.bar}
          {!asking && !roundUnderway ? <EstimateLine line={estimate} /> : null}
          {suggesting ? (
            <div role="group" aria-label={t("composer.suggest.aria")} className="flex flex-wrap items-center justify-center gap-2" data-testid="am-composer-suggestions">
              <Button type="button" variant="secondary" size="sm" onClick={pickFiles}>
                {t("composer.suggest.attach")}
              </Button>
              {finishedRound ? (
                <>
                  <Button type="button" variant="secondary" size="sm" onClick={() => suggest(t("composer.suggest.askText"))}>
                    {t("composer.suggest.ask")}
                  </Button>
                  <Button type="button" variant="secondary" size="sm" onClick={() => suggest(t("composer.suggest.formats"))}>
                    {t("composer.suggest.formats")}
                  </Button>
                </>
              ) : sampleRunId ? (
                <Button type="button" variant="secondary" size="sm" onClick={showSample}>
                  {t("composer.suggest.sample")}
                </Button>
              ) : datasheetHref ? (
                <ButtonLink href={datasheetHref} variant="secondary" size="sm">
                  {t("composer.suggest.datasheet")}
                </ButtonLink>
              ) : null}
            </div>
          ) : null}
        </>
      }
      pillHidden={dictation.active}
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
          {uploads.items.some((s) => s.status !== "error" && formOf(s).can) ? (
            <ul className="flex flex-col gap-1 px-4 pt-2" data-testid="am-composer-forms">
              {uploads.items
                .filter((s) => s.status !== "error" && formOf(s).can)
                .map((s) => (
                  <li key={s.localId}>
                    <label className="flex cursor-pointer items-start gap-2 text-xs text-text-secondary">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={formOf(s).on}
                        onChange={(e) => setFormMarks((cur) => ({ ...cur, [s.localId]: e.target.checked }))}
                        data-testid="am-composer-form-mark"
                        data-name={s.name}
                      />
                      <span className="min-w-0">{t("form.mark", { name: s.name })}</span>
                    </label>
                  </li>
                ))}
            </ul>
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
      attach={{ label: t("composer.attach"), onClick: pickFiles }}
      rightTools={
        <>
          {kbOffered && asking ? <KBSelector conversationId={null} standalone activeKBId={kbId} onPick={pickKb} /> : null}
          {tools}
          {dictation.supported ? <DictationButton onStart={dictation.start} /> : null}
        </>
      }
      footer={
        <>
          {kbOffered && !asking ? (
            <p className="text-center text-[11px] text-text-muted" data-testid="am-kb-ask-only">
              {t("knowledge.askOnly")}
            </p>
          ) : null}
          {footer}
        </>
      }
    />
  );
}
