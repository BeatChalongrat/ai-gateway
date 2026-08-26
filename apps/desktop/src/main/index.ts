// SRAN Desktop — Electron shell. E1: the locked window. E2: the host owns
// the session. E3: the bridge and the first turn through the host.
//
// Boot order (E2): authenticate FIRST, navigate SECOND. Session state reaches
// the page as cookies. E3 adds the typed bridge (src/main/bridge.ts — the
// only file touching the bridge's IPC channels) and the chat-only turn
// (src/main/turn-coordinator.ts). No tools, no sidecar (E4).
//
// Diagnostics (dev only, refused when app.isPackaged):
//   --devtools / --e1-probe / --measure / --session-file / --exit-after
//   --probe-corrupt-access / --probe-collision / --probe-server-revoke
//   --probe-signout / --probe-page-logout / --probe-rotate-now / --probe-nonce-file   (E2)
//   --probe-e3 <containment|memory|five|artifact|cancel|reload|reject|logout>       (E3)

import {
  app,
  BrowserWindow,
  dialog,
  globalShortcut,
  Menu,
  nativeImage,
  Notification,
  powerMonitor,
  safeStorage,
  session,
  shell,
  Tray,
  type Session,
  type WebContents,
  type WebFrameMain,
} from "electron";
import fs from "node:fs";
import path from "node:path";
import { BridgeChannel } from "./bridge";
import { ChatEdgeClient } from "./chat-edge-client";
import { loadConfig, resolveConfigPath, type ShellConfig } from "./config";
import { parseIntent } from "./deep-link";
import { GatewayClient } from "./gateway-client";
import { GrantStore, defaultForbiddenRoots } from "./grants";
import { HostTools } from "./host-tools";
import { createLogger, redactUrl } from "./logger";
import { PermissionStore } from "./permissions";
import { runE4Probe } from "./probes-e4";
import { SidecarClient } from "./sidecar-client";
import { TaskScheduler } from "./task-scheduler";
import { classifyFrame, classifyTopLevel, isExternalHttpUrl } from "./navigation-policy";
import { PortalAuthClient, fingerprint } from "./portal-auth";
import { PromptAssembler } from "./prompt-assembler";
import { SessionController, type ViewState } from "./session-controller";
import { SessionStore } from "./session-store";
import { TurnCoordinator } from "./turn-coordinator";
import { buildWebPreferences } from "./web-preferences";
import { BRIDGE_SURFACE_MEMBERS } from "../shared/bridge-surface";

const APP_ROOT = path.resolve(__dirname, "..", "..");
const SESSION_VIEW = path.join(APP_ROOT, "assets", "session.html");
const log = createLogger();
const isRelease = app.isPackaged;
const flags = parseFlags(process.argv);
let isQuitting = false;
let mainWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let rendererCrashes = 0;
let sessionController: SessionController | null = null;
let bridge: BridgeChannel | null = null;
let coordinator: TurnCoordinator | null = null;
let sidecar: SidecarClient | null = null;
let scheduler: TaskScheduler | null = null;
let grants: GrantStore | null = null;

// ── identity (FIX20 §1): the app is SRAN Desktop, one client of SRAN AI
// Gateway. The AppUserModelId MUST equal electron-builder's appId — the
// installer stamps that id on the shortcuts, and Windows groups/pins the
// running window under the shortcut only when the two match.
// userData stays "SranDesktop-Electron" (not user-visible): "%APPDATA%\SRAN
// Desktop" still exists on machines that ran the retired WPF app and holds
// its incompatible data; renaming would also orphan every existing grant,
// connector and note. The installer keeps this folder on uninstall.
app.setName("SRAN Desktop");
app.setPath("userData", path.join(app.getPath("appData"), "SranDesktop-Electron"));
app.setAppUserModelId("co.th.gbtech.sran.desktop");

// ── config: file, never constants ───────────────────────────────────────────
// FIX20 §5: packaged → the file shipped beside the exe (<resources>\
// desktop.config.json); dev → the repo's config/desktop.config.json;
// $SRAN_DESKTOP_CONFIG overrides both.
let config: ShellConfig;
try {
  const configPath = resolveConfigPath(APP_ROOT, process.env, isRelease ? process.resourcesPath : null);
  config = loadConfig(configPath);
  log.info(`config loaded from ${configPath}`);
  log.info(
    `pageUrl=${config.pageUrl} allowedOrigins=[${config.allowedOrigins.join(", ")}] portalOrigin=${config.portalOrigin ?? "(none)"} gatewayUrl=${config.gatewayUrl} chatEdgeUrl=${config.chatEdgeUrl} scheme=${config.protocolScheme}:// oauthClient=${config.oauthClient} refreshEvery=${config.sessionRefreshMinutes}min defaultModel=${config.defaultModel}`,
  );
} catch (err) {
  const message = (err as Error).message;
  log.error(`FATAL config: ${message}`);
  dialog.showErrorBox("SRAN Desktop — configuration error", message);
  app.exit(2);
  throw err;
}

if (flags.anyDiagnostic && isRelease) {
  log.error("diagnostic flags are dev-only; refusing in a packaged build");
  app.exit(3);
}

// ── single instance + deep link ─────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  log.info("another instance holds the lock — forwarding argv and exiting");
  app.exit(0);
}
app.on("second-instance", (_event, argv) => {
  log.info("second-instance: focusing the existing window");
  showMainWindow();
  const link = findDeepLink(argv, config.protocolScheme);
  if (link) handleDeepLink(link);
});
registerDeepLinkScheme(config.protocolScheme);

// ── every webContents, whoever creates it ───────────────────────────────────
app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (event) => {
    event.preventDefault();
    log.warn("REFUSED <webview> attach (webviewTag is off; this is belt-and-braces)");
  });
  installNavigationGuards(contents);
  installCrashHandlers(contents);
});

app.on("child-process-gone", (_event, details) => {
  log.error(`CHILD PROCESS GONE type=${details.type} name=${details.name ?? "-"} reason=${details.reason} exitCode=${details.exitCode}`);
});
app.on("window-all-closed", () => {
  /* tray app */
});
app.on("before-quit", () => {
  isQuitting = true;
});
app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  scheduler?.stop();
  void sidecar?.stop();
});

// ── ready ───────────────────────────────────────────────────────────────────
void app.whenReady().then(async () => {
  log.attachFile(path.join(app.getPath("userData"), "logs", "shell.log"));
  log.info(`SRAN Desktop ${app.getVersion()} pid=${process.pid} electron=${process.versions.electron} chrome=${process.versions.chrome} node=${process.versions.node} packaged=${isRelease}`);
  log.info(`userData=${app.getPath("userData")}`);

  installSessionPolicy(session.defaultSession);
  createTray();
  registerHotkey();
  applyLoginItem();

  mainWindow = createMainWindow();
  const win = mainWindow;

  // E2: the session controller owns everything between "window exists" and
  // "page loaded". Its surface is the window; nothing else may touch the jar.
  const store = new SessionStore(path.join(app.getPath("userData"), "session.bin"));
  sessionController = new SessionController(
    {
      gatewayUrl: config.gatewayUrl,
      oauthProvider: config.oauthProvider,
      oauthClient: config.oauthClient,
      scheme: config.protocolScheme,
      refreshMinutes: config.sessionRefreshMinutes,
      signInTimeoutSeconds: config.signInTimeoutSeconds,
      ...(flags.nonceFile && !isRelease ? { nonceExportPath: flags.nonceFile } : {}),
    },
    store,
    session.defaultSession.cookies,
    new PortalAuthClient(config.gatewayUrl),
    {
      showView: (state, reason) => showSessionView(win, state, reason),
      loadPage: () => win.loadURL(config.pageUrl),
      reloadPage: () => win.webContents.reload(),
      blankPage: () => win.loadURL("about:blank"),
      openExternal: (url) => shell.openExternal(url),
    },
    log,
  );
  const ctl = sessionController;

  // E3: the bridge and the turn. The bridge is the only IPC surface; the
  // coordinator is the only thing that speaks to the gateway for turns.
  bridge = new BridgeChannel(config.policy.allowedOrigins, log);
  const keyStore = new SafeKeyStore(path.join(app.getPath("userData"), "agent-key.bin"));
  const gateway = new GatewayClient(config.gatewayUrl, () => ctl.currentAccessToken(), keyStore, log);
  const chatEdge = new ChatEdgeClient(config.chatEdgeUrl, () => ctl.currentAccessToken(), log);
  const assembler = new PromptAssembler(chatEdge, log);
  // E4: the sidecar (all local tools), host tools, consent + grants.
  const dataDir = app.getPath("userData");
  // FIX19 — two-mode sidecar resolution (the same dev/packaged split FIX17
  // used for the sandbox runtime):
  //   • PACKAGED: the installed app carries its own sidecar in
  //     <resources>\sidecar\; there is no repo beside it, so config.sidecarPath
  //     (a dev, repo-relative path) is IGNORED. BrowserHost.exe + the Chrome
  //     extension bundle sit in that same folder, which is where the sidecar
  //     looks for them (its own AppContext.BaseDirectory).
  //   • DEV (npm start): honour config.sidecarPath exactly as before, so a
  //     developer runs from the repo without editing anything.
  const sidecarExe = isRelease
    ? path.join(process.resourcesPath, "sidecar", "SranDesktop.ToolsSidecar.exe")
    : path.isAbsolute(config.sidecarPath)
      ? config.sidecarPath
      : path.resolve(APP_ROOT, config.sidecarPath);
  log.info(`SIDECAR resolved (${isRelease ? "packaged" : "dev"}): ${sidecarExe}`);
  sidecar = new SidecarClient(sidecarExe, dataDir, log);
  const permissions = new PermissionStore(path.join(dataDir, "permissions.json"));
  grants = new GrantStore(path.join(dataDir, "grants.json"), { forbiddenRoots: defaultForbiddenRoots(process.env, dataDir) });
  if (flags.grant) {
    const v = grants.add(flags.grant);
    log.info(`PROBE grant ${v.verdict}: ${flags.grant}`);
  }
  log.info(`grants: [${grants.list().join(", ")}] approvalMode=${config.approvalMode} webSearch=${config.webSearchEnabled}`);
  const hostTools = new HostTools(gateway, chatEdge, (req, sig) => coordinator!.runNested(req, sig), log, () => config.webSearchEnabled);
  coordinator = new TurnCoordinator(
    bridge,
    gateway,
    chatEdge,
    assembler,
    sidecar,
    hostTools,
    permissions,
    grants,
    {
      homeDirectory: app.getPath("home"),
      dataDir,
      defaultModel: config.defaultModel,
      ...(flags.model && !isRelease ? { forceModel: flags.model } : {}),
      approvalMode: config.approvalMode,
      signOut: () => ctl.signOut("bridge", true),
      pickFolder: async () => {
        const r = await dialog.showOpenDialog(win, { title: "Grant SRAN access to a folder", properties: ["openDirectory", "dontAddToRecent"] });
        return r.canceled || r.filePaths.length === 0 ? null : r.filePaths[0]!;
      },
      // FIX2 native dialogs — the sidecar is headless, so windows live here.
      pickSkillFile: async () => {
        const r = await dialog.showOpenDialog(win, {
          title: "Choose a skill package",
          properties: ["openFile", "dontAddToRecent"],
          filters: [{ name: "Skill package", extensions: ["zip", "skill"] }],
        });
        return r.canceled || r.filePaths.length === 0 ? null : r.filePaths[0]!;
      },
      pickExecutableFile: async () => {
        const r = await dialog.showOpenDialog(win, {
          title: "Choose the MCP server program (the user picks it — the page names no process)",
          properties: ["openFile", "dontAddToRecent"],
        });
        return r.canceled || r.filePaths.length === 0 ? null : r.filePaths[0]!;
      },
      confirmSpawn: async (message: string) => {
        const r = await dialog.showMessageBox(win, {
          type: "warning",
          title: "Add MCP connector",
          message,
          buttons: ["Cancel", "Add connector"],
          defaultId: 0,
          cancelId: 0,
        });
        return r.response === 1;
      },
      // FIX2 §1.2: the OS side-effect of a host-owned setting, derived from the
      // one value the sidecar just persisted (no second copy to drift).
      applyHostSetting: (key: string, value: unknown) => {
        if (key === "startWithWindows") {
          try {
            app.setLoginItemSettings({ openAtLogin: value === true });
            log.info(`SETTINGS startWithWindows → login item ${value === true ? "on" : "off"}`);
          } catch (err) {
            log.warn(`setLoginItemSettings failed: ${(err as Error).message}`);
          }
        }
        // minimizeToTray is read from settings at window-close time; no effect to apply now.
      },
    },
    log,
  );
  const co = coordinator;
  void sidecar
    .ensureStarted()
    .then(async () => {
      // §1.2: reconcile the OS login item to the ONE stored value (no drift).
      try {
        const s = await sidecar!.hostQuery("settings.get", {});
        const startup = (s.data as { startWithWindows?: boolean } | undefined)?.startWithWindows;
        if (typeof startup === "boolean") app.setLoginItemSettings({ openAtLogin: startup });
      } catch {
        /* best-effort */
      }
    })
    .catch((err: Error) => log.error(`sidecar start failed: ${err.message}`));
  // FIX2 §1.1: the scheduled-task firing timer lives HERE (the host has the turn
  // engine + session). Fires only while the app runs; no-ops while signed out.
  if (!flags.e1Probe && !flags.e3Probe && !flags.e4Probe && !flags.measure) {
    // FIX8 A1.1: notify the user when a MISSED run is caught up (the app may be
    // minimized to the tray). OS-native so it shows without the window focused.
    const notifyCatchUp = (task: { name: string; catchUpFor: string; skippedOlder: number }) => {
      try {
        if (Notification.isSupported()) {
          new Notification({
            title: "Scheduled task caught up",
            body: `“${task.name}” was due while the app was closed — running it now (missed ${new Date(task.catchUpFor).toLocaleString()}${task.skippedOlder > 0 ? `; ${task.skippedOlder} older run(s) skipped` : ""}).`,
          }).show();
        }
      } catch (err) {
        log.warn(`catch-up notification failed: ${(err as Error).message}`);
      }
    };
    const localScheduler = new TaskScheduler(sidecar, co, () => !!ctl.currentAccessToken(), log, notifyCatchUp);
    scheduler = localScheduler;
    scheduler.start();
    // FIX8 A1.1: also catch up after the MACHINE wakes from sleep, not just app start.
    powerMonitor.on("resume", () => {
      log.info("SCHEDULER: machine resumed — running the missed-run catch-up");
      void localScheduler.catchUpMissed();
    });
  }
  bridge.attach(win, {
    onTurnSubmit: (p) => co.onTurnSubmit(p),
    onTurnCancel: (p) => co.onTurnCancel(p),
    onConsentRespond: (p) => co.onConsentRespond(p),
    onUiReady: (p) => co.onUiReady(p),
    onHostQuery: (p) => void co.onHostQuery(p),
  });

  if (flags.sessionFile) {
    const json = JSON.parse(fs.readFileSync(flags.sessionFile, "utf8")) as { access_token: string; refresh_token: string; user?: { email?: string } };
    await ctl.probeInjectFromFile(json);
  }

  showSessionView(win, "signing-in");

  if (flags.e1Probe) {
    await win.loadURL(config.pageUrl).catch((err: Error) => log.warn(`initial load rejected: ${err.message}`));
    await runE1Probe(win);
    return;
  }

  const initialLink = findDeepLink(process.argv, config.protocolScheme);
  const ready = await ctl.ensureSessionAtBoot();
  if (ready) {
    ctl.startRotation();
    if (flags.measure) runMeasure(win);
    await win.loadURL(config.pageUrl).catch((err: Error) => log.warn(`initial load rejected: ${err.message}`));
  } else if (flags.measure || flags.e3Probe) {
    log.error("this probe needs a stored session (use --session-file); exiting");
    app.exit(4);
  }
  if (initialLink) handleDeepLink(initialLink);

  scheduleProbes(win, ctl);
  if (flags.e3Probe) void runE3Probe(win, ctl, co, chatEdge, flags.e3Probe);
  if (flags.e4Probe && ready) {
    void runE4Probe(
      {
        win,
        bridge: bridge!,
        coordinator: co,
        sidecar: sidecar!,
        grants: grants!,
        log,
        evidenceDir: path.join(dataDir, "e4-evidence"),
        homeDir: app.getPath("home"),
        quit: (code) => {
          isQuitting = true;
          void sidecar?.stop().finally(() => app.exit(code));
        },
      },
      flags.e4Probe,
    );
  }
});

// ── safeStorage-backed store for the agent gateway key ──────────────────────
class SafeKeyStore {
  constructor(private readonly filePath: string) {}
  load(): string | null {
    try {
      if (!fs.existsSync(this.filePath) || !safeStorage.isEncryptionAvailable()) return null;
      return safeStorage.decryptString(fs.readFileSync(this.filePath)) || null;
    } catch {
      return null;
    }
  }
  save(key: string): void {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    fs.writeFileSync(this.filePath, safeStorage.encryptString(key));
  }
  clear(): void {
    fs.rmSync(this.filePath, { force: true });
  }
}

// ── window ──────────────────────────────────────────────────────────────────
function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1200,
    height: 820,
    minWidth: 640,
    minHeight: 480,
    show: false,
    title: "SRAN Desktop",
    icon: path.join(APP_ROOT, "assets", "app.ico"),
    autoHideMenuBar: true,
    webPreferences: buildWebPreferences({
      preloadPath: path.join(__dirname, "..", "preload", "index.js"),
      isRelease,
    }),
  });
  Menu.setApplicationMenu(null);

  // FIX20 §1: the OS window title is the app's identity (taskbar, Alt-Tab), so
  // it stays "SRAN Desktop". By default Electron overwrites it with each
  // page's <title> — the hosted page is "SRAN Chat", a different product — so
  // refuse the page-driven update and keep the app name.
  win.on("page-title-updated", (event) => {
    event.preventDefault();
    if (!win.isDestroyed() && win.getTitle() !== "SRAN Desktop") win.setTitle("SRAN Desktop");
  });

  win.once("ready-to-show", () => {
    win.show();
    if (flags.devtools && !isRelease) win.webContents.openDevTools({ mode: "detach" });
  });
  win.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      win.hide();
      log.info("window hidden to tray");
    }
  });
  win.on("closed", () => {
    mainWindow = null;
  });
  win.webContents.on("did-finish-load", () => {
    const url = win.webContents.getURL();
    log.info(`did-finish-load ${redactUrl(url)}`);
    if (url.startsWith(config.pageUrl.slice(0, config.pageUrl.indexOf("/", 8)))) void reportPageState(win);
  });
  win.webContents.on("did-fail-load", (_e, code, description, url, isMainFrame) => {
    if (isMainFrame && code !== -3) log.error(`did-fail-load ${code} ${description} ${redactUrl(url)}`);
  });
  win.webContents.on("console-message", (_e, level, message) => {
    if (message.includes("Content Security Policy")) log.warn(`CSP (renderer console): ${message.slice(0, 300)}`);
    else if (message.includes("[auth]") || message.includes("[e3-probe]") || message.includes("[canvas-probe]")) log.info(`page.console: ${message.slice(0, 400)}`);
    else if (level >= 2) log.info(`renderer console [${level}]: ${message.slice(0, 200)}`);
  });
  return win;
}

function showSessionView(win: BrowserWindow, state: ViewState, reason?: string): void {
  if (win.isDestroyed()) return;
  const query: Record<string, string> = { state, scheme: config.protocolScheme };
  if (reason) query["reason"] = reason;
  log.info(`VIEW: ${state}${reason ? ` (${reason})` : ""}`);
  void win.loadFile(SESSION_VIEW, { query }).catch((err: Error) => log.warn(`session view load rejected: ${err.message}`));
}

async function reportPageState(win: BrowserWindow): Promise<void> {
  await sleep(1500);
  if (win.isDestroyed()) return;
  try {
    const state = (await win.webContents.executeJavaScript(
      `JSON.stringify({ path: location.pathname, composer: !!document.querySelector('textarea'), title: document.title, bodyChars: document.body.innerText.length })`,
    )) as string;
    log.info(`PAGE STATE: ${state}`);
  } catch (err) {
    log.warn(`PAGE STATE unavailable: ${(err as Error).message}`);
  }
}

function showMainWindow(): void {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}
function toggleMainWindow(): void {
  if (!mainWindow) return;
  if (mainWindow.isVisible() && mainWindow.isFocused()) {
    mainWindow.hide();
    log.info("hotkey: window hidden");
  } else {
    showMainWindow();
    log.info("hotkey: window shown");
  }
}

// ── navigation locks ────────────────────────────────────────────────────────
function installNavigationGuards(contents: WebContents): void {
  const policy = config.policy;
  contents.on("will-navigate", (event, url) => {
    const intent = parseIntent(url, config.protocolScheme);
    if (intent !== null) {
      event.preventDefault();
      if (!contents.getURL().startsWith("file://")) {
        log.warn(`INTENT DROPPED (${intent}) — not from the shell-owned view: ${redactUrl(contents.getURL())}`);
        return;
      }
      log.info(`INTENT from the signed-out view: ${intent}`);
      if (intent === "signin") void sessionController?.signInFromView();
      else sessionController?.cancelSignIn(true);
      return;
    }
    const verdict = classifyTopLevel(policy, url);
    if (verdict.allowed) {
      log.info(`navigation allowed: ${redactUrl(url)}`);
      return;
    }
    event.preventDefault();
    log.warn(`NAVIGATION REFUSED (${verdict.reason}): ${redactUrl(url)}`);
    if (verdict.reason === "portal-reauth") void sessionController?.onPortalRedirect(redactUrl(url));
  });
  contents.on("will-redirect", (event, url) => {
    const verdict = classifyTopLevel(policy, url);
    if (verdict.allowed) {
      log.info(`redirect allowed: ${redactUrl(url)}`);
      return;
    }
    event.preventDefault();
    log.warn(`REDIRECT REFUSED (${verdict.reason}): ${redactUrl(url)}`);
    if (verdict.reason === "portal-reauth") void sessionController?.onPortalRedirect(redactUrl(url));
  });
  contents.on("will-frame-navigate", (details) => {
    if (details.isMainFrame) return;
    const verdict = classifyFrame(policy, details.url);
    if (verdict.allowed) return;
    details.preventDefault();
    log.warn(`FRAME NAVIGATION REFUSED (${verdict.reason}): ${redactUrl(details.url)}`);
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (isExternalHttpUrl(url)) {
      log.info(`window.open → system browser: ${redactUrl(url)}`);
      void shell.openExternal(url);
    } else {
      log.warn(`window.open DROPPED (non-http scheme): ${redactUrl(url)}`);
    }
    return { action: "deny" };
  });
}

// ── session policy: CSP, permissions, downloads, logout observation ─────────
function installSessionPolicy(ses: Session): void {
  const allowed = config.policy.allowedOrigins;
  ses.webRequest.onHeadersReceived({ urls: ["http://*/*", "https://*/*"] }, (details, callback) => {
    const isDocument = details.resourceType === "mainFrame" || details.resourceType === "subFrame";
    let origin: string | null = null;
    try {
      origin = new URL(details.url).origin;
    } catch {
      /* not ours */
    }
    if (!isDocument || origin === null || !allowed.has(origin)) {
      callback({});
      return;
    }
    const headers = { ...details.responseHeaders };
    headers["Content-Security-Policy"] = [...(headers["Content-Security-Policy"] ?? []), config.contentSecurityPolicy];
    callback({ responseHeaders: headers });
  });
  // E2 fallback (E3 §2 keeps it): the page's own POST /portal/auth/logout,
  // observed here, makes the bounce that follows a logout, not a re-auth.
  ses.webRequest.onCompleted({ urls: [`${config.gatewayUrl}/portal/auth/logout`] }, (details) => {
    if (details.method === "POST" && details.statusCode === 200) sessionController?.markLogoutIntent();
  });
  ses.setPermissionRequestHandler((_contents, permission, callback) => {
    log.warn(`PERMISSION REFUSED: ${permission}`);
    callback(false);
  });
  ses.setPermissionCheckHandler(() => false);
  ses.on("will-download", (event, item) => {
    event.preventDefault();
    log.warn(`DOWNLOAD REFUSED: ${redactUrl(item.getURL())}`);
  });
}

// ── crash handling ──────────────────────────────────────────────────────────
function installCrashHandlers(contents: WebContents): void {
  contents.on("render-process-gone", (_event, details) => {
    rendererCrashes += 1;
    log.error(`RENDERER GONE #${rendererCrashes} reason=${details.reason} exitCode=${details.exitCode}`);
    const win = BrowserWindow.fromWebContents(contents);
    if (!win || win.isDestroyed()) return;
    if (details.reason === "clean-exit") return;
    if (flags.e1Probe || rendererCrashes <= 1) {
      log.info("recovering: reloading the page");
      setTimeout(() => {
        if (!win.isDestroyed()) void win.loadURL(config.pageUrl);
      }, 300);
      return;
    }
    void dialog
      .showMessageBox(win, {
        type: "error",
        title: "SRAN Desktop",
        message: "The page process stopped unexpectedly.",
        detail: `reason=${details.reason} exitCode=${details.exitCode}\nLog: ${log.filePath ?? "(stdout)"}`,
        buttons: ["Reload", "Quit"],
        defaultId: 0,
        cancelId: 1,
      })
      .then(({ response }) => {
        if (response === 0) void win.loadURL(config.pageUrl);
        else app.quit();
      });
  });
}

// ── tray / hotkey / login item / deep link ──────────────────────────────────
function createTray(): void {
  const icon = nativeImage.createFromPath(path.join(APP_ROOT, "assets", "app.ico"));
  tray = new Tray(icon);
  tray.setToolTip("SRAN Desktop");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Show / Hide", click: () => toggleMainWindow() },
      { type: "separator" },
      { label: "Sign in", click: () => void sessionController?.signInFromView() },
      { label: "Sign out", click: () => void sessionController?.signOut("tray", true) },
      { type: "separator" },
      {
        label: "Quit",
        click: () => {
          isQuitting = true;
          app.quit();
        },
      },
    ]),
  );
  tray.on("click", () => toggleMainWindow());
  log.info("tray created");
}
function registerHotkey(): void {
  const ok = globalShortcut.register(config.hotkey, toggleMainWindow);
  if (ok) log.info(`global hotkey registered: ${config.hotkey}`);
  else log.warn(`global hotkey NOT registered (held by another app?): ${config.hotkey}`);
}
function applyLoginItem(): void {
  const current = app.getLoginItemSettings();
  if (current.openAtLogin !== config.openAtLogin) {
    app.setLoginItemSettings({ openAtLogin: config.openAtLogin });
    log.info(`login item set: openAtLogin=${config.openAtLogin}`);
  } else {
    log.info(`login item unchanged: openAtLogin=${current.openAtLogin}`);
  }
}
function registerDeepLinkScheme(scheme: string): void {
  const ok = isRelease
    ? app.setAsDefaultProtocolClient(scheme)
    : app.setAsDefaultProtocolClient(scheme, process.execPath, [path.resolve(process.argv[1] ?? APP_ROOT)]);
  log.info(`deep link scheme ${scheme}:// ${ok ? "registered" : "NOT registered"}`);
}
function findDeepLink(argv: readonly string[], scheme: string): string | null {
  return argv.find((a) => a.toLowerCase().startsWith(`${scheme}://`)) ?? null;
}
function handleDeepLink(url: string): void {
  log.info(`DEEP LINK received: ${redactUrl(url)}`);
  showMainWindow();
  void sessionController?.handleDeepLink(url);
}

// ── dev diagnostics ─────────────────────────────────────────────────────────
interface Flags {
  devtools: boolean;
  e1Probe: boolean;
  measure: boolean;
  sessionFile: string | null;
  exitAfter: number | null;
  corruptAccess: number | null;
  collision: boolean;
  serverRevoke: number | null;
  signout: number | null;
  pageLogout: number | null;
  rotateNow: number | null;
  nonceFile: string | null;
  e3Probe: string | null;
  e4Probe: string | null;
  /** dev: add a folder grant without the native picker */
  grant: string | null;
  /** dev: force every turn onto one model (the S2 auto-routing defect) */
  model: string | null;
  anyDiagnostic: boolean;
}
function parseFlags(argv: readonly string[]): Flags {
  const valueOf = (name: string): string | null => {
    const i = argv.indexOf(name);
    return i >= 0 && i + 1 < argv.length ? argv[i + 1]! : null;
  };
  const msOf = (name: string): number | null => {
    const v = valueOf(name);
    return v === null ? null : Number.parseInt(v, 10);
  };
  const f: Omit<Flags, "anyDiagnostic"> = {
    devtools: argv.includes("--devtools"),
    e1Probe: argv.includes("--e1-probe"),
    measure: argv.includes("--measure"),
    sessionFile: valueOf("--session-file"),
    exitAfter: msOf("--exit-after"),
    corruptAccess: msOf("--probe-corrupt-access"),
    collision: argv.includes("--probe-collision"),
    serverRevoke: msOf("--probe-server-revoke"),
    signout: msOf("--probe-signout"),
    pageLogout: msOf("--probe-page-logout"),
    rotateNow: msOf("--probe-rotate-now"),
    nonceFile: valueOf("--probe-nonce-file"),
    e3Probe: valueOf("--probe-e3"),
    e4Probe: valueOf("--probe-e4"),
    grant: valueOf("--grant"),
    model: valueOf("--model"),
  };
  const anyDiagnostic = Object.values(f).some((v) => v !== null && v !== false);
  return { ...f, anyDiagnostic };
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function summaryLine(): string {
  const refusals = log.lines.filter((l) => /(NAVIGATION|REDIRECT|FRAME NAVIGATION) REFUSED/.test(l)).length;
  return `E2_SUMMARY ${JSON.stringify({ refusals, rotations: sessionController?.rotations ?? 0, hasSession: sessionController?.hasSession ?? false, logFile: log.filePath })}`;
}
function quitWithSummary(code = 0): void {
  log.info(summaryLine());
  process.stdout.write(summaryLine() + "\n");
  isQuitting = true;
  app.exit(code);
}

function scheduleProbes(win: BrowserWindow, ctl: SessionController): void {
  const at = (ms: number | null, fn: () => void | Promise<void>) => {
    if (ms !== null) setTimeout(() => void fn(), ms);
  };
  at(flags.rotateNow, async () => {
    log.info("PROBE: host rotation on demand");
    await ctl.rotate("probe");
  });
  at(flags.corruptAccess, async () => {
    log.info("PROBE: corrupting the jar's access cookie (simulated invalid access token), then reloading");
    await ctl.probeCorruptAccessCookie();
  });
  at(flags.signout, async () => {
    log.info("PROBE: host-initiated sign-out (the tray's Sign out)");
    await ctl.signOut("probe", true);
  });
  at(flags.pageLogout, async () => {
    log.info("PROBE: page-side logout (web-chat's browser path: POST /portal/auth/logout then location.href = PORTAL/login)");
    await win.webContents.executeJavaScript(
      `fetch(${JSON.stringify(config.gatewayUrl)} + '/portal/auth/logout', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json; charset=utf-8' } })
         .then(r => { console.log('[e2-probe] logout status=' + r.status); location.href = ${JSON.stringify(config.portalOrigin)} + '/login'; })`,
    );
  });
  at(flags.serverRevoke, async () => {
    const refresh = ctl.currentRefreshToken();
    if (refresh === null) return;
    log.info(`PROBE: server-side revocation of the session's refresh token (sha256/8=${fingerprint(refresh)}) via POST /portal/auth/logout from the host`);
    const r = await new PortalAuthClient(config.gatewayUrl).logout(refresh);
    log.info(`PROBE: revocation status=${r.status}`);
    await sleep(500);
    await ctl.probeCorruptAccessCookie();
  });
  if (flags.collision) void runCollisionProbe(win, ctl);
  at(flags.exitAfter, () => quitWithSummary(0));
}

async function runCollisionProbe(win: BrowserWindow, ctl: SessionController): Promise<void> {
  const GRACE_MS = 31_000;
  log.info("=== COLLISION PROBE (criterion 4) ===");
  await sleep(4000);
  log.info(`PROBE A: waiting ${GRACE_MS / 1000}s (> 30 s refresh grace) with the page idle, then a host rotation`);
  await sleep(GRACE_MS);
  const a = await ctl.rotate("collision-probe-A");
  log.info(`PROBE A RESULT: host rotation ${a ? "200 — no second holder consumed the token" : "FAILED"}`);
  log.info("PROBE B (positive control): page-context fetch('/portal/auth/refresh') — the second holder the spike had");
  const pageStatus = await win.webContents.executeJavaScript(
    `fetch(${JSON.stringify(config.gatewayUrl)} + '/portal/auth/refresh', { method: 'POST', credentials: 'include' }).then(r => r.status)`,
  );
  log.info(`PROBE B: page-side refresh status=${String(pageStatus)} (the jar's refresh cookie is now rotated; the host's copy is stale)`);
  await sleep(GRACE_MS);
  const before = ctl.hasSession;
  const b = await ctl.rotate("collision-probe-B");
  log.info(`PROBE B RESULT: host rotation ${b ? "200 (unexpected)" : `rejected — hasSession ${before} → ${ctl.hasSession} (the spike's step-7 outcome, reproduced on demand)`}`);
  log.info("=== COLLISION PROBE DONE ===");
  quitWithSummary(a && !b ? 0 : 1);
}

// ── E3 probes ───────────────────────────────────────────────────────────────
type Check = { name: string; pass: boolean; evidence: string };

/** Type into web-chat's composer and press Enter — the page's own send path
 * (ChatInput.onKeyDown → shouldSendOnEnter → submit). DOM automation inside
 * the page, never OS input. */
const TYPE_AND_SEND = (text: string) => `(() => {
  const ta = document.querySelector('textarea');
  if (!ta) return 'no-textarea';
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, ${JSON.stringify(text)});
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.focus();
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
  return 'sent';
})()`;

const PAGE_TAIL = (n: number) => `document.body.innerText.replace(/\\s+/g, ' ').slice(-${n})`;

async function runE3Probe(win: BrowserWindow, ctl: SessionController, co: TurnCoordinator, chatEdge: ChatEdgeClient, name: string): Promise<void> {
  const checks: Check[] = [];
  const check = (n: string, pass: boolean, evidence: string) => {
    checks.push({ name: n, pass, evidence });
    log.info(`[${pass ? "PASS" : "FAIL"}] ${n} — ${evidence}`);
  };
  const wc = win.webContents;
  const js = (code: string) => wc.executeJavaScript(code) as Promise<unknown>;
  const lastLine = (re: RegExp) => [...log.lines].reverse().find((l) => re.test(l)) ?? "(no log line)";
  const b = bridge!;

  /** Send a turn through the composer, wait for the host to finish it,
   * return {convId, reply, dom}. */
  const turn = async (text: string, timeoutMs = 90_000): Promise<{ convId: string; reply: string; dom: string }> => {
    const accepted = b.accepted.length;
    const sent = await js(TYPE_AND_SEND(text));
    if (sent !== "sent") throw new Error(`composer not found (${String(sent)})`);
    const t0 = Date.now();
    while (b.accepted.length === accepted || !b.accepted.slice(accepted).some((a) => a.type === "turn.submit")) {
      if (Date.now() - t0 > 15_000) throw new Error("turn.submit never arrived at the host");
      await sleep(100);
    }
    const convId = co.lastConversationId!;
    const deadline = Date.now() + timeoutMs;
    while (co.isRunning(convId) && Date.now() < deadline) await sleep(100);
    await sleep(1200); // let the page render the final delta
    const dom = (await js(PAGE_TAIL(600))) as string;
    return { convId, reply: co.lastReply.get(convId) ?? "", dom };
  };

  const finish = () => {
    const failed = checks.filter((c) => !c.pass).length;
    log.info(`=== E3 PROBE ${name}: ${checks.length - failed}/${checks.length} passed ===`);
    process.stdout.write(`E3_PROBE ${JSON.stringify({ name, checks, logFile: log.filePath })}\n`);
    isQuitting = true;
    app.exit(failed === 0 ? 0 : 1);
  };

  try {
    // Wait for the page + the handshake.
    const t0 = Date.now();
    while (!log.lines.some((l) => /BRIDGE HANDSHAKE/.test(l)) && Date.now() - t0 < 20_000) await sleep(200);
    check("bridge handshake (ui.ready, nonce verified) arrived from the top document", log.lines.some((l) => /BRIDGE HANDSHAKE/.test(l)), lastLine(/BRIDGE HANDSHAKE/));
    await sleep(800);

    switch (name) {
      case "memory": {
        const number = String(100000 + Math.floor(Math.random() * 900000));
        log.info(`PROBE memory: random 6-digit number generated at test time = ${number}`);
        const t1 = await turn(`Please remember this number: ${number}. Reply with just "OK".`);
        check("turn 1 completed", t1.reply.length > 0, `reply="${t1.reply.slice(0, 60)}"`);
        const t2 = await turn("What number did I ask you to remember? Reply with only the digits.");
        check("turn 2 recalls the number", t2.reply.includes(number), `reply="${t2.reply.slice(0, 80)}" expected=${number}`);
        check("the number is on screen", t2.dom.includes(number), `page tail: …${t2.dom.slice(-160)}`);
        await sleep(6000);
        const conv = await chatEdge.getConversation(t1.convId);
        const sidebar = (await js(`document.body.innerText.includes(${JSON.stringify(conv?.title ?? "\u0000")})`)) as boolean;
        check("conversation has a title on chat-edge and it is in the sidebar", !!conv?.title && !/^new chat$/i.test(conv.title) && sidebar, `title="${conv?.title ?? "(none)"}" inSidebarDom=${sidebar} ${lastLine(/TURN TITLE/)}`);
        break;
      }
      case "five": {
        const word = ["tangerine", "cobalt", "viridian", "saffron", "magenta"][Math.floor(Math.random() * 5)]! + "-" + Math.floor(Math.random() * 1000);
        log.info(`PROBE five: turn-1 fact = favourite colour "${word}"`);
        await turn(`My favourite colour is "${word}". Reply with just "Noted".`);
        await turn("Name one planet. One word only.");
        await turn("What is 7 times 8? Digits only.");
        await turn("Say a short greeting in Thai.");
        const t5 = await turn("What did I tell you my favourite colour was, in turn one? Reply with only that colour.");
        check("turn 5 references turn 1", t5.reply.toLowerCase().includes(word.toLowerCase()), `reply="${t5.reply.slice(0, 80)}" expected="${word}"`);
        check("five turns of history went out on turn 5", /TURN OUTBOUND[^\n]*: 10 msg/.test(lastLine(/TURN OUTBOUND/)), lastLine(/TURN OUTBOUND/).slice(30, 260));
        break;
      }
      case "artifact":
      case "containment": {
        const t1 = await turn(
          'Return a complete, minimal HTML page as a single ```html fenced code block: a <h1> reading "E3 containment probe", one <button> that changes the heading colour when clicked, and a tiny inline <script>. No explanation, only the code block.',
        );
        check("HTML artifact turn returned a ```html block", /```html/i.test(t1.reply), `reply starts: ${t1.reply.slice(0, 80).replace(/\n/g, "\\n")}`);
        await sleep(800);
        // web-chat may auto-open the canvas when the artifact arrives; only
        // click an "Open in Canvas" affordance, never "Close canvas".
        const opened = await js(`(() => { if (document.querySelector('iframe[data-testid="canvas-frame"]')) return 'already-open'; const b = [...document.querySelectorAll('button')].find(x => { const t = (x.textContent || '') + ' ' + (x.getAttribute('aria-label') || '') + ' ' + (x.title || ''); return /canvas/i.test(t) && !/close/i.test(t); }); if (!b) return 'no-button'; b.click(); return 'clicked:' + (b.textContent || b.getAttribute('aria-label')); })()`);
        await sleep(2500);
        const frames = wc.mainFrame.framesInSubtree.filter((f) => f !== wc.mainFrame);
        const canvas = frames.find((f) => f.url === "about:srcdoc") ?? null;
        const domFrames = (await js(`JSON.stringify([...document.querySelectorAll('iframe')].map(f => ({ sandbox: f.getAttribute('sandbox'), hasSrcdoc: f.hasAttribute('srcdoc'), testid: f.getAttribute('data-testid') })))`)) as string;
        check("an executable canvas frame is mounted (srcdoc, sandbox=allow-scripts)", canvas !== null && /"sandbox":"allow-scripts"/.test(domFrames), `open=${String(opened)} iframes=${domFrames} childFrames=${frames.map((f) => f.url).join(",")}`);
        if (canvas) {
          // Prove WHICH frame we are in, then attempt every path to the host.
          const inside = (await runInFrame(canvas, `(async () => {
            const r = { href: location.href, body: (document.body ? document.body.innerHTML : '').slice(0, 160), attempts: {} };
            const tryIt = (k, fn) => { try { const v = fn(); r.attempts[k] = 'no-throw:' + String(v).slice(0, 60); } catch (e) { r.attempts[k] = 'threw:' + (e && e.message ? e.message : String(e)).slice(0, 80); } };
            tryIt('typeof window.sranHost', () => typeof window.sranHost);
            tryIt('window.sranHost.postMessage', () => window.sranHost.postMessage({ nonce: 'x', type: 'ui.ready', payload: { from: 'canvas-child' } }));
            tryIt('parent.sranHost.postMessage', () => parent.sranHost.postMessage({ nonce: 'x', type: 'ui.ready', payload: { from: 'canvas-child' } }));
            tryIt('top.sranHost', () => top.sranHost);
            tryIt('parent.document', () => parent.document.title);
            tryIt('typeof require', () => typeof require);
            tryIt('typeof process', () => typeof process);
            tryIt('typeof ' + ${JSON.stringify("ipc" + "Renderer")}, () => typeof window[${JSON.stringify("ipc" + "Renderer")}]);
            tryIt('parent.postMessage (page-level, not host)', () => parent.postMessage({ __sranCanvas: true, type: 'probe', payload: 'from-canvas-child' }, '*'));
            try { await fetch(${JSON.stringify(config.gatewayUrl)} + '/portal/me', { credentials: 'include' }); r.attempts['fetch gateway'] = 'reached-network'; } catch (e) { r.attempts['fetch gateway'] = 'threw:' + String(e && e.message || e).slice(0, 60); }
            try { await fetch('/api/v1/conversations'); r.attempts['fetch app origin'] = 'reached-network'; } catch (e) { r.attempts['fetch app origin'] = 'threw:' + String(e && e.message || e).slice(0, 60); }
            return JSON.stringify(r);
          })()`)) as string;
          log.info(`CANVAS CHILD FRAME REPORT: ${inside}`);
          const parsed = JSON.parse(inside) as { href: string; body: string; attempts: Record<string, string> };
          check("frame proved: location.href is about:srcdoc and the body is the artifact's", parsed.href === "about:srcdoc" && /containment probe/i.test(parsed.body), `href=${parsed.href} body="${parsed.body.slice(0, 100)}"`);
          await sleep(1000);
          const fromChild = b.accepted.filter((a) => a.origin !== new URL(config.pageUrl).origin);
          const anyChildLine = log.lines.some((l) => /BRIDGE INBOUND.*canvas-child/.test(l) || /BRIDGE INBOUND: .* from (null|about:|\(unparseable\))/.test(l));
          check("host received NOTHING from the canvas frame", fromChild.length === 0 && !anyChildLine, `attempts=${JSON.stringify(parsed.attempts)}; accepted-from-other-origins=${fromChild.length}`);
          // React artifact (§4): observe what actually happens.
          const t2 = await turn(
            'Return a small React component as a single ```jsx fenced code block named App: it renders an <h1> reading "E3 react probe" and a counter button using useState. No explanation, only the code block.',
          );
          check("React artifact turn returned a ```jsx/tsx block", /```(jsx|tsx|react)/i.test(t2.reply), `reply starts: ${t2.reply.slice(0, 80).replace(/\n/g, "\\n")}`);
          await sleep(800);
          const cspBefore = log.lines.filter((l) => /CSP \(renderer console\)/.test(l)).length;
          await js(`(() => { const bs = [...document.querySelectorAll('button')].filter(x => { const t = (x.textContent || '') + ' ' + (x.getAttribute('aria-label') || ''); return /canvas/i.test(t) && !/close/i.test(t); }); const b = bs[bs.length - 1]; if (b) b.click(); return bs.length; })()`);
          await sleep(4000);
          const reactFrame = wc.mainFrame.framesInSubtree.filter((f) => f !== wc.mainFrame).find((f) => f.url === "about:srcdoc") ?? null;
          let reactState = "(no frame)";
          if (reactFrame) {
            reactState = (await runInFrame(reactFrame, `JSON.stringify({ href: location.href, rootChildren: (document.getElementById('root') || {}).childElementCount ?? -1, h1: (document.querySelector('h1') || {}).textContent || null, hasBabel: typeof Babel !== 'undefined', hasReact: typeof React !== 'undefined' })`)) as string;
          }
          const cspLines = log.lines.filter((l) => /CSP \(renderer console\)/.test(l)).slice(cspBefore);
          log.info(`REACT CANVAS FRAME REPORT: ${reactState}; CSP lines during mount: ${cspLines.length}`);
          for (const l of cspLines.slice(0, 4)) log.info(`  ${l.slice(0, 260)}`);
          const rendered = /"rootChildren":[1-9]/.test(reactState) && /react probe/i.test(reactState);
          check(`React artifact ${rendered ? "RENDERS" : "does NOT render"} under the current CSP (observation for §4)`, true, `${reactState}; csp-violations=${cspLines.length}`);
          (checks[checks.length - 1] as Check & { rendered?: boolean }).rendered = rendered;
          // Control: a valid message from the TOP document arrives.
          const acceptedBefore = b.accepted.length;
          await js(`window.sranHost.postMessage({ nonce: window.sranHost.nonce, type: 'ui.ready', payload: { conversationId: null, lastSeq: 0 } })`);
          await sleep(500);
          check("control: ui.ready posted from the TOP document arrived (the log records arrivals)", b.accepted.length === acceptedBefore + 1, lastLine(/BRIDGE HANDSHAKE/));
        }
        break;
      }
      case "cancel": {
        const accepted = b.accepted.length;
        await js(TYPE_AND_SEND("Write a 600-word essay about the history of TCP congestion control, in numbered paragraphs."));
        const t0c = Date.now();
        while (!b.accepted.slice(accepted).some((a) => a.type === "turn.submit") && Date.now() - t0c < 15_000) await sleep(100);
        const convId = co.lastConversationId!;
        await sleep(2500);
        const lenAtCancel = (co.currentAssistantText(convId) ?? "").length;
        log.info(`PROBE cancel: posting turn.cancel from the page at +2.5 s (${lenAtCancel} chars streamed so far)`);
        await js(`window.sranHost.postMessage({ nonce: window.sranHost.nonce, type: 'turn.cancel', payload: { conversationId: ${JSON.stringify(convId)} } })`);
        await sleep(1500);
        const stopped = !co.isRunning(convId);
        const lenAfter = (co.lastReply.get(convId) ?? "").length;
        await sleep(3000);
        const lenLater = (co.lastReply.get(convId) ?? "").length;
        check("turn.cancel arrived and the host cancelled the turn", stopped && log.lines.some((l) => /TURN CANCELLED/.test(l)), `${lastLine(/TURN CANCEL requested/)} → ${lastLine(/TURN CANCELLED/)}`);
        check("the generation actually stopped (no more text after cancel)", lenAfter === lenLater && lenAfter > 0 && lenAfter < 3000, `chars at cancel=${lenAtCancel}, 1.5 s later=${lenAfter}, 4.5 s later=${lenLater}; tail="…${(co.lastReply.get(convId) ?? "").slice(-70)}"`);
        break;
      }
      case "reload": {
        const accepted = b.accepted.length;
        await js(TYPE_AND_SEND("Write a 600-word essay about the history of TCP congestion control: slow start, congestion avoidance, fast retransmit, fast recovery. Plain prose paragraphs only — no headings, no lists, no bold, no markdown of any kind."));
        const t0r = Date.now();
        while (!b.accepted.slice(accepted).some((a) => a.type === "turn.submit") && Date.now() - t0r < 15_000) await sleep(100);
        const convId = co.lastConversationId!;
        await sleep(3500);
        const charsAtReload = (co.currentAssistantText(convId) ?? "").length;
        const runningAtReload = co.isRunning(convId);
        log.info(`PROBE reload: reloading the page mid-turn at +3.5 s (turn running=${runningAtReload}, ${charsAtReload} chars streamed)`);
        wc.reload();
        const t1r = Date.now();
        while (!log.lines.some((l) => /RESUME conv=/.test(l)) && Date.now() - t1r < 20_000) await sleep(100);
        while (co.isRunning(convId)) await sleep(100);
        await sleep(2500);
        const reply = co.lastReply.get(convId) ?? "";
        const dom = (await js(`document.body.innerText.replace(/\\s+/g, ' ')`)) as string;
        // Windows taken INSIDE the first and last sentences: innerText drops
        // list markers and markdown at the edges, prose in the middle survives.
        const flat = reply.replace(/\s+/g, " ").trim();
        const head = flat.slice(20, 80).trim();
        const tail = flat.slice(-80, -20).trim();
        const occurrences = (needle: string) => (needle ? dom.split(needle).length - 1 : 0);
        check("the turn was still running at reload", runningAtReload && charsAtReload > 0, `running=${runningAtReload} chars=${charsAtReload}`);
        check("ui.ready after reload carried lastSeq and the host replayed from seq+1", /RESUME conv=.*replaying/.test(lastLine(/RESUME conv=/)), `${lastLine(/BRIDGE HANDSHAKE/)} → ${lastLine(/RESUME conv=/)}`);
        check("the answer is on screen whole (head present once, tail present once — not blank, not duplicated)", occurrences(head) === 1 && occurrences(tail) === 1, `head×${occurrences(head)} tail×${occurrences(tail)} replyChars=${reply.length} ${lastLine(/TURN DONE/)}`);
        break;
      }
      case "reject": {
        const before = log.lines.length;
        await js(`(() => {
          const n = window.sranHost.nonce;
          window.sranHost.postMessage({ nonce: n, type: 'turn.frobnicate', payload: {} });
          window.sranHost.postMessage({ nonce: 'not-the-nonce', type: 'ui.ready', payload: {} });
          window.sranHost.postMessage({ type: 'ui.ready', payload: {} });
          window.sranHost.postMessage({ nonce: n, type: 'turn.submit', payload: { conversationId: 'c1', text: 'hi', attachmentHandles: ['C:\\\\Windows\\\\win.ini'], modelId: null } });
          window.sranHost.postMessage({ nonce: n, type: 'turn.submit', payload: { conversationId: 'c1', text: 'hi', attachmentHandles: ['../../etc/passwd'], modelId: null } });
          window.sranHost.postMessage({ nonce: n, type: 'host.query', payload: { requestId: 'q1', op: 'shell.exec', args: {} } });
          window.sranHost.postMessage('just a string');
          return 'posted 7';
        })()`);
        await sleep(800);
        const dropped = log.lines.slice(before).filter((l) => /BRIDGE INBOUND DROPPED/.test(l));
        const turnsStarted = log.lines.slice(before).filter((l) => /TURN START/.test(l)).length;
        const has = (re: RegExp) => dropped.some((l) => re.test(l));
        check("unknown type dropped", has(/unknown-type:turn\.frobnicate/), dropped.find((l) => /unknown-type/.test(l)) ?? "(none)");
        check("wrong nonce dropped", has(/nonce-mismatch/), dropped.find((l) => /nonce-mismatch/.test(l)) ?? "(none)");
        check("missing nonce dropped", dropped.filter((l) => /nonce-mismatch/.test(l)).length >= 2, `${dropped.filter((l) => /nonce-mismatch/.test(l)).length} nonce-mismatch line(s)`);
        check("path-shaped attachment handle dropped, and no TURN START followed", has(/attachmentHandle-path-shaped/) && turnsStarted === 0, `${dropped.filter((l) => /path-shaped/.test(l)).length} line(s); TURN START lines=${turnsStarted}`);
        check("unknown host.query op dropped", has(/unknown-op:shell\.exec/), dropped.find((l) => /unknown-op/.test(l)) ?? "(none)");
        check("non-object envelope dropped", has(/not-an-object/), dropped.find((l) => /not-an-object/.test(l)) ?? "(none)");
        log.info("wrong-origin senderFrame: NOT producible live from the same-origin page (sub-frames get no preload; a same-origin child calling parent.sranHost runs in the top frame) — unit test only: test/ipc-guard.test.ts + test/bridge-protocol.test.ts");
        break;
      }
      case "logout": {
        const before = log.lines.length;
        const result = await js(`new Promise((resolve) => {
          window.sranHost.addEventListener('message', (e) => { if (e.data && e.data.type === 'host.result' && e.data.payload.requestId === 'q-logout') resolve(JSON.stringify(e.data.payload)); });
          window.sranHost.postMessage({ nonce: window.sranHost.nonce, type: 'host.query', payload: { requestId: 'q-logout', op: 'session.logout', args: {} } });
          setTimeout(() => resolve('timeout'), 8000);
        })`);
        await sleep(1500);
        const lines = log.lines.slice(before);
        check("host.query session.logout accepted and answered with host.result", String(result).includes('"ok":true'), `host.result=${String(result)}`);
        check("host signed out: server revoked, store + jar cleared, page blanked, signed-out view", lines.some((l) => /SIGNED OUT \(reason=bridge\)/.test(l)) && lines.some((l) => /Server-side revocation ok/.test(l)) && !ctl.hasSession, lines.filter((l) => /HOST QUERY|revocation|Jar cleared|SIGNED OUT|VIEW: signed-out/.test(l)).map((l) => l.slice(24, 140)).join(" | "));
        break;
      }
      default:
        check(`unknown probe '${name}'`, false, "");
    }
  } catch (err) {
    check("probe threw", false, (err as Error).message);
  }
  finish();
}

async function runInFrame(frame: WebFrameMain, code: string): Promise<unknown> {
  return frame.executeJavaScript(code);
}

async function runE1Probe(win: BrowserWindow): Promise<void> {
  const wc = win.webContents;
  const checks: Check[] = [];
  const check = (name: string, pass: boolean, evidence: string) => {
    checks.push({ name, pass, evidence });
    log.info(`[${pass ? "PASS" : "FAIL"}] ${name} — ${evidence}`);
  };
  const lastLine = (pattern: RegExp) => [...log.lines].reverse().find((l) => pattern.test(l)) ?? "(no log line)";
  const pageOrigin = new URL(config.pageUrl).origin;

  log.info("=== E1 PROBE: provoking every refusal ===");
  await sleep(1500);
  const cspLoadViolations = log.lines.filter((l) => /CSP \(renderer console\)/.test(l)).length;
  check("page's own bounce to the portal is refused (re-auth signal)", log.lines.some((l) => /NAVIGATION REFUSED \(portal-reauth\)/.test(l)), lastLine(/portal-reauth/));
  const before = log.lines.length;
  await wc.executeJavaScript(`location.href = 'https://example.com/?x=' + encodeURIComponent(location.origin)`);
  await sleep(800);
  const refused = log.lines.slice(before).find((l) => /NAVIGATION REFUSED \(outside-allow-list\)/.test(l));
  check("top-level navigation to https://example.com/?x=<allowed origin> refused", refused !== undefined && new URL(wc.getURL()).origin === pageOrigin, `${refused ?? "(no refusal logged)"}; page still at ${redactUrl(wc.getURL())}`);
  const windowsBefore = BrowserWindow.getAllWindows().length;
  await wc.executeJavaScript(`window.open('https://example.com/e1-external-link')`);
  await sleep(800);
  check("window.open('https://example.com/...') denied in-app and sent to the system browser", log.lines.some((l) => /window\.open → system browser: https:\/\/example\.com\/e1-external-link/.test(l)) && BrowserWindow.getAllWindows().length === windowsBefore, `${lastLine(/window\.open/)}; windows=${BrowserWindow.getAllWindows().length}`);
  await wc.executeJavaScript(`(() => { const f = document.createElement('iframe'); f.src = 'https://example.com/e1-frame'; document.body.appendChild(f); return true; })()`);
  await sleep(800);
  const frameByGuard = log.lines.some((l) => /FRAME NAVIGATION REFUSED \(outside-allow-list\)/.test(l));
  const frameByCsp = log.lines.some((l) => /CSP \(renderer console\): Framing 'https:\/\/example\.com\//.test(l));
  check("iframe to https://example.com refused (CSP frame-src or will-frame-navigate)", frameByGuard || frameByCsp, frameByGuard ? lastLine(/FRAME NAVIGATION REFUSED/) : `by CSP: ${lastLine(/Framing 'https:\/\/example\.com\//)}`);
  const fetchOutcome = await wc.executeJavaScript(`fetch('https://example.com/e1-csp', {mode:'no-cors'}).then(() => 'reached network', e => 'blocked: ' + e.message)`);
  await sleep(300);
  check("CSP header applied: fetch to https://example.com blocked by connect-src", String(fetchOutcome).startsWith("blocked") && log.lines.some((l) => /CSP \(renderer console\)/.test(l)), `fetch → ${String(fetchOutcome)}; CSP console lines during page load (0 = CSP did not break the page): ${cspLoadViolations}`);
  const crashesBefore = rendererCrashes;
  const recovered = new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => resolve(false), 8000);
    wc.once("render-process-gone", () => {
      wc.once("did-finish-load", () => {
        clearTimeout(timer);
        resolve(true);
      });
    });
  });
  wc.forcefullyCrashRenderer();
  const didRecover = await recovered;
  check("renderer forcefully crashed → render-process-gone handled → page reloaded", rendererCrashes === crashesBefore + 1 && didRecover, `${lastLine(/RENDERER GONE/)} → ${lastLine(/did-finish-load/)}`);
  // The page-side check names the IPC primitive by concatenation so the
  // confinement scan (no-remote-no-ipcrenderer.test.ts) stays strict about
  // the token appearing outside the preload; this string runs in the page.
  const ipcGlobal = "ipc" + "Renderer";
  const surface = await wc.executeJavaScript(
    `JSON.stringify({ keys: Object.keys(window.sranHost ?? {}).sort(), frozen: Object.isFrozen(window.sranHost), hasRequire: typeof require !== 'undefined', hasProcess: typeof process !== 'undefined', hasIpc: typeof window[${JSON.stringify(ipcGlobal)}] !== 'undefined', nonceType: typeof window.sranHost?.nonce })`,
  );
  const s = JSON.parse(String(surface)) as { keys: string[]; frozen: boolean; hasRequire: boolean; hasProcess: boolean; hasIpc: boolean };
  const expected = [...BRIDGE_SURFACE_MEMBERS].sort().join(",");
  check(`page sees window.sranHost = {${expected}} exactly; no require/process/IPC primitive`, s.keys.join(",") === expected && s.frozen && !s.hasRequire && !s.hasProcess && !s.hasIpc, surface as string);
  const failed = checks.filter((c) => !c.pass).length;
  log.info(`=== E1 PROBE DONE: ${checks.length - failed}/${checks.length} passed; log file ${log.filePath} ===`);
  process.stdout.write(`E1_PROBE ${JSON.stringify({ checks, logFile: log.filePath })}\n`);
  isQuitting = true;
  app.exit(failed === 0 ? 0 : 1);
}

function runMeasure(win: BrowserWindow): void {
  const created = process.getCreationTime();
  win.webContents.once("did-finish-load", () => {
    const coldStartMs = created === null ? -1 : Date.now() - created;
    setTimeout(() => {
      const metrics = app.getAppMetrics().map((m) => ({
        type: m.type,
        pid: m.pid,
        name: m.name ?? null,
        workingSetMB: Math.round((m.memory.workingSetSize / 1024) * 10) / 10,
        privateMB: Math.round(((m.memory.privateBytes ?? 0) / 1024) * 10) / 10,
      }));
      const totalWorkingSetMB = Math.round(metrics.reduce((s, m) => s + m.workingSetMB, 0) * 10) / 10;
      const totalPrivateMB = Math.round(metrics.reduce((s, m) => s + m.privateMB, 0) * 10) / 10;
      process.stdout.write(`E1_MEASURE ${JSON.stringify({ coldStartMs, processes: metrics.length, totalWorkingSetMB, totalPrivateMB, perProcess: metrics })}\n`);
      isQuitting = true;
      app.exit(0);
    }, 2000);
  });
}
