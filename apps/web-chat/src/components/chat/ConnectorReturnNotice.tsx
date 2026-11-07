"use client";

// What the Connectors page says when a consent flow comes back to it.
//
// The desktop app lands here with ?connected=<id>&status=ok|error from the
// link the broker's return page bounces to (apps/desktop deep-link.ts:
// connectorsPageUrl). The link only says a flow ended; whether the connector
// IS connected is asked of the broker itself (GET /portal/connectors), so a
// hand-made link can make this page say nothing that is not so. With no
// arrival, the page still says which of the broker's connectors — the
// mailbox a person sends their own work from — is connected, because this is
// the page a person looks for that on.

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { GATEWAY_URL } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";

interface Broker {
  connections: { connector_id: string; connected: boolean }[];
}

const ID = /^[a-z0-9][a-z0-9_-]{0,39}$/;

// The broker's connectors by the name a person knows them by. A product's name is the same in every
// language; the mailbox that only sends says so in the reader's. Another id reads as it is.
const NAMES: Record<string, string> = { gmail: "Gmail", gdrive: "Google Drive", gcalendar: "Google Calendar", github: "GitHub" };
const NAMED: Record<string, DictKey> = { mail: "connectors.return.name.mail" };

async function brokerState(): Promise<Broker | null> {
  try {
    const res = await fetch(`${GATEWAY_URL}/portal/connectors`, { credentials: "include" });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<Broker>;
    return { connections: Array.isArray(body.connections) ? body.connections : [] };
  } catch {
    return null;
  }
}

export function ConnectorReturnNotice() {
  const t = useT();
  const params = useSearchParams();
  const arrived = params.get("connected");
  const connector = arrived && ID.test(arrived) ? arrived : null;
  const status = params.get("status") === "ok" ? "ok" : "error";
  const [broker, setBroker] = useState<Broker | null | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    void brokerState().then((b) => {
      if (alive) setBroker(b);
    });
    return () => {
      alive = false;
    };
  }, [connector, status]);

  const name = (id: string) => (NAMED[id] ? t(NAMED[id]) : (NAMES[id] ?? id));
  const isConnected = (id: string) => !!broker?.connections.some((c) => c.connector_id === id && c.connected);
  const canGoBack = typeof window !== "undefined" && window.history.length > 1;

  if (connector) {
    // the link says how the flow ended; the broker says what is so
    const tone = broker === undefined ? "wait" : status === "ok" && isConnected(connector) ? "ok" : "error";
    const text =
      tone === "wait"
        ? t("connectors.return.checking", { name: name(connector) })
        : tone === "ok"
          ? t("connectors.return.ok", { name: name(connector) })
          : status === "ok"
            ? t("connectors.return.notSeen", { name: name(connector) })
            : t("connectors.return.error", { name: name(connector) });
    return (
      <div
        role="status"
        data-testid="connector-return"
        data-tone={tone}
        className={`mt-4 rounded-xl border px-4 py-3 text-sm ${
          tone === "ok" ? "border-status-success/40 bg-status-success/10 text-text-primary" : tone === "error" ? "border-status-warning/40 bg-status-warning/10 text-text-primary" : "border-border bg-bg-surface text-text-secondary"
        }`}
      >
        <p>{text}</p>
        {canGoBack && tone !== "wait" ? (
          <button
            type="button"
            data-testid="connector-return-back"
            className="mt-2 text-sm font-medium text-accent underline-offset-2 hover:underline"
            onClick={() => window.history.back()}
          >
            {t("connectors.return.back")}
          </button>
        ) : null}
      </div>
    );
  }
  if (broker && isConnected("mail")) {
    return (
      <p data-testid="connector-mailbox" className="mt-4 text-sm text-text-secondary">
        {t("connectors.return.mailbox")}
      </p>
    );
  }
  return null;
}
