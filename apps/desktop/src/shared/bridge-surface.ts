// The ONE object the preload exposes to the page, and the IPC transport
// channels the main process registers.
//
// E1 shipped this with no methods; E3 adds the bridge: two functions (post,
// listen) and the per-navigation nonce. That is the whole page-visible API —
// the closed message vocabulary rides INSIDE it (../shared/bridge-protocol).
// ipcRenderer itself is never a member.
//
// This module must never import 'electron': the pinning tests load it in
// plain Node. The preload cannot import it either (sandboxed preloads cannot
// require local modules), so the preload restates the surface literally and
// test/bridge-surface.test.ts cross-checks the two texts.

/** `window.sranHost` — the global name the page sees. */
export const BRIDGE_GLOBAL_NAME = "sranHost";

/** Bumped whenever the member list changes. E1 = 1, E3 = 2. */
export const BRIDGE_SURFACE_VERSION = 2;

export interface SranHostSurface {
  readonly surfaceVersion: number;
  /** Per-navigation nonce (C-3); null outside the top frame of an allowed origin. */
  readonly nonce: string | null;
  /** Post one envelope {nonce, type, payload} to the host. Fire-and-forget;
   * everything invalid is dropped and logged host-side. */
  readonly postMessage: (message: unknown) => void;
  /** Subscribe to host→page envelopes ({type, payload}); only "message". */
  readonly addEventListener: (type: string, listener: (event: { data: unknown }) => void) => void;
}

/** The exact, exhaustive member list, BY NAME. Adding, removing or renaming
 * a member without updating this fails test/bridge-surface.test.ts. */
export const BRIDGE_SURFACE_MEMBERS: readonly (keyof SranHostSurface)[] = Object.freeze([
  "surfaceVersion",
  "nonce",
  "postMessage",
  "addEventListener",
] as const);

/** The IPC transport channels — three, all registered through guarded
 * wrappers that validate event.senderFrame (main/bridge.ts). These are the
 * PIPE; the closed vocabulary is in bridge-protocol.ts. */
export const IPC_CHANNELS = Object.freeze({
  /** preload → main, sendSync at preload time: the per-navigation nonce. */
  nonce: "sran.bridge.nonce",
  /** page → main (via the preload's postMessage): one envelope. */
  inbound: "sran.bridge.in",
  /** main → page (via webContents.send to the top frame): one envelope. */
  outbound: "sran.bridge.out",
} as const);
