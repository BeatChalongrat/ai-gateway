"use client";

// AM10 — an agent's chats, as the sidebar and the workspace list them:
// read from GET /installs/{id}/threads, kept per install while any part of
// the page shows them, and told to read again when one changes anywhere on
// the page (a rename in the sidebar, a new chat from the workspace menu).

import { useCallback, useEffect, useState } from "react";

import { amApi } from "./api";
import type { AgentThread } from "./types";

type Listener = (installId: string) => void;
const listeners = new Set<Listener>();

export function onChatsChanged(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function notifyChatsChanged(installId: string): void {
  for (const fn of listeners) fn(installId);
}

/** The first chat has no id; the routes and the URL call it "main". */
export const chatKey = (t: Pick<AgentThread, "id">): string => t.id ?? "main";

export interface ChatsState {
  chats: AgentThread[] | null;
  error: unknown;
  reload: () => void;
  rename: (chat: AgentThread, title: string) => Promise<AgentThread>;
  pin: (chat: AgentThread, on: boolean) => Promise<AgentThread>;
  remove: (chat: AgentThread) => Promise<{ cancelled_runs: number; kept_runs: number }>;
  create: () => Promise<AgentThread>;
}

export function useChats(installId: string, enabled = true): ChatsState {
  const [chats, setChats] = useState<AgentThread[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const reload = useCallback(() => {
    if (!enabled) return;
    amApi.threads(installId).then(
      (r) => {
        setChats(r?.threads ?? []);
        setError(null);
      },
      (err) => setError(err),
    );
  }, [installId, enabled]);

  useEffect(() => {
    reload();
    return onChatsChanged((id) => {
      if (id === installId) reload();
    });
  }, [reload, installId]);

  const replace = useCallback((next: AgentThread) => {
    setChats((cur) => (cur ?? []).map((c) => (chatKey(c) === chatKey(next) ? next : c)));
  }, []);

  const rename = useCallback(
    async (chat: AgentThread, title: string) => {
      const r = await amApi.patchThread(installId, chatKey(chat), { title });
      replace(r.thread);
      notifyChatsChanged(installId);
      return r.thread;
    },
    [installId, replace],
  );

  const pin = useCallback(
    async (chat: AgentThread, on: boolean) => {
      const r = await amApi.patchThread(installId, chatKey(chat), { pinned: on });
      replace(r.thread);
      notifyChatsChanged(installId);
      return r.thread;
    },
    [installId, replace],
  );

  const remove = useCallback(
    async (chat: AgentThread) => {
      const r = await amApi.deleteThread(installId, chatKey(chat));
      setChats((cur) => (cur ?? []).filter((c) => chatKey(c) !== chatKey(chat)));
      notifyChatsChanged(installId);
      return r;
    },
    [installId],
  );

  const create = useCallback(async () => {
    const r = await amApi.newThread(installId);
    notifyChatsChanged(installId);
    return r.thread;
  }, [installId]);

  return { chats, error, reload, rename, pin, remove, create };
}
