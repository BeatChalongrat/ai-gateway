// Session persistence — Electron's safeStorage (DPAPI on Windows, Keychain on
// macOS; one API) replaces the WebView2 shell's DpapiCredentialStore. The
// ciphertext lives in userData/session.bin; the plaintext exists only in this
// process's memory. Never logged.

import { safeStorage } from "electron";
import fs from "node:fs";
import path from "node:path";
import type { SessionTokens } from "./portal-auth";

export interface StoredSession extends SessionTokens {
  readonly savedAt: string;
}

export class SessionStore {
  constructor(private readonly filePath: string) {}

  get path(): string {
    return this.filePath;
  }

  exists(): boolean {
    return fs.existsSync(this.filePath);
  }

  load(): StoredSession | null {
    if (!fs.existsSync(this.filePath)) return null;
    if (!safeStorage.isEncryptionAvailable()) throw new Error("safeStorage encryption is not available");
    const plain = safeStorage.decryptString(fs.readFileSync(this.filePath));
    const parsed = JSON.parse(plain) as Partial<StoredSession>;
    if (typeof parsed.accessToken !== "string" || typeof parsed.refreshToken !== "string") return null;
    return {
      accessToken: parsed.accessToken,
      refreshToken: parsed.refreshToken,
      userEmail: typeof parsed.userEmail === "string" ? parsed.userEmail : null,
      savedAt: typeof parsed.savedAt === "string" ? parsed.savedAt : "",
    };
  }

  save(tokens: SessionTokens): StoredSession {
    if (!safeStorage.isEncryptionAvailable()) throw new Error("safeStorage encryption is not available");
    const stored: StoredSession = { ...tokens, savedAt: new Date().toISOString() };
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    const tmp = `${this.filePath}.tmp`;
    fs.writeFileSync(tmp, safeStorage.encryptString(JSON.stringify(stored)));
    fs.renameSync(tmp, this.filePath);
    return stored;
  }

  clear(): void {
    try {
      fs.rmSync(this.filePath, { force: true });
    } catch {
      /* already gone */
    }
  }
}
