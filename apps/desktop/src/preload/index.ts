// The preload — the ONLY code with a foot on each side of the context-
// isolation boundary. It exposes exactly one frozen, typed object and
// nothing else. ipcRenderer is USED here and NEVER EXPOSED: the page gets
// two functions (post a message, listen for messages) and two data members,
// not the IPC primitive. This is the Electron equivalent of "never
// AddHostObjectToScript" — the surface is the host's, not Electron's.
//
// It runs SANDBOXED: only the 'electron' subset, 'events', 'timers' and
// 'url' can be required — no local modules — so the surface is restated
// literally here and test/bridge-surface.test.ts pins this text against
// ../shared/bridge-surface's BRIDGE_SURFACE_MEMBERS so the two cannot drift.
//
// The nonce (C-3): obtained synchronously from the host on every navigation,
// BEFORE any page script runs. The host mints it only for the top frame of an
// allowed origin (it validates event.senderFrame); anywhere else it is null
// and every post is dropped host-side as nonce-mismatch.

import { contextBridge, ipcRenderer } from "electron";

const nonce = ipcRenderer.sendSync("sran.bridge.nonce") as string | null;
const listeners = new Set<(event: { data: unknown }) => void>();
ipcRenderer.on("sran.bridge.out", (_event, message: unknown) => {
  for (const listener of listeners) {
    try {
      listener({ data: message });
    } catch {
      /* a page listener's error is the page's problem */
    }
  }
});

const surface = Object.freeze({
  surfaceVersion: 2,
  nonce,
  postMessage: (message: unknown) => {
    ipcRenderer.send("sran.bridge.in", message);
  },
  addEventListener: (type: string, listener: (event: { data: unknown }) => void) => {
    if (type === "message" && typeof listener === "function") listeners.add(listener);
  },
});

contextBridge.exposeInMainWorld("sranHost", surface);
