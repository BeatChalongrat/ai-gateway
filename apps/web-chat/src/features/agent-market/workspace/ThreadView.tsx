"use client";

// The agent workspace thread is the app's chat thread (AM5): the person's
// messages are the chat's bubble with the chat's attachment cards and copy
// on it, the agent's words are the chat's answer body with copy and the
// time under it, a failed turn is the chat's failed-turn card, a state the
// person should know about is the chat's note. They are the same components
// (components/chat/MessageParts), not look-alikes. On top of that surface
// sit the things only an agent has: the greeting, question cards, the
// progress of a round, its files and numbers, round dividers.

import Link from "next/link";
import { createContext, useContext, useMemo, useState } from "react";
import { Check, ListChecks, RotateCcw } from "lucide-react";

import { Markdown } from "@/components/chat/Markdown";
import {
  AssistantProse,
  CollapsibleUserText,
  CopyAction,
  ErrorCard,
  ErrorCardAction,
  MessageTime,
  NoteCard,
  UserMessageRow,
  UserTextPill,
  useCopyText,
} from "@/components/chat/MessageParts";

import { amApi, errorMessage } from "../api";
import { useAmT } from "../i18n";
import { isUserItem } from "../thread";
import { ProblemButton } from "../problem/ProblemButton";
import type {
  AgentTextPayload,
  CardPayload,
  Failure,
  GreetingPayload,
  NoticePayload,
  OutputsPayload,
  ProgressPayload,
  ThreadItem,
  UserFilesPayload,
  UserTextPayload,
} from "../types";
import { BTN_SMALL, cls, DOT, Spin, Thai } from "../ui";
import { RunExceptions } from "../exceptions/ExceptionsPanel";
import { AgentQuestionCard } from "./AgentQuestionCard";
import { runHref, settingsHref, useWorkspace } from "./context";
import { FileChip } from "./FileChips";
import { OutputsCard } from "./OutputsCard";
import { ProgressCard } from "./ProgressCard";
import { RequestFilesCard } from "./RequestFilesCard";
import { personText } from "./personText";
import { FoldedRound, IntentChip, RoundDivider } from "./RoundParts";
import { segments, supersededOutputs } from "./rounds";

/** AM5: the files cards a newer one of the same round has replaced. */
const Superseded = createContext<ReadonlySet<string>>(new Set());

const KNOWN = new Set(["greeting", "user_text", "user_files", "agent_text", "notice", "card", "progress", "outputs", "intent"]);

type Group = { side: "user" | "agent"; items: ThreadItem[] };

export function groupItems(items: ThreadItem[]): Group[] {
  const groups: Group[] = [];
  for (const it of items) {
    if (!KNOWN.has(it.kind)) continue;
    const side = isUserItem(it) ? "user" : "agent";
    const last = groups[groups.length - 1];
    if (last && last.side === side && side === "agent") last.items.push(it);
    else groups.push({ side, items: [it] });
  }
  return groups;
}

/** AM4: the thread as rounds — a finished round closed by its divider, older ones folded. */
export function ThreadView({ items }: { items: ThreadItem[] }) {
  const replaced = useMemo(() => supersededOutputs(items), [items]);
  return (
    <Superseded.Provider value={replaced}>
      <Rounds items={items} />
    </Superseded.Provider>
  );
}

function Rounds({ items }: { items: ThreadItem[] }) {
  const segs = segments(items);
  const out: React.ReactNode[] = [];
  let run: ThreadItem[] = [];
  const flush = () => {
    if (run.length) out.push(<ThreadItems key={`g-${run[0]!.id}`} items={run} />);
    run = [];
  };
  for (const s of segs) {
    if (s.type === "item") {
      run.push(s.item);
      continue;
    }
    flush();
    if (s.type === "divider") {
      out.push(<RoundDivider key={`d-${s.round.runId}`} round={s.round} latest={s.latest} started={s.started} />);
    } else {
      out.push(
        <FoldedRound key={`f-${s.round.runId}`} round={s.round}>
          <ThreadItems items={s.items} />
        </FoldedRound>,
      );
    }
  }
  flush();
  return <div className="flex flex-col gap-5">{out}</div>;
}

export function ThreadItems({ items }: { items: ThreadItem[] }) {
  const groups = groupItems(items);
  return (
    <Thai>
      <div className="flex flex-col gap-5">
        {groups.map((g) =>
          g.side === "user" ? (
            <UserItem key={g.items[0]!.id} item={g.items[0]!} />
          ) : (
            // Like the chat's answers: in the column, no avatar (the header
            // already says which agent this is).
            <div key={g.items[0]!.id} className="flex w-full min-w-0 flex-col gap-3" data-testid="am-agent-group">
              {g.items.map((it) => (
                <AgentItem key={it.id} item={it} />
              ))}
            </div>
          ),
        )}
      </div>
    </Thai>
  );
}

function UserItem({ item }: { item: ThreadItem }) {
  const p = (item.payload ?? {}) as UserTextPayload & UserFilesPayload;
  const files = item.kind === "user_files" ? p.files ?? [] : [];
  const text = (p.text ?? "").trim();
  const { copied, copy } = useCopyText(text);
  if (!text && files.length === 0) return null;
  return (
    <UserMessageRow
      data-testid="am-user-item"
      data-item-id={item.id}
      attachments={
        files.length ? (
          <div className="mt-1 flex flex-col gap-1.5">
            {files.map((f) => (
              <FileChip key={f.id} id={f.id} name={f.name} size={f.size} />
            ))}
          </div>
        ) : null
      }
      below={
        text ? (
          <div className="message-actions flex items-center gap-0.5">
            <CopyAction copied={copied} onCopy={copy} />
          </div>
        ) : null
      }
    >
      {text ? (
        <UserTextPill>
          <CollapsibleUserText content={text} />
        </UserTextPill>
      ) : null}
    </UserMessageRow>
  );
}

function AgentItem({ item }: { item: ThreadItem }) {
  const replaced = useContext(Superseded);
  switch (item.kind) {
    case "greeting":
      return <Greeting p={(item.payload ?? {}) as GreetingPayload} />;
    case "agent_text":
      return <AgentText p={(item.payload ?? {}) as AgentTextPayload} at={item.created_at} />;
    case "notice":
      return <Notice p={(item.payload ?? {}) as NoticePayload} />;
    case "card": {
      const card = item.payload as CardPayload;
      if (!card || !card.card_id) return null;
      return card.kind === "request_files" ? <RequestFilesCard card={card} /> : <AgentQuestionCard card={card} />;
    }
    case "progress": {
      const p = item.payload as ProgressPayload;
      return p?.run_id ? <ProgressCard progress={p} /> : null;
    }
    case "intent":
      return <IntentChip item={item} />;
    case "outputs": {
      const p = item.payload as OutputsPayload;
      if (!p?.run_id) return null;
      // what the run left for a person to decide sits under its files (a re-render has none of its own)
      return p.rerender ? (
        <OutputsCard outputs={p} />
      ) : (
        <div className="flex flex-col gap-3">
          <OutputsCard outputs={p} superseded={replaced.has(item.id)} />
          <RunExceptions runId={p.run_id} />
        </div>
      );
    }
    default:
      return null;
  }
}

function Greeting({ p }: { p: GreetingPayload }) {
  const t = useAmT();
  const needs = p.needs ?? [];
  const produces = p.produces ?? [];
  return (
    <div data-testid="am-greeting">
      {p.text ? <p className="text-[15px] leading-relaxed text-text-primary">{p.text}</p> : null}
      {needs.length ? (
        <ul className="mt-2 flex flex-col gap-1.5">
          {needs.map((n, i) => (
            <li key={`${n.label}-${i}`} className="flex items-start gap-2 text-sm">
              <Check width={15} height={15} strokeWidth={2.5} className="mt-0.5 shrink-0 text-status-success" aria-hidden />
              <span className="min-w-0 text-text-primary">
                {n.label}
                {n.note ? <span className="text-text-muted">{` — ${n.note}`}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {produces.length ? (
        <p className="mt-2 text-xs text-text-muted">{`${t("greeting.produces")}: ${produces.map((o) => o.label).join(", ")}`}</p>
      ) : null}
    </div>
  );
}

function AgentText({ p, at }: { p: AgentTextPayload; at?: string }) {
  const text = p.text ?? "";
  const { copied, copy } = useCopyText(text);
  if (!text.trim()) return null;
  if (p.tone === "error") return <FailedRound p={p} />;
  return (
    <div className="flex w-full flex-col gap-1.5">
      <AssistantProse data-testid="am-agent-text">
        <Markdown>{text}</Markdown>
      </AssistantProse>
      <div className="flex items-center gap-1">
        <div className="flex items-center gap-0.5">
          <CopyAction copied={copied} onCopy={copy} />
        </div>
        <MessageTime createdAt={at} />
      </div>
    </div>
  );
}

/** AM5 — a round that did not work, on the chat's failed-turn card: what
 *  happened, what to try next, whether the files are safe, whether anything
 *  was charged — and the ways forward. Never the error's own text, a type
 *  name or a step id: the server sends none, and whatever an older row still
 *  holds is taken out here. The technical detail is on the round's page and
 *  goes along with "แจ้งปัญหา". */
function FailedRound({ p }: { p: AgentTextPayload }) {
  const t = useAmT();
  const ws = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const f: Failure | null = p.failure ?? null;
  const say = (s: string | undefined) => personText(s ?? "");
  const what = f ? say(f.what) || t("failed.generic") : say(p.text) || t("failed.generic");
  const next = f ? say(f.next) : "";
  const files = f ? say(f.files) : "";
  const charged = f ? say(f.charged) : "";
  const runId = p.run_id;
  const retry = () => {
    if (!runId) return;
    setBusy(true);
    setNote(null);
    amApi.retry(runId).then(
      (r) => {
        setBusy(false);
        if (r?.item) ws?.merge([r.item]);
        setNote({ text: t("failed.retried"), error: false });
      },
      (err) => {
        setBusy(false);
        setNote({ text: errorMessage(err), error: true });
      },
    );
  };
  const retried = !!note && !note.error;
  return (
    <div data-testid="am-failed-round" data-tone="error" aria-label={t("failed.aria")}>
      <ErrorCard
        message={
          <span className="whitespace-pre-wrap" data-testid="am-failed-what">
            {what}
          </span>
        }
        actions={
          <>
            {next ? (
              <p className="mt-1.5 pl-6 text-text-primary" data-testid="am-failed-next">
                {next}
              </p>
            ) : null}
            {files ? (
              <p className="mt-1 pl-6 text-xs text-text-secondary" data-testid="am-failed-files">
                {files}
              </p>
            ) : null}
            {charged ? (
              <p className="mt-0.5 pl-6 text-xs text-text-secondary" data-testid="am-failed-charged">
                {charged}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-x-4 pl-6">
              {f?.can_retry && runId && !retried ? (
                <ErrorCardAction testId="am-failed-retry" disabled={busy} icon={busy ? <Spin size={14} /> : <RotateCcw size={14} className="shrink-0" aria-hidden />} onClick={retry}>
                  {busy ? t("failed.retrying") : t("failed.retry")}
                </ErrorCardAction>
              ) : null}
              {runId ? (
                <Link href={runHref(runId)} className="mt-2 inline-flex max-w-full items-center gap-2 text-left underline" data-testid="am-failed-run">
                  <ListChecks size={14} className="shrink-0" aria-hidden />
                  <span className="min-w-0 break-words">{t("failed.run")}</span>
                </Link>
              ) : null}
            </div>
            {note ? (
              <p role={note.error ? "alert" : "status"} className="mt-1.5 pl-6 text-xs text-text-primary" data-testid="am-failed-note">
                {note.text}
              </p>
            ) : null}
            <div className="mt-2 pl-6 text-text-primary">
              {runId ? <ProblemButton target={{ kind: "run", runId }} /> : ws ? <ProblemButton target={{ kind: "install", installId: ws.installId }} /> : null}
            </div>
          </>
        }
      />
    </div>
  );
}

/** "จำไว้เป็นค่าตั้งต้นรอบหน้า · แก้ได้" → the text, then "แก้ได้" as a link. */
export function splitNotice(text: string): string {
  return text.replace(/\s*[··]?\s*แก้ได้\s*$/u, "").trim();
}

function Notice({ p }: { p: NoticePayload }) {
  const t = useAmT();
  const ws = useWorkspace();
  const text = splitNotice(personText(p.text ?? ""));
  if (!text) return null;
  // AM3: a run waiting for budget (or an agent switched off) is said plainly, not in grey.
  if (p.tone === "warning" || p.action === "resume_budget") return <WarningNotice p={p} text={text} />;
  return (
    <p className={cls("flex flex-wrap items-center gap-x-1.5 text-xs", p.tone === "success" ? "text-status-success" : "text-text-muted")} data-testid="am-notice">
      <span>{text}</span>
      {p.link === "defaults" && ws ? (
        <>
          <span aria-hidden>{DOT}</span>
          <Link href={settingsHref(ws.installId)} className="focus-ring rounded text-text-secondary underline underline-offset-2 hover:text-text-primary">
            {t("notice.edit")}
          </Link>
        </>
      ) : null}
    </p>
  );
}

/** AM3: a notice that needs the person — a run waiting for budget offers "ลองต่อ". */
function WarningNotice({ p, text }: { p: NoticePayload; text: string }) {
  const t = useAmT();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const resume = () => {
    if (!p.run_id) return;
    setBusy(true);
    setNote(null);
    amApi.resume(p.run_id).then(
      () => {
        setBusy(false);
        setNote({ text: t("budget.resumed"), ok: true });
      },
      (err) => {
        setBusy(false);
        setNote({ text: errorMessage(err), ok: false });
      },
    );
  };
  return (
    <NoteCard tone="warning" data-testid="am-notice" data-tone="warning">
      <p className="whitespace-pre-wrap">{text}</p>
      {p.action === "resume_budget" && p.run_id ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" onClick={resume} disabled={busy} className={BTN_SMALL} data-testid="am-resume-budget">
            {busy ? <Spin size={12} /> : null}
            <span>{t("budget.resume")}</span>
          </button>
          {note ? (
            <span className={cls("text-xs", note.ok ? "text-status-success" : "text-text-secondary")} data-testid="am-resume-note">
              {note.text}
            </span>
          ) : null}
        </div>
      ) : null}
    </NoteCard>
  );
}
