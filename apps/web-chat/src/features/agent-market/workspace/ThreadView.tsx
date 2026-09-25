"use client";

// The agent workspace thread. Same idiom as the app's chat: the person's
// messages are right-aligned bubbles (bg-bubble-user, rounded 20 px); the
// agent's side is plain text and cards in the column. Consecutive agent
// items share one identity tile, the way one assistant turn reads in the
// mockup (hidden below `sm`, where every pixel goes to the cards).

import Link from "next/link";
import { Check } from "lucide-react";

import { Markdown } from "@/components/chat/Markdown";

import { useAmT } from "../i18n";
import { isUserItem } from "../thread";
import type {
  AgentTextPayload,
  CardPayload,
  GreetingPayload,
  NoticePayload,
  OutputsPayload,
  ProgressPayload,
  ThreadItem,
  UserFilesPayload,
  UserTextPayload,
} from "../types";
import { AgentTile, cls, DOT } from "../ui";
import { AgentQuestionCard } from "./AgentQuestionCard";
import { settingsHref, useWorkspace } from "./context";
import { FileChip } from "./FileChips";
import { OutputsCard } from "./OutputsCard";
import { ProgressCard } from "./ProgressCard";
import { RequestFilesCard } from "./RequestFilesCard";

const KNOWN = new Set(["greeting", "user_text", "user_files", "agent_text", "notice", "card", "progress", "outputs"]);

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

export function ThreadView({ items }: { items: ThreadItem[] }) {
  const ws = useWorkspace();
  const groups = groupItems(items);
  return (
    <div className="flex flex-col gap-5">
      {groups.map((g) =>
        g.side === "user" ? (
          <UserItem key={g.items[0]!.id} item={g.items[0]!} />
        ) : (
          <div key={g.items[0]!.id} className="flex gap-3" data-testid="am-agent-group">
            <div className="hidden pt-0.5 sm:block">
              <AgentTile icon={ws?.icon} size={28} />
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-3">
              {g.items.map((it) => (
                <AgentItem key={it.id} item={it} />
              ))}
            </div>
          </div>
        ),
      )}
    </div>
  );
}

function UserItem({ item }: { item: ThreadItem }) {
  const p = (item.payload ?? {}) as UserTextPayload & UserFilesPayload;
  const files = item.kind === "user_files" ? p.files ?? [] : [];
  const text = (p.text ?? "").trim();
  if (!text && files.length === 0) return null;
  return (
    <div className="flex w-full justify-end" data-testid="am-user-item" data-item-id={item.id}>
      <div className="flex max-w-[85%] flex-col items-end gap-1.5">
        {files.length ? (
          <div className="flex flex-wrap justify-end gap-1.5">
            {files.map((f) => (
              <FileChip key={f.id} name={f.name} size={f.size} />
            ))}
          </div>
        ) : null}
        {text ? (
          <div className="max-w-full whitespace-pre-wrap break-words rounded-bubble bg-bubble-user px-4 py-2.5 text-[15px] leading-relaxed text-text-primary shadow-sm">
            {text}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function AgentItem({ item }: { item: ThreadItem }) {
  switch (item.kind) {
    case "greeting":
      return <Greeting p={(item.payload ?? {}) as GreetingPayload} />;
    case "agent_text":
      return <AgentText p={(item.payload ?? {}) as AgentTextPayload} />;
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
    case "outputs": {
      const p = item.payload as OutputsPayload;
      return p?.run_id ? <OutputsCard outputs={p} /> : null;
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

function AgentText({ p }: { p: AgentTextPayload }) {
  const text = p.text ?? "";
  if (!text.trim()) return null;
  if (p.tone === "error") {
    return (
      <div role="alert" className="rounded-xl border border-status-danger/40 bg-status-danger/10 px-4 py-2.5 text-sm text-text-primary" data-testid="am-agent-text" data-tone="error">
        <div className="whitespace-pre-wrap">{text}</div>
      </div>
    );
  }
  return (
    <div className="prose-chat w-full text-text-primary" data-testid="am-agent-text">
      <Markdown>{text}</Markdown>
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
  const text = splitNotice(p.text ?? "");
  return (
    <p className={cls("flex flex-wrap items-center gap-x-1.5 text-xs text-text-muted")} data-testid="am-notice">
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
