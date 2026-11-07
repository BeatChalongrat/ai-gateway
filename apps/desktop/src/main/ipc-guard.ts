// senderFrame validation for EVERY IPC message (E1 §2). The Electron
// equivalent of the WebView2 rule "never register CoreWebView2Frame.
// WebMessageReceived; compare e.Source by origin".
//
// Pure function: the main process hands it what it read off the event, so
// the rule is pinned by test/ipc-guard.test.ts without a renderer. E1 has no
// channels (IPC_CHANNELS is empty) — the guard exists so that the first
// channel E3 adds cannot be registered without it.

export interface SenderInfo {
  /** event.senderFrame?.url — undefined/null when the frame is already gone. */
  readonly frameUrl: string | null | undefined;
  /** event.senderFrame === webContents.mainFrame */
  readonly isMainFrame: boolean;
}

export type SenderVerdict =
  | { readonly trusted: true }
  | { readonly trusted: false; readonly reason: "no-frame" | "sub-frame" | "unparseable" | "origin-not-allowed" };

export function isTrustedSender(sender: SenderInfo, allowedOrigins: ReadonlySet<string>): SenderVerdict {
  if (sender.frameUrl === null || sender.frameUrl === undefined || sender.frameUrl === "") {
    return { trusted: false, reason: "no-frame" };
  }
  // Only the top frame may speak to the host. Artifact iframes are sub-frames
  // of the same webContents; this is the line that keeps them out.
  if (!sender.isMainFrame) return { trusted: false, reason: "sub-frame" };

  let parsed: URL;
  try {
    parsed = new URL(sender.frameUrl);
  } catch {
    return { trusted: false, reason: "unparseable" };
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { trusted: false, reason: "origin-not-allowed" };
  }
  // Compared by ORIGIN via the URL parser — "https://evil.com/?x=https://good"
  // and "http://good@evil.com/" have origin evil.com.
  return allowedOrigins.has(parsed.origin)
    ? { trusted: true }
    : { trusted: false, reason: "origin-not-allowed" };
}
