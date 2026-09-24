// FIX132 C — "delete my account". The account lives in the gateway (the
// identity authority), so the request goes there, not to chat-edge: the
// gateway checks the typed email and the team rule, has chat-edge hide every
// chat, ends every session and schedules the purge. Its error messages are
// already written for the user (Thai), so they are shown as they come.

import { GATEWAY_URL } from "./auth";

export type DeleteAccountResult =
  | { ok: true; purgeAfter: string | null; purgeDays: number }
  | { ok: false; status: number; code: string; message: string };

export async function deleteAccount(confirm: string): Promise<DeleteAccountResult> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY_URL}/portal/me/delete-account`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ confirm }),
    });
  } catch {
    return { ok: false, status: 0, code: "network", message: "" };
  }
  let body: {
    purge_after?: string;
    purge_days?: number;
    error?: { code?: string; message?: string };
  } | null = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (res.ok) {
    return { ok: true, purgeAfter: body?.purge_after ?? null, purgeDays: body?.purge_days ?? 30 };
  }
  return {
    ok: false,
    status: res.status,
    code: body?.error?.code ?? `HTTP_${res.status}`,
    message: body?.error?.message ?? "",
  };
}
