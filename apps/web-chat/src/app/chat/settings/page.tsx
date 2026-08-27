"use client";

// Desktop "Settings" screen — the local subset (SHELL_PHASE5_REPORT.md §2).
//
// This is the security-critical screen: it drives settings.set (a CLOSED key
// allow-list — the host drops any key not in it) and folder grant/revoke (the
// page NEVER names a grant path; "Add folder" opens a NATIVE picker in the host
// and the host validates what the USER chose). Server-backed settings — personal
// instructions, the model list — are NOT here; the page fetches those normally.

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { useHostedMode } from "@/hooks/useHostedMode";
import { hostApi, type SettingKey, type SettingsSnapshot } from "@/lib/hostApi";

export default function SettingsPage() {
  const hosted = useHostedMode();
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
        toast("Couldn't load settings.");
      } finally {
        setLoading(false);
      }
    })();
  }, [hosted]);

  if (!hosted) return <DesktopOnlyNotice />;

  // Persist one key, optimistic with revert. The host validates the value and
  // drops any key outside its allow-list; a refusal reverts the control.
  async function patch<K extends keyof SettingsSnapshot>(key: SettingKey, value: SettingsSnapshot[K]) {
    if (!settings) return;
    const previous = settings;
    setSettings({ ...settings, [key]: value });
    const r = await hostApi.setSetting(key, value);
    if (!r.ok) {
      setSettings(previous);
      toast(r.error === "refused" ? "That setting can't be changed here." : "Couldn't save that setting.");
      return;
    }
    if (r.data?.restartRequired) setRestartNote(true);
  }

  async function addFolder() {
    const r = await hostApi.pickAndGrantFolder();
    if (!r.ok) {
      toast("That folder can't be granted.");
      return;
    }
    if (r.data?.cancelled) return;
    if (r.data?.duplicate) {
      toast("That folder is already granted.");
      return;
    }
    if (r.data?.folders && settings) setSettings({ ...settings, grantedFolders: r.data.folders });
  }

  async function revokeFolder(folder: string) {
    const r = await hostApi.revokeFolder(folder);
    if (r.ok && r.data && settings) setSettings({ ...settings, grantedFolders: r.data.folders });
    else if (!r.ok) toast("Couldn't remove that folder.");
  }

  // FIX1: re-read settings (browser connection status is a snapshot).
  async function refreshBrowser() {
    try {
      setSettings(await hostApi.getSettings());
    } catch {
      toast("Couldn't refresh.");
    }
  }

  // FIX1 §1.2 escape hatch: point the app at the folder the user actually loaded.
  async function pickExtensionFolder() {
    const r = await hostApi.pickExtensionFolder();
    if (!r.ok) {
      if (r.error !== "cancelled") toast("That folder can't be used.");
      return;
    }
    if (r.data?.cancelled) return;
    await refreshBrowser();
    toast("Extension folder set. Reload the extension in Chrome if it doesn't connect.");
  }

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <BackIcon /> Back
        </Link>

        <h1 className="mt-6 text-2xl font-semibold tracking-tight text-text-primary">Settings</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Preferences stored on this device. Account-wide settings live in your profile.
        </p>

        {loading || !settings ? (
          <div className="flex items-center justify-center py-16 text-text-secondary">
            <Spinner size={20} />
          </div>
        ) : (
          <div className="mt-8 flex flex-col gap-8">
            <Section title="Appearance">
              <Row label="Theme" hint="Applies to the desktop window chrome.">
                <select
                  value={settings.theme}
                  onChange={(e) => void patch("theme", e.target.value)}
                  className="focus-ring rounded-lg border border-border bg-bg-surface px-3 py-1.5 text-sm text-text-primary"
                >
                  <option value="Dark">Dark</option>
                  <option value="Light">Light</option>
                  <option value="System">System</option>
                </select>
              </Row>
            </Section>

            <Section title="General">
              <Toggle
                label="Minimize to tray"
                checked={settings.minimizeToTray}
                onChange={(v) => void patch("minimizeToTray", v)}
              />
              <Toggle
                label="Start with Windows"
                checked={settings.startWithWindows}
                onChange={(v) => void patch("startWithWindows", v)}
              />
              <Toggle
                label="Send crash reports"
                hint="Off by default. Diagnostic reports only, never chat content."
                checked={settings.crashReportingEnabled}
                onChange={(v) => void patch("crashReportingEnabled", v)}
              />
              <Toggle
                label="Back up agent conversations to your account"
                checked={settings.syncAgentConversations}
                onChange={(v) => void patch("syncAgentConversations", v)}
              />
              <Toggle
                label="Prefer on-device Office file generation"
                hint="When supported; the server path stays authoritative otherwise."
                checked={settings.preferLocalOfficeGeneration}
                onChange={(v) => void patch("preferLocalOfficeGeneration", v)}
              />
            </Section>

            <Section title="Folder access" hint="Extra folders the agent's file tools may reach. Consent still gates every write.">
              {settings.grantedFolders.length === 0 ? (
                <p className="text-sm text-text-muted">No folders granted yet.</p>
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
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-3">
                <Button variant="ghost" onClick={() => void addFolder()}>
                  Add folder…
                </Button>
                {/* The page names no path: this opens a NATIVE folder picker in
                    the host, which validates the folder the USER chose (§2.3). */}
              </div>
            </Section>

            <SitesSection />

            <BrowserSection browser={settings.browser} onRefresh={refreshBrowser} onPick={pickExtensionFolder} />

            <Section title="Endpoints" hint="Where the desktop reaches your gateway. Changing these needs a restart.">
              <EndpointField
                label="Chat edge URL"
                value={settings.chatEdgeBaseUrl}
                onCommit={(v) => void patch("chatEdgeBaseUrl", v)}
              />
              <EndpointField
                label="Gateway URL"
                value={settings.gatewayBaseUrl}
                onCommit={(v) => void patch("gatewayBaseUrl", v)}
              />
              {restartNote ? (
                <p className="text-xs text-status-warning">Restart the app for endpoint changes to take effect.</p>
              ) : null}
            </Section>

            <Section title="Advanced">
              <EndpointField
                label="Default model"
                value={settings.defaultModel}
                onCommit={(v) => void patch("defaultModel", v)}
              />
              <EndpointField
                label="Browser extension ID (load-unpacked)"
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

function DesktopOnlyNotice() {
  return (
    <div className="flex flex-1 items-center justify-center bg-bg-base px-6 text-center">
      <p className="max-w-sm text-sm text-text-muted">This screen is available in the SRAN Desktop app.</p>
    </div>
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
    else toast("Couldn't revoke that site.");
  }
  return (
    <Section title="Approved sites" hint="Sites you chose 'Always allow actions on this site' for. Every browser action re-checks the live site; revoke to make it ask again.">
      {sites === null ? (
        <p className="text-sm text-text-tertiary">Loading…</p>
      ) : sites.length === 0 ? (
        <p className="text-sm text-text-tertiary" data-testid="sites-empty">No approved sites yet — the assistant asks before acting on each new site.</p>
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
                Revoke
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
  if (!browser) return null;
  const connected = browser.connected;
  async function copyFolder() {
    try {
      await navigator.clipboard.writeText(browser!.extensionFolder);
      toast("Folder path copied.");
    } catch {
      toast("Couldn't copy.");
    }
  }
  return (
    <Section title="Browser" hint="Lets the assistant read and act on your Chrome tabs. The extension is loaded by hand, once.">
      <div className="flex items-center gap-2">
        <span
          className={`h-2.5 w-2.5 rounded-full ${connected ? "bg-status-success" : "bg-status-warning"}`}
          aria-hidden
        />
        <span className="text-sm text-text-primary" data-testid="browser-status">
          {connected ? "Connected" : "Not connected"}
        </span>
        <button
          type="button"
          onClick={onRefresh}
          className="ml-auto rounded px-2 py-1 text-xs text-text-muted hover:bg-bg-elevated hover:text-text-secondary"
        >
          Refresh
        </button>
      </div>

      {!connected ? (
        <div className="rounded-md border border-border bg-bg-surface p-3">
          <p className="text-sm text-text-secondary">To connect, load the extension in Chrome:</p>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-text-muted">
            <li>
              Open <span className="font-mono text-text-secondary">chrome://extensions</span>
            </li>
            <li>Turn on <span className="text-text-secondary">Developer mode</span> (top-right)</li>
            <li>
              Click <span className="text-text-secondary">Load unpacked</span> and choose the folder below
            </li>
            <li>Come back here and press Refresh</li>
          </ol>
        </div>
      ) : null}

      <div>
        <p className="text-xs text-text-muted">Extension folder to load</p>
        <div className="mt-1 flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate rounded-md border border-border bg-bg-surface px-3 py-2 font-mono text-xs text-text-primary" data-testid="browser-folder">
            {browser.extensionFolder}
          </span>
          <Button variant="ghost" onClick={() => void copyFolder()}>
            Copy
          </Button>
        </div>
        <p className="mt-1 text-[11px] text-text-muted">
          Expected id <span className="font-mono">{browser.extensionId || "—"}</span>
        </p>
      </div>

      <div>
        <Button variant="ghost" onClick={onPick}>
          I loaded a different folder…
        </Button>
        {/* The page names no path: this opens a NATIVE folder picker in the host,
            which derives the id and authorises the folder the USER chose. */}
      </div>
    </Section>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-xs font-medium uppercase tracking-wider text-text-secondary">{title}</h2>
      {hint ? <p className="mt-1 text-xs text-text-muted">{hint}</p> : null}
      <div className="mt-3 flex flex-col gap-3">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
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
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`focus-ring relative h-6 w-11 shrink-0 rounded-full transition-colors ${
          checked ? "bg-status-success" : "bg-bg-elevated"
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
            checked ? "translate-x-[22px]" : "translate-x-0.5"
          }`}
        />
      </button>
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

function BackIcon() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}
