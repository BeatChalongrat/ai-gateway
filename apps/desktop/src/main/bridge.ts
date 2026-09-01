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
//
// FIX34 Part A: MORE THAN ONE window. The main window is attached first
// (attach); the quick-ask window is added with addWindow. The inbound side
// was already per-webContents (nonce per sender id); the outbound side now
// routes by "who asked" (bridge-routing.ts): a turn's events go to the window
// that submitted it, a host.result to the requester, everything else to main.

import { ipcMain, type BrowserWindow, type IpcMainEvent, type WebContents } from "electron";
import { randomBytes } from "node:crypto";
import { OutboundRouter } from "./bridge-routing";
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
  /** Every attached window by webContents id (main + quick-ask). */
  private readonly windows = new Map<number, BrowserWindow>();
  private readonly router = new OutboundRouter();
  private ipcInstalled = false;
  /** Every accepted inbound, for the probes' evidence. */
  readonly accepted: { type: string; origin: string }[] = [];

  constructor(
    private readonly allowedOrigins: ReadonlySet<string>,
    private readonly log: Logger,
  ) {}

  attach(window: BrowserWindow, handlers: BridgeHandlers): void {
    this.handlers = handlers;
    this.register(window, true);
    if (this.ipcInstalled) return;
    this.ipcInstalled = true;
    this.guardedOn(IPC_CHANNELS.nonce, (event) => {
      // Minted per navigation: the preload runs once per document, so every
      // request here IS a new top-frame document.
      const nonce = randomBytes(32).toString("base64url");
      this.nonces.set(event.sender.id, nonce);
      this.log.info(`Bridge nonce issued to the top document (sha256/8=${fingerprint(nonce)}, wc=${event.sender.id})`);
      event.returnValue = nonce;
    }, (event) => {
      event.returnValue = null;
    });
    this.guardedOn(IPC_CHANNELS.inbound, (event, message) => this.onInbound(event, message));
    this.log.info(`Bridge attached: channels ${IPC_CHANNELS.nonce}, ${IPC_CHANNELS.inbound} (in), ${IPC_CHANNELS.outbound} (out)`);
  }

  /** FIX34 Part A: a second window on the same origin (the quick-ask window).
   * Same channels, same guards; only the outbound routing knows it exists. */
  addWindow(window: BrowserWindow): void {
    this.register(window, false);
    this.log.info(`Bridge: window added (wc=${window.webContents.id})`);
  }

  private register(window: BrowserWindow, main: boolean): void {
    const id = window.webContents.id;
    this.windows.set(id, window);
    this.router.registerWindow(id, { main });
    window.once("closed", () => {
      this.windows.delete(id);
      this.router.unregisterWindow(id);
      this.nonces.delete(id);
    });
  }

  /** Which window (webContents id) owns a conversation's turn stream, if any. */
  ownerOf(conversationId: string): number | null {
    return this.router.ownerOf(conversationId);
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
    const sender = event.sender.id;
    // The closed set. Each arm validates its payload; nothing else exists.
    switch (envelope.type) {
      case "turn.submit": {
        const v = validateTurnSubmit(envelope.payload);
        if (!v.ok) {
          this.log.warn(`BRIDGE INBOUND DROPPED: turn.submit payload invalid: ${v.reason}`);
          return;
        }
        this.accept("turn.submit", origin);
        this.router.noteInbound(sender, { type: "turn.submit", payload: v.value });
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
        this.router.noteInbound(sender, { type: "turn.cancel", payload: v.value });
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
        this.log.info(`BRIDGE HANDSHAKE: ui.ready (nonce verified) conv=${v.value.conversationId ?? "-"} lastSeq=${v.value.lastSeq} wc=${sender}`);
        this.router.noteInbound(sender, { type: "ui.ready", payload: v.value });
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
        this.router.noteInbound(sender, { type: "host.query", payload: v.value });
        h.onHostQuery(v.value);
        return;
      }
    }
  }

  private accept(type: string, origin: string): void {
    this.accepted.push({ type, origin });
    this.log.info(`BRIDGE INBOUND: ${type} from ${origin}`);
  }

  // ── outbound: top frame of the window that asked ──────────────────────────
  post(type: OutboundType, payload: unknown): void {
    const target = this.router.targetFor(type, payload);
    if (target === null) return;
    const wc: WebContents | undefined = this.windows.get(target)?.webContents;
    if (!wc || wc.isDestroyed()) return;
    wc.send(IPC_CHANNELS.outbound, { type, payload });
  }
  postTurnEvent(conversationId: string, seq: number, event: string, data: unknown, runId?: string): void {
    // FIX41 — runId rides on the ENVELOPE beside seq, where the page
    // already reads seq from. Putting it in `data` instead would mean
    // every event payload gained a field the renderer has to ignore.
    this.post("turn.event", { conversationId, runId, seq, event, data });
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
