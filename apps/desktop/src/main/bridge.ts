// The bridge channel (E3) — ported from SranDesktop.Shell/Bridge/BridgeChannel.cs.
// The ONLY file in the shell that touches the bridge's IPC channels.
//
//   C-1  No ipcRenderer reaches the page: the preload exposes two functions.
//        The host answers only the three transport channels declared in
//        ../shared/bridge-surface, each through a guarded wrapper.
//   C-2  event.senderFrame is validated on EVERY message — parsed as a URL,
//        compared BY ORIGIN to the allow-list, and required to be the top
//        frame of its webContents (isTrustedSender, ipc-guard.ts). A message
//        from any sub-frame — same-origin or not — is dropped and logged.
//   C-3  A per-navigation nonce: minted when the top frame's preload asks for
//        it (sendSync, before any page script runs), replaced on every
//        navigation, required on every inbound envelope.
//   C-4  The type set is closed (bridge-protocol.ts). Unknown → drop + log.
//        There is no default branch that does anything.
//
// Outbound messages go to the TOP FRAME only (webContents.send), never to a
// sub-frame. session.changed is defined and never sent (E2: the page needs no
// telling about cookie rotation).

import { ipcMain, type BrowserWindow, type IpcMainEvent, type WebContents } from "electron";
import { randomBytes } from "node:crypto";
import { isTrustedSender } from "./ipc-guard";
import { redactUrl, type Logger } from "./logger";
import { fingerprint } from "./portal-auth";
import {
  MAX_MESSAGE_BYTES,
  parseEnvelope,
  validateConsentRespond,
  validateHostQuery,
  validateTurnCancel,
  validateTurnSubmit,
  validateUiReady,
  type ConsentRespondPayload,
  type HostQueryPayload,
  type OutboundType,
  type TurnCancelPayload,
  type TurnSubmitPayload,
  type UiReadyPayload,
} from "../shared/bridge-protocol";
import { IPC_CHANNELS } from "../shared/bridge-surface";

export interface BridgeHandlers {
  onTurnSubmit(payload: TurnSubmitPayload): void;
  onTurnCancel(payload: TurnCancelPayload): void;
  onConsentRespond(payload: ConsentRespondPayload): void;
  onUiReady(payload: UiReadyPayload): void;
  onHostQuery(payload: HostQueryPayload): void;
}

export class BridgeChannel {
  /** nonce per webContents id — replaced on every top-frame navigation. */
  private readonly nonces = new Map<number, string>();
  private handlers: BridgeHandlers | null = null;
  private window: BrowserWindow | null = null;
  /** Every accepted inbound, for the probes' evidence. */
  readonly accepted: { type: string; origin: string }[] = [];

  constructor(
    private readonly allowedOrigins: ReadonlySet<string>,
    private readonly log: Logger,
  ) {}

  attach(window: BrowserWindow, handlers: BridgeHandlers): void {
    this.window = window;
    this.handlers = handlers;
    this.guardedOn(IPC_CHANNELS.nonce, (event) => {
      // Minted per navigation: the preload runs once per document, so every
      // request here IS a new top-frame document.
      const nonce = randomBytes(32).toString("base64url");
      this.nonces.set(event.sender.id, nonce);
      this.log.info(`Bridge nonce issued to the top document (sha256/8=${fingerprint(nonce)})`);
      event.returnValue = nonce;
    }, (event) => {
      event.returnValue = null;
    });
    this.guardedOn(IPC_CHANNELS.inbound, (event, message) => this.onInbound(event, message));
    this.log.info(`Bridge attached: channels ${IPC_CHANNELS.nonce}, ${IPC_CHANNELS.inbound} (in), ${IPC_CHANNELS.outbound} (out)`);
  }

  /** The senderFrame check (C-2), quoted in the report. Runs before ANY
   * handler on ANY channel. The rejected branch may answer (the nonce
   * channel must return null to sendSync) but never runs the handler. */
  private guardedOn(
    channel: string,
    handler: (event: IpcMainEvent, ...args: unknown[]) => void,
    rejected?: (event: IpcMainEvent) => void,
  ): void {
    ipcMain.on(channel, (event, ...args) => {
      const frame = event.senderFrame;
      const verdict = isTrustedSender(
        { frameUrl: frame?.url, isMainFrame: frame !== null && frame === event.sender.mainFrame },
        this.allowedOrigins,
      );
      if (!verdict.trusted) {
        this.log.warn(`BRIDGE INBOUND DROPPED: sender-frame-${verdict.reason} (channel=${channel}, frame=${frame ? redactUrl(frame.url) : "(none)"})`);
        rejected?.(event);
        return;
      }
      handler(event, ...args);
    });
  }

  private onInbound(event: IpcMainEvent, message: unknown): void {
    const origin = originOf(event.senderFrame?.url ?? "");
    let bytes = 0;
    try {
      bytes = Buffer.byteLength(JSON.stringify(message) ?? "", "utf8");
    } catch {
      this.log.warn(`BRIDGE INBOUND DROPPED: unreadable message (source=${origin})`);
      return;
    }
    if (bytes > MAX_MESSAGE_BYTES) {
      this.log.warn(`BRIDGE INBOUND DROPPED: too-large (${bytes} bytes, source=${origin})`);
      return;
    }
    const envelope = parseEnvelope(message, this.nonces.get(event.sender.id) ?? null);
    if (!envelope.ok) {
      this.log.warn(`BRIDGE INBOUND DROPPED: ${envelope.reason} (source=${origin}, ${bytes} bytes)`);
      return;
    }
    const h = this.handlers;
    if (!h) return;
    // The closed set. Each arm validates its payload; nothing else exists.
    switch (envelope.type) {
      case "turn.submit": {
        const v = validateTurnSubmit(envelope.payload);
        if (!v.ok) {
          this.log.warn(`BRIDGE INBOUND DROPPED: turn.submit payload invalid: ${v.reason}`);
          return;
        }
        this.accept("turn.submit", origin);
        h.onTurnSubmit(v.value);
        return;
      }
      case "turn.cancel": {
        const v = validateTurnCancel(envelope.payload);
        if (!v.ok) {
          this.log.warn(`BRIDGE INBOUND DROPPED: turn.cancel payload invalid: ${v.reason}`);
          return;
        }
        this.accept("turn.cancel", origin);
        h.onTurnCancel(v.value);
        return;
      }
      case "consent.respond": {
        const v = validateConsentRespond(envelope.payload);
        if (!v.ok) {
          this.log.warn(`BRIDGE INBOUND DROPPED: consent.respond payload invalid: ${v.reason}`);
          return;
        }
        this.accept("consent.respond", origin);
        h.onConsentRespond(v.value);
        return;
      }
      case "ui.ready": {
        const v = validateUiReady(envelope.payload);
        if (!v.ok) {
          this.log.warn(`BRIDGE INBOUND DROPPED: ui.ready payload invalid: ${v.reason}`);
          return;
        }
        this.accept("ui.ready", origin);
        this.log.info(`BRIDGE HANDSHAKE: ui.ready (nonce verified) conv=${v.value.conversationId ?? "-"} lastSeq=${v.value.lastSeq}`);
        h.onUiReady(v.value);
        return;
      }
      case "host.query": {
        const v = validateHostQuery(envelope.payload);
        if (!v.ok) {
          this.log.warn(`BRIDGE INBOUND DROPPED: host.query invalid: ${v.reason}`);
          return;
        }
        this.accept(`host.query:${v.value.op}`, origin);
        h.onHostQuery(v.value);
        return;
      }
    }
  }

  private accept(type: string, origin: string): void {
    this.accepted.push({ type, origin });
    this.log.info(`BRIDGE INBOUND: ${type} from ${origin}`);
  }

  // ── outbound: top frame only ──────────────────────────────────────────────
  post(type: OutboundType, payload: unknown): void {
    const wc: WebContents | undefined = this.window?.webContents;
    if (!wc || wc.isDestroyed()) return;
    wc.send(IPC_CHANNELS.outbound, { type, payload });
  }
  postTurnEvent(conversationId: string, seq: number, event: string, data: unknown): void {
    this.post("turn.event", { conversationId, seq, event, data });
  }
  postHostError(code: string, message: string): void {
    this.post("host.error", { code, message });
  }
  postHostResult(requestId: string, ok: boolean, data?: unknown, error?: string): void {
    this.post("host.result", { requestId, ok, data, error });
  }
}

function originOf(url: string): string {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.origin : u.protocol + "//" + (u.host || "opaque");
  } catch {
    return "(unparseable)";
  }
}
