"use client";

// Desktop "Settings" screen — the local subset (SHELL_PHASE5_REPORT.md §2).
//
// FIX43 Part B — this is the DEVICE half of /chat/settings. page.tsx picks
// between it and WebSettings at runtime (window.sranHost present or not), so
// one build still serves both surfaces and none of these rows — tray, start
// with Windows, folder access, approved sites, the browser bridge — can ever
// render in a plain browser, disabled or otherwise. Section / Row / BackIcon
// are exported so the web half looks identical.
//
// This is the security-critical screen: it drives settings.set (a CLOSED key
// allow-list — the host drops any key not in it) and folder grant/revoke (the
// page NEVER names a grant path; "Add folder" opens a NATIVE picker in the host
// and the host validates what the USER chose). Server-backed settings — personal
// instructions, the model list — are NOT here; the page fetches those normally.
//
// FIX33 B7: every visible string comes from lib/i18n/dict.ts (settings.* keys);
// i18n.hardcoded.test.tsx pins this file so English can't creep back.

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { useHostedMode } from "@/hooks/useHostedMode";
import { Switch } from "@/components/ui/Switch";
import { acceleratorFromKey, describeAccelerator } from "@/lib/accelerator";
import { hostApi, type SettingKey, type SettingsSnapshot, type ShellPrefsDto } from "@/lib/hostApi";
import { tOutside, useT } from "@/lib/i18n";

export function DeviceSettings() {
  // Rendered only when page.tsx has already decided the surface is the
  // desktop; `hosted` is still read through the hydration-safe hook because
  // the first effect pass sees false.
  const hosted = useHostedMode();
  const t = useT();
  const [settings, setSettings] = useState<SettingsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [restartNote, setRestartNote] = useState(false);

  useEffect(() => {
    // Depends on `hosted`: the hydration pass commits with false first.
    if (!hosted) return;
    void (async () => {
      try {
        // FIX25 §3: the grant list comes from `folders.list` (the host's
        // GrantStore — the list the file tools and the consent gate use), not
        // from the settings snapshot's `grantedFolders`, which was the
        // sidecar's never-written WPF-era field: it read as empty on a cold
        // start beside two live grants, until an add replaced it with the
        // real list. (The host now overlays the snapshot too; this is the
        // belt to that brace.)
        const [snapshot, folders] = await Promise.all([hostApi.getSettings(), hostApi.listFolders().catch(() => null)]);
        setSettings(folders ? { ...snapshot, grantedFolders: folders } : snapshot);
      } catch {
        toast(tOutside("settings.toast.loadFailed"));
      } finally {
        setLoading(false);
      }
    })();
  }, [hosted]);

  if (!hosted) return null;

  // Persist one key, optimistic with revert. The host validates the value and
  // drops any key outside its allow-list; a refusal reverts the control.
  async function patch<K extends keyof SettingsSnapshot>(key: SettingKey, value: SettingsSnapshot[K]) {
    if (!settings) return;
    const previous = settings;
    setSettings({ ...settings, [key]: value });
    const r = await hostApi.setSetting(key, value);
    if (!r.ok) {
      setSettings(previous);
      toast(r.error === "refused" ? tOutside("settings.toast.refused") : tOutside("settings.toast.saveFailed"));
      return;
    }
    if (r.data?.restartRequired) setRestartNote(true);
  }

  async function addFolder() {
    const r = await hostApi.pickAndGrantFolder();
    if (!r.ok) {
      toast(tOutside("settings.toast.folderRejected"));
      return;
    }
    if (r.data?.cancelled) return;
    if (r.data?.duplicate) {
      toast(tOutside("settings.toast.folderDuplicate"));
      return;
    }
    if (r.data?.folders && settings) setSettings({ ...settings, grantedFolders: r.data.folders });
  }

  async function revokeFolder(folder: string) {
    const r = await hostApi.revokeFolder(folder);
    if (r.ok && r.data && settings) setSettings({ ...settings, grantedFolders: r.data.folders });
    else if (!r.ok) toast(tOutside("settings.toast.folderRemoveFailed"));
  }

  // FIX1: re-read settings (browser connection status is a snapshot).
  async function refreshBrowser() {
    try {
      setSettings(await hostApi.getSettings());
    } catch {
      toast(tOutside("settings.toast.refreshFailed"));
    }
  }

  // FIX1 §1.2 escape hatch: point the app at the folder the user actually loaded.
  async function pickExtensionFolder() {
    const r = await hostApi.pickExtensionFolder();
    if (!r.ok) {
      if (r.error !== "cancelled") toast(tOutside("settings.toast.extFolderRejected"));
      return;
    }
    if (r.data?.cancelled) return;
    await refreshBrowser();
    toast(tOutside("settings.toast.extFolderSet"));
  }

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <BackIcon /> {t("settings.back")}
        </Link>

        <h1 className="mt-6 text-2xl font-semibold tracking-tight text-text-primary">{t("settings.title")}</h1>
        <p className="mt-1 text-sm text-text-secondary">{t("settings.subtitle")}</p>

        {loading || !settings ? (
          <div className="flex items-center justify-center py-16 text-text-secondary">
            <Spinner size={20} />
          </div>
        ) : (
          <div className="mt-8 flex flex-col gap-8">
            <Section title={t("settings.section.appearance")}>
              <Row label={t("settings.theme")} hint={t("settings.theme.hint")}>
                <select
                  value={settings.theme}
                  onChange={(e) => void patch("theme", e.target.value)}
                  className="focus-ring rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-text-primary"
                >
                  <option value="Dark">{t("settings.theme.dark")}</option>
                  <option value="Light">{t("settings.theme.light")}</option>
                  <option value="System">{t("settings.theme.system")}</option>
                </select>
              </Row>
            </Section>

            <Section title={t("settings.section.general")}>
              <Toggle
                label={t("settings.minimizeToTray")}
                checked={settings.minimizeToTray}
                onChange={(v) => void patch("minimizeToTray", v)}
              />
              <Toggle
                label={t("settings.startWithWindows")}
                checked={settings.startWithWindows}
                onChange={(v) => void patch("startWithWindows", v)}
              />
              <Toggle
                label={t("settings.crashReports")}
                hint={t("settings.crashReports.hint")}
                checked={settings.crashReportingEnabled}
                onChange={(v) => void patch("crashReportingEnabled", v)}
              />
              <Toggle
                label={t("settings.backupAgentConvs")}
                checked={settings.syncAgentConversations}
                onChange={(v) => void patch("syncAgentConversations", v)}
              />
              <Toggle
                label={t("settings.localOffice")}
                hint={t("settings.localOffice.hint")}
                checked={settings.preferLocalOfficeGeneration}
                onChange={(v) => void patch("preferLocalOfficeGeneration", v)}
              />
            </Section>

            <Section title={t("settings.section.folders")} hint={t("settings.section.folders.hint")}>
              {settings.grantedFolders.length === 0 ? (
                <p className="text-sm text-text-muted">{t("settings.folders.empty")}</p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {settings.grantedFolders.map((folder) => (
                    <li
                      key={folder}
                      className="flex items-center gap-3 rounded-md border border-border bg-bg-surface px-3 py-2"
                    >
                      <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-primary">{folder}</span>
                      <button
                        type="button"
                        onClick={() => void revokeFolder(folder)}
                        className="rounded px-2 py-1 text-xs text-status-danger hover:bg-status-danger/10"
                      >
                        {t("settings.folders.remove")}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-3">
                <Button variant="ghost" onClick={() => void addFolder()}>
                  {t("settings.folders.add")}
                </Button>
                {/* The page names no path: this opens a NATIVE folder picker in
                    the host, which validates the folder the USER chose (§2.3). */}
              </div>
            </Section>

            <SitesSection />

            <BrowserSection browser={settings.browser} onRefresh={refreshBrowser} onPick={pickExtensionFolder} />

            <Section title={t("settings.section.endpoints")} hint={t("settings.section.endpoints.hint")}>
              <EndpointField
                label={t("settings.chatEdgeUrl")}
                value={settings.chatEdgeBaseUrl}
                onCommit={(v) => void patch("chatEdgeBaseUrl", v)}
              />
              <EndpointField
                label={t("settings.gatewayUrl")}
                value={settings.gatewayBaseUrl}
                onCommit={(v) => void patch("gatewayBaseUrl", v)}
              />
              {restartNote ? (
                <p className="text-xs text-status-warning">{t("settings.restartNote")}</p>
              ) : null}
            </Section>

            <QuickAskSection />

            <Section title={t("settings.section.advanced")}>
              <EndpointField
                label={t("settings.defaultModel")}
                value={settings.defaultModel}
                onCommit={(v) => void patch("defaultModel", v)}
              />
              <EndpointField
                label={t("settings.extensionId")}
                value={settings.browserExtensionId}
                onCommit={(v) => void patch("browserExtensionId", v)}
              />
            </Section>
          </div>
        )}
      </main>
    </div>
  );
}

// FIX34 Part A — the quick-ask window's two host prefs. Claude on Windows
// offers "set a custom keyboard shortcut" for its quick entry; the tray-click
// choice is ours (§2.5: Claude forces the quick entry on a taskbar click with
// no setting — anthropics/claude-code#22420, closed not planned). A shortcut
// the OS refuses (held by another app) is reported, never silently dropped,
// and the previous shortcut keeps working.
function QuickAskSection() {
  const t = useT();
  const [prefs, setPrefs] = useState<ShellPrefsDto | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void (async () => {
      try {
        setPrefs(await hostApi.getShellPrefs());
      } catch {
        setPrefs(null); // pre-FIX34 host: the section stays hidden
      }
    })();
  }, []);
  useEffect(() => {
    if (!capturing) return;
    function onKey(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        setCapturing(false);
        return;
      }
      const acc = acceleratorFromKey(e);
      if (!acc) return; // only modifiers so far — keep waiting
      setCapturing(false);
      void save({ quickHotkey: acc });
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capturing]);

  async function save(patch: Partial<ShellPrefsDto>) {
    setError(null);
    const r = await hostApi.setShellPrefs(patch);
    if (r.ok && r.data) {
      setPrefs(r.data);
      if (patch.quickHotkey) toast(tOutside("settings.quickHotkey.saved"));
      return;
    }
    if (r.error === "hotkey-taken") setError(tOutside("settings.quickHotkey.taken"));
    else if (r.error === "hotkey-taken-by-main") setError(tOutside("settings.quickHotkey.takenByMain"));
    else if (r.error?.startsWith("hotkey-")) setError(tOutside("settings.quickHotkey.invalid"));
    else toast(tOutside("settings.toast.prefsFailed"));
  }

  if (!prefs) return null;
  return (
    <Section title={t("settings.section.quickAsk")} hint={t("settings.section.quickAsk.hint")}>
      <Row label={t("settings.quickHotkey")} hint={t("settings.quickHotkey.hint")}>
        <div className="flex items-center gap-2">
          <kbd
            className={`rounded border px-2 py-1 font-mono text-xs ${capturing ? "border-accent text-accent" : "border-border bg-bg-base text-text-primary"}`}
            data-testid="quick-hotkey"
          >
            {capturing ? t("settings.quickHotkey.capture") : describeAccelerator(prefs.quickHotkey)}
          </kbd>
          <Button variant="ghost" onClick={() => setCapturing((v) => !v)} data-testid="quick-hotkey-change">
            {capturing ? t("settings.quickHotkey.cancel") : t("settings.quickHotkey.change")}
          </Button>
        </div>
      </Row>
      {error ? (
        <p className="text-xs text-status-danger" data-testid="quick-hotkey-error">
          {error}
        </p>
      ) : null}
      <Row label={t("settings.trayClick")}>
        <select
          value={prefs.trayClick}
          onChange={(e) => void save({ trayClick: e.target.value as ShellPrefsDto["trayClick"] })}
          className="focus-ring rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-text-primary"
          data-testid="tray-click"
        >
          <option value="main">{t("settings.trayClick.main")}</option>
          <option value="quick">{t("settings.trayClick.quick")}</option>
        </select>
      </Row>
    </Section>
  );
}

// FIX1 §3.3: the one place that says whether the browser bridge is connected and
// EXACTLY what to do about it — the folder to load, copyable. A person who has
// never seen this repo can get connected from what this shows (tested in §4.4).
// FIX6: "Your approved sites" (Claude-in-Chrome). Lists the origins granted
// "Always allow actions on this site" and lets the user revoke each. There is no
// "add" — a site is approved only through the consent prompt, so this section can
// only ever list and remove. Self-contained (its own fetch), like BrowserSection.
function SitesSection() {
  const t = useT();
  const [sites, setSites] = useState<string[] | null>(null);
  useEffect(() => {
    void (async () => {
      try {
        setSites(await hostApi.listSites());
      } catch {
        setSites([]);
      }
    })();
  }, []);
  async function revoke(origin: string) {
    const r = await hostApi.revokeSite(origin);
    if (r.ok && r.data) setSites(r.data.sites);
    else toast(tOutside("settings.sites.revokeFailed"));
  }
  return (
    <Section title={t("settings.section.sites")} hint={t("settings.section.sites.hint")}>
      {sites === null ? (
        <p className="text-sm text-text-tertiary">{t("common.loading")}</p>
      ) : sites.length === 0 ? (
        <p className="text-sm text-text-tertiary" data-testid="sites-empty">{t("settings.sites.empty")}</p>
      ) : (
        <ul className="space-y-1" data-testid="sites-list">
          {sites.map((origin) => (
            <li key={origin} className="flex items-center justify-between gap-2 rounded bg-bg-base px-2 py-1.5" data-site={origin}>
              <span className="truncate font-mono text-xs text-text-secondary" title={origin}>{origin}</span>
              <button
                type="button"
                onClick={() => void revoke(origin)}
                className="shrink-0 rounded px-2 py-1 text-xs font-medium text-status-danger hover:bg-status-danger/10 focus-ring"
                data-testid="site-revoke"
              >
                {t("settings.sites.revoke")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function BrowserSection({
  browser,
  onRefresh,
  onPick,
}: {
  browser?: import("@/lib/hostApi").BrowserStatus;
  onRefresh: () => void;
  onPick: () => void;
}) {
  const t = useT();
  if (!browser) return null;
  const connected = browser.connected;
  async function copyFolder() {
    try {
      await navigator.clipboard.writeText(browser!.extensionFolder);
      toast(tOutside("settings.browser.copied"));
    } catch {
      toast(tOutside("settings.browser.copyFailed"));
    }
  }
  return (
    <Section title={t("settings.section.browser")} hint={t("settings.section.browser.hint")}>
      <div className="flex items-center gap-2">
        <span
          className={`h-2.5 w-2.5 rounded-full ${connected ? "bg-status-success" : "bg-status-warning"}`}
          aria-hidden
        />
        <span className="text-sm text-text-primary" data-testid="browser-status">
          {connected ? t("settings.browser.connected") : t("settings.browser.notConnected")}
        </span>
        <button
          type="button"
          onClick={onRefresh}
          className="ml-auto rounded px-2 py-1 text-xs text-text-muted hover:bg-bg-elevated hover:text-text-secondary"
        >
          {t("settings.browser.refresh")}
        </button>
      </div>

      {!connected ? (
        <div className="rounded-md border border-border bg-bg-surface p-3">
          <p className="text-sm text-text-secondary">{t("settings.browser.howto")}</p>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-text-muted">
            <li>
              {t("settings.browser.step1")} <span className="font-mono text-text-secondary">chrome://extensions</span>
            </li>
            <li>
              {/* "Developer mode" / "Load unpacked" are Chrome's own labels — kept verbatim. */}
              {t("settings.browser.step2.pre")} <span className="text-text-secondary">Developer mode</span> {t("settings.browser.step2.post")}
            </li>
            <li>
              {t("settings.browser.step3.pre")} <span className="text-text-secondary">Load unpacked</span> {t("settings.browser.step3.post")}
            </li>
            <li>{t("settings.browser.step4")}</li>
          </ol>
        </div>
      ) : null}

      <div>
        <p className="text-xs text-text-muted">{t("settings.browser.folderLabel")}</p>
        <div className="mt-1 flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate rounded-md border border-border bg-bg-surface px-3 py-2 font-mono text-xs text-text-primary" data-testid="browser-folder">
            {browser.extensionFolder}
          </span>
          <Button variant="ghost" onClick={() => void copyFolder()}>
            {t("common.copy")}
          </Button>
        </div>
        <p className="mt-1 text-[11px] text-text-muted">
          {t("settings.browser.expectedId")} <span className="font-mono">{browser.extensionId || "—"}</span>
        </p>
      </div>

      <div>
        <Button variant="ghost" onClick={onPick}>
          {t("settings.browser.pickOther")}
        </Button>
        {/* The page names no path: this opens a NATIVE folder picker in the host,
            which derives the id and authorises the folder the USER chose. */}
      </div>
    </Section>
  );
}

export function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-xs font-medium uppercase tracking-wider text-text-secondary">{title}</h2>
      {hint ? <p className="mt-1 text-xs text-text-muted">{hint}</p> : null}
      <div className="mt-3 flex flex-col gap-3">{children}</div>
    </section>
  );
}

export function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <p className="text-sm text-text-primary">{label}</p>
        {hint ? <p className="text-xs text-text-muted">{hint}</p> : null}
      </div>
      {children}
    </div>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <Row label={label} hint={hint}>
      <Switch checked={checked} onChange={onChange} label={label} />
    </Row>
  );
}

// Text setting committed on blur / Enter (not per keystroke), so a network
// write does not fire on every character.
function EndpointField({ label, value, onCommit }: { label: string; value: string; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <Input
      label={label}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft !== value) onCommit(draft.trim());
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

export function BackIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}
