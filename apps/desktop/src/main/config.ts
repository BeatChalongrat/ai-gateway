// Shell configuration — the page URL, the allow-list and the portal origin
// come from a file, never from constants (E1 §2). Resolution order:
//   1. $SRAN_DESKTOP_CONFIG  (absolute path — per-environment override)
//   2. <app root>/config/desktop.config.json
// Validation is strict and happens once, before any window exists; a bad file
// is a startup failure, not a silently widened allow-list.
//
// No 'electron' import: test/navigation-policy.test.ts loads the real file
// through this module.

import fs from "node:fs";
import path from "node:path";
import { createNavigationPolicy, originOf, parseOriginEntry, type NavigationPolicy } from "./navigation-policy";

export interface ShellConfig {
  readonly pageUrl: string;
  readonly allowedOrigins: readonly string[];
  readonly portalOrigin: string | null;
  /** Identity authority: refresh / ticket exchange / logout, and the host
   * the session cookies are written for. Not allow-listed for navigation. */
  readonly gatewayUrl: string;
  /** chat-edge: the page's conversation store (history, transcript import,
   * rename, memories). Reached by the host with the portal token; not
   * allow-listed for navigation. */
  readonly chatEdgeUrl: string;
  /** Model when neither the page nor the conversation names one. */
  readonly defaultModel: string;
  /** E4: the tools sidecar exe (absolute, or relative to the app root). */
  readonly sidecarPath: string;
  /** E4: manual | auto | skip — the WPF app's ApprovalMode, same semantics. */
  readonly approvalMode: string;
  /** E4: web_search is listed only when true (the WPF default is off). */
  readonly webSearchEnabled: boolean;
  readonly protocolScheme: string;
  /** Backend-side client id that maps to protocolScheme (allow-listed there). */
  readonly oauthClient: string;
  readonly oauthProvider: string;
  /** Host rotation interval; must be strictly under the 60-minute access TTL. */
  readonly sessionRefreshMinutes: number;
  readonly signInTimeoutSeconds: number;
  readonly hotkey: string;
  readonly openAtLogin: boolean;
  readonly contentSecurityPolicy: string;
  /** Where it was read from — logged at startup so nobody guesses. */
  readonly sourcePath: string;
  readonly policy: NavigationPolicy;
}

export const CONFIG_ENV_VAR = "SRAN_DESKTOP_CONFIG";
const SCHEME_PATTERN = /^[a-z][a-z0-9+.-]*$/;

export function resolveConfigPath(appRoot: string, env: NodeJS.ProcessEnv = process.env): string {
  const override = env[CONFIG_ENV_VAR];
  if (override && override.trim() !== "") {
    if (!path.isAbsolute(override)) {
      throw new Error(`${CONFIG_ENV_VAR} must be an absolute path (got '${override}').`);
    }
    return override;
  }
  return path.join(appRoot, "config", "desktop.config.json");
}

export function loadConfig(configPath: string): ShellConfig {
  let raw: unknown;
  try {
    raw = JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch (err) {
    throw new Error(`Cannot read shell config '${configPath}': ${(err as Error).message}`);
  }
  return validateConfig(raw, configPath);
}

export function validateConfig(raw: unknown, sourcePath: string): ShellConfig {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error(`Shell config '${sourcePath}' must be a JSON object.`);
  }
  const obj = raw as Record<string, unknown>;

  const pageUrl = requireString(obj, "pageUrl", sourcePath);
  const allowedOrigins = obj["allowedOrigins"];
  if (!Array.isArray(allowedOrigins) || allowedOrigins.some((o) => typeof o !== "string")) {
    throw new Error(`'allowedOrigins' in '${sourcePath}' must be an array of strings.`);
  }
  const portalRaw = obj["portalOrigin"];
  if (portalRaw !== undefined && portalRaw !== null && typeof portalRaw !== "string") {
    throw new Error(`'portalOrigin' in '${sourcePath}' must be a string when present.`);
  }
  const policy = createNavigationPolicy(allowedOrigins as string[], (portalRaw as string | null | undefined) ?? null);

  const pageOrigin = originOf(pageUrl);
  if (pageOrigin === null || !policy.allowedOrigins.has(pageOrigin)) {
    throw new Error(`'pageUrl' ${pageUrl} is outside 'allowedOrigins' in '${sourcePath}'.`);
  }

  const protocolScheme = requireString(obj, "protocolScheme", sourcePath);
  if (!SCHEME_PATTERN.test(protocolScheme)) {
    throw new Error(`'protocolScheme' must match ${SCHEME_PATTERN} (got '${protocolScheme}').`);
  }
  if (protocolScheme === "sran") {
    // Registered to the WebView2 shell (Debug) since Phase 2 testing; the
    // production scheme is a cut-over decision, not something to grab now.
    throw new Error("'protocolScheme' must not be 'sran' in E1 — use a distinct dev scheme.");
  }

  // E2: the identity authority. An origin (validated like an allow-list
  // entry) — but it must NOT be allow-listed for navigation: the page
  // reaches it with fetch, never by navigating there.
  const gatewayUrl = parseOriginEntry(requireString(obj, "gatewayUrl", sourcePath), "gatewayUrl");
  if (policy.allowedOrigins.has(gatewayUrl)) {
    throw new Error(`'gatewayUrl' ${gatewayUrl} must not also be in 'allowedOrigins'.`);
  }
  const chatEdgeUrl = parseOriginEntry(requireString(obj, "chatEdgeUrl", sourcePath), "chatEdgeUrl");
  if (policy.allowedOrigins.has(chatEdgeUrl)) {
    throw new Error(`'chatEdgeUrl' ${chatEdgeUrl} must not also be in 'allowedOrigins'.`);
  }
  const defaultModel = requireString(obj, "defaultModel", sourcePath);
  const sidecarPath = requireString(obj, "sidecarPath", sourcePath);
  const approvalMode = requireString(obj, "approvalMode", sourcePath).toLowerCase();
  if (!/^(manual|auto|skip)$/.test(approvalMode)) {
    throw new Error(`'approvalMode' must be manual, auto or skip (got '${approvalMode}').`);
  }
  const webSearchEnabled = obj["webSearchEnabled"] === true;
  const oauthClient = requireString(obj, "oauthClient", sourcePath);
  if (!/^[a-z][a-z0-9-]*$/.test(oauthClient)) {
    throw new Error(`'oauthClient' must be a lower-case identifier (got '${oauthClient}').`);
  }
  if (oauthClient === "desktop" && protocolScheme !== "sran") {
    throw new Error("'oauthClient' desktop maps to sran:// on the backend; the scheme here does not match.");
  }
  const oauthProvider = requireString(obj, "oauthProvider", sourcePath);
  if (!/^(google|github)$/.test(oauthProvider)) {
    throw new Error(`'oauthProvider' must be google or github (got '${oauthProvider}').`);
  }
  // Strictly under the 60-minute access TTL (backend config.py:131). The
  // guard only knows "less than 60"; a shorter production TTL must move this.
  const sessionRefreshMinutes = requireNumber(obj, "sessionRefreshMinutes", sourcePath, 1, 59);
  const signInTimeoutSeconds = requireNumber(obj, "signInTimeoutSeconds", sourcePath, 30, 3600);

  const hotkey = requireString(obj, "hotkey", sourcePath);
  const openAtLogin = obj["openAtLogin"] === true;
  const contentSecurityPolicy = requireString(obj, "contentSecurityPolicy", sourcePath);

  return Object.freeze({
    pageUrl,
    allowedOrigins: Object.freeze([...policy.allowedOrigins]),
    portalOrigin: policy.portalOrigin,
    gatewayUrl,
    chatEdgeUrl,
    defaultModel,
    sidecarPath,
    approvalMode,
    webSearchEnabled,
    protocolScheme,
    oauthClient,
    oauthProvider,
    sessionRefreshMinutes,
    signInTimeoutSeconds,
    hotkey,
    openAtLogin,
    contentSecurityPolicy,
    sourcePath,
    policy,
  });
}

function requireNumber(obj: Record<string, unknown>, key: string, sourcePath: string, min: number, max: number): number {
  const value = obj[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max) {
    throw new Error(`'${key}' in '${sourcePath}' must be an integer in [${min}, ${max}] (got ${String(value)}).`);
  }
  return value;
}

function requireString(obj: Record<string, unknown>, key: string, sourcePath: string): string {
  const value = obj[key];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`'${key}' in '${sourcePath}' must be a non-empty string.`);
  }
  return value.trim();
}
