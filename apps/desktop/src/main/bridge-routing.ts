// FIX34 Part A — outbound routing for a bridge with MORE THAN ONE window.
//
// Until FIX34 the bridge posted every host→page envelope to the one main
// window. The quick-ask window is a second BrowserWindow on the same origin
// with the same preload, so inbound messages already carry a per-webContents
// nonce (bridge.ts keeps one per sender id); what was missing is the OUTBOUND
// half: which window a turn.event or host.result belongs to.
//
// The rule is "reply to whoever asked":
//   * turn.event  → the webContents that submitted (or resumed) that
//                   conversation; a conversation nobody claimed → main;
//   * host.result → the webContents that posted the host.query (by requestId),
//                   then the entry is forgotten;
//   * everything else (consent.request, host.notice, host.error,
//                   session.changed) → the MAIN window only. A quick-ask turn
//                   has no tools, so no consent can ever be for it.
// A window that has gone away falls back to main; when even main is gone the
// caller gets null and drops the message (nothing to show it to).
//
// Pure: ids in, id out. No 'electron' import — unit-tested in plain Node.

export interface RouterInbound {
  readonly type: string;
  readonly payload: unknown;
}

export class OutboundRouter {
  private mainId: number | null = null;
  private readonly windows = new Set<number>();
  /** conversationId → webContents id of the page that owns its turn stream. */
  private readonly conversationOwner = new Map<string, number>();
  /** host.query requestId → webContents id of the requester. */
  private readonly requestOwner = new Map<string, number>();

  registerWindow(webContentsId: number, opts: { main: boolean }): void {
    this.windows.add(webContentsId);
    if (opts.main) this.mainId = webContentsId;
  }

  unregisterWindow(webContentsId: number): void {
    this.windows.delete(webContentsId);
    if (this.mainId === webContentsId) this.mainId = null;
    for (const [k, v] of this.conversationOwner) if (v === webContentsId) this.conversationOwner.delete(k);
    for (const [k, v] of this.requestOwner) if (v === webContentsId) this.requestOwner.delete(k);
  }

  get main(): number | null {
    return this.mainId;
  }

  /** Called for every ACCEPTED inbound envelope (after validation). */
  noteInbound(senderId: number, inbound: RouterInbound): void {
    const p = (inbound.payload ?? {}) as Record<string, unknown>;
    switch (inbound.type) {
      case "turn.submit":
      case "turn.cancel":
      case "ui.ready": {
        const conv = p["conversationId"];
        if (typeof conv === "string" && conv) this.conversationOwner.set(conv, senderId);
        return;
      }
      case "host.query": {
        const id = p["requestId"];
        if (typeof id === "string" && id) this.requestOwner.set(id, senderId);
        return;
      }
      default:
        return;
    }
  }

  /** The webContents id an outbound envelope goes to, or null to drop it. */
  targetFor(type: string, payload: unknown): number | null {
    const p = (payload ?? {}) as Record<string, unknown>;
    if (type === "turn.event") {
      const conv = p["conversationId"];
      const owner = typeof conv === "string" ? this.conversationOwner.get(conv) : undefined;
      return this.alive(owner) ?? this.alive(this.mainId);
    }
    if (type === "host.result") {
      const id = p["requestId"];
      const owner = typeof id === "string" ? this.requestOwner.get(id) : undefined;
      if (typeof id === "string") this.requestOwner.delete(id);
      return this.alive(owner) ?? this.alive(this.mainId);
    }
    return this.alive(this.mainId);
  }

  /** The window that owns a conversation's stream (for "which window asked"). */
  ownerOf(conversationId: string): number | null {
    return this.alive(this.conversationOwner.get(conversationId));
  }

  private alive(id: number | null | undefined): number | null {
    return typeof id === "number" && this.windows.has(id) ? id : null;
  }
}
