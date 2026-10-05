"use client";

// AM10 — an installed agent's chats, listed beneath it in the sidebar: pinned
// first, then the newest activity first, each named for what it is (the
// period of its latest round, or when it began) with rename, pin, "open
// beside" (two rounds side by side) and delete, and "แชทใหม่กับ agent นี้" on
// top. Deleting a chat takes it off the list and nothing else — the run
// pages, the files and the audit rows of its rounds live on, which the
// confirmation says.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { Columns2, MessageSquarePlus, MoreHorizontal, Pencil, Pin, PinOff, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Spinner } from "@/components/ui/Spinner";

import { errorMessage } from "./api";
import { chatKey, useChats } from "./chats";
import { useAmT } from "./i18n";
import type { AgentThread } from "./types";
import { cls } from "./ui";
import { dashboardHref, workspaceHref } from "./workspace/context";

const ITEM = "focus-ring flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm text-text-primary hover:bg-bg-elevated disabled:opacity-50";

/** The chat on screen: ?thread=<id> of the workspace URL ("main" or absent = the first). */
export function currentChatKey(search: string | null | undefined): string {
  const v = (search ? new URLSearchParams(search).get("thread") : null) ?? "";
  return v && v !== "main" ? v : "main";
}

export function besideHref(installId: string, current: string, other: string): string {
  const base = workspaceHref(installId, current === "main" ? null : current);
  return `${base}${base.includes("?") ? "&" : "?"}beside=${encodeURIComponent(other)}`;
}

export function ChatRows({
  installId,
  agentName,
  active,
  currentKey,
  onNavigate,
}: {
  installId: string;
  agentName: string;
  /** the workspace of this agent is on screen */
  active: boolean;
  /** the chat on screen (when active) */
  currentKey: string;
  onNavigate?: () => void;
}) {
  const t = useAmT();
  const router = useRouter();
  const { chats, error, create, rename, pin, remove } = useChats(installId);
  const [creating, setCreating] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const newChat = () => {
    setCreating(true);
    setNote(null);
    create().then(
      (th) => {
        setCreating(false);
        onNavigate?.();
        router.push(workspaceHref(installId, th.id));
      },
      (err) => {
        setCreating(false);
        setNote(`${t("ws.newChatFailed")}: ${errorMessage(err)}`);
      },
    );
  };

  return (
    <ul className="flex flex-col gap-px pb-1" data-testid="agent-chat-rows" aria-label={t("chat.listAria", { name: agentName })}>
      <li>
        <button type="button" onClick={newChat} disabled={creating} className={cls(ITEM, "rounded-lg pl-10 text-xs text-text-secondary")} data-testid="agent-chat-new">
          {creating ? <Spinner size={13} /> : <MessageSquarePlus width={13} height={13} aria-hidden />}
          <span className="min-w-0 flex-1 truncate">{t("chat.new")}</span>
        </button>
      </li>
      {note ? (
        <li className="px-10 py-1 text-[11px] text-status-danger" role="alert">
          {note}
        </li>
      ) : null}
      {error && !chats ? <li className="px-10 py-1 text-[11px] text-text-muted">{errorMessage(error)}</li> : null}
      {chats && chats.length === 0 ? <li className="px-10 py-1 text-[11px] text-text-muted">{t("chat.none")}</li> : null}
      {(chats ?? []).map((c) => (
        <ChatRow
          key={chatKey(c)}
          installId={installId}
          chat={c}
          current={active && currentKey === chatKey(c)}
          currentKey={active ? currentKey : null}
          onNavigate={onNavigate}
          onRename={(title) => rename(c, title)}
          onPin={() => pin(c, !c.pinned_at)}
          onDelete={() => remove(c)}
        />
      ))}
    </ul>
  );
}

function ChatRow({
  installId,
  chat,
  current,
  currentKey,
  onNavigate,
  onRename,
  onPin,
  onDelete,
}: {
  installId: string;
  chat: AgentThread;
  current: boolean;
  currentKey: string | null;
  onNavigate?: () => void;
  onRename: (title: string) => Promise<unknown>;
  onPin: () => Promise<unknown>;
  onDelete: () => Promise<{ cancelled_runs: number; kept_runs: number }>;
}) {
  const t = useAmT();
  const router = useRouter();
  const key = chatKey(chat);
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(chat.title);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (renaming) inputRef.current?.select();
  }, [renaming]);

  const act = (fn: () => Promise<unknown>, then?: () => void) => {
    setBusy(true);
    setFail(null);
    fn().then(
      () => {
        setBusy(false);
        setOpen(false);
        then?.();
      },
      (err) => {
        setBusy(false);
        setFail(errorMessage(err));
      },
    );
  };

  const commitRename = () => {
    const title = draft.trim();
    setRenaming(false);
    if (title === chat.title) return;
    act(() => onRename(title));
  };

  const href = workspaceHref(installId, chat.id);
  const canBeside = currentKey !== null && currentKey !== key;

  return (
    <li className="group relative flex items-center" data-testid="agent-chat-row" data-chat={key} data-current={current || undefined}>
      {renaming ? (
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            if (e.key === "Escape") {
              setDraft(chat.title);
              setRenaming(false);
            }
          }}
          maxLength={80}
          aria-label={t("chat.renameAria")}
          className="focus-ring ml-10 mr-9 w-full min-w-0 rounded-md border border-border bg-bg-surface px-2 py-1 text-sm text-text-primary"
          data-testid="agent-chat-rename-input"
        />
      ) : (
        <Link
          href={href}
          onClick={onNavigate}
          aria-current={current ? "page" : undefined}
          className={cls(
            "focus-ring flex min-w-0 flex-1 items-center gap-2 rounded-lg py-1.5 pl-10 pr-9 text-sm transition-colors hover:bg-bg-elevated hover:text-text-primary",
            current ? "bg-bg-elevated text-text-primary" : "text-text-secondary",
          )}
          data-testid="agent-chat-link"
        >
          {chat.pinned_at ? <Pin width={11} height={11} aria-hidden className="shrink-0 text-text-muted" /> : null}
          <span className="min-w-0 flex-1 truncate">{chat.title}</span>
          <span className="shrink-0 text-[10px] text-text-muted">{chat.waiting ? t("chat.waiting") : t("chat.rounds", { n: String(chat.rounds) })}</span>
        </Link>
      )}
      <div ref={menuRef} className="absolute right-1">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          aria-label={t("chat.menu", { name: chat.title })}
          className={cls(
            "focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-text-muted transition-opacity hover:bg-bg-surface hover:text-text-primary focus:opacity-100 group-hover:opacity-100",
            open || busy ? "opacity-100" : "opacity-0",
          )}
          data-testid="agent-chat-menu"
        >
          {busy ? <Spinner size={13} /> : <MoreHorizontal width={14} height={14} aria-hidden />}
        </button>
        {open ? (
          <div id={menuId} role="menu" className="absolute right-0 top-full z-30 mt-1 w-56 overflow-hidden rounded-xl border border-border bg-bg-surface py-1 shadow-lg" data-testid="agent-chat-menu-list">
            <button
              type="button"
              role="menuitem"
              className={ITEM}
              onClick={() => {
                setOpen(false);
                setDraft(chat.title);
                setRenaming(true);
              }}
              data-testid="agent-chat-rename"
            >
              <Pencil width={14} height={14} aria-hidden />
              <span>{t("chat.rename")}</span>
            </button>
            <button type="button" role="menuitem" className={ITEM} onClick={() => act(onPin)} data-testid="agent-chat-pin">
              {chat.pinned_at ? <PinOff width={14} height={14} aria-hidden /> : <Pin width={14} height={14} aria-hidden />}
              <span>{chat.pinned_at ? t("chat.unpin") : t("chat.pin")}</span>
            </button>
            {canBeside ? (
              <button
                type="button"
                role="menuitem"
                className={ITEM}
                onClick={() => {
                  setOpen(false);
                  onNavigate?.();
                  router.push(besideHref(installId, currentKey!, key));
                }}
                data-testid="agent-chat-beside"
              >
                <Columns2 width={14} height={14} aria-hidden />
                <span>{t("chat.beside")}</span>
              </button>
            ) : null}
            <button
              type="button"
              role="menuitem"
              className={cls(ITEM, "text-status-danger")}
              onClick={() => {
                setOpen(false);
                setConfirm(true);
              }}
              data-testid="agent-chat-delete"
            >
              <Trash2 width={14} height={14} aria-hidden />
              <span>{t("chat.delete")}</span>
            </button>
            {fail ? (
              <p className="px-3 py-1.5 text-[11px] text-status-danger" role="alert">
                {fail}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        title={t("chat.delete.title", { name: chat.title })}
        testId="agent-chat-delete-dialog"
        footer={
          <>
            <Button type="button" variant="secondary" size="sm" onClick={() => setConfirm(false)} data-testid="agent-chat-delete-cancel">
              {t("chat.delete.cancel")}
            </Button>
            <Button
              type="button"
              variant="danger"
              size="sm"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setFail(null);
                onDelete().then(
                  () => {
                    setBusy(false);
                    setConfirm(false);
                    if (current) router.replace(workspaceHref(installId, null));
                  },
                  (err) => {
                    setBusy(false);
                    setFail(errorMessage(err));
                  },
                );
              }}
              data-testid="agent-chat-delete-confirm"
            >
              {t("chat.delete.confirm")}
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-secondary" data-testid="agent-chat-delete-body">
          {t("chat.delete.body")}
        </p>
        {chat.waiting ? <p className="mt-2 text-sm text-status-warning">{t("chat.delete.waiting")}</p> : null}
        <p className="mt-2 text-xs text-text-muted">
          <Link href={dashboardHref(installId)} className="underline underline-offset-2" onClick={() => setConfirm(false)}>
            {t("chat.delete.where")}
          </Link>
        </p>
        {fail ? (
          <p className="mt-2 text-sm text-status-danger" role="alert">
            {fail}
          </p>
        ) : null}
      </Modal>
    </li>
  );
}
