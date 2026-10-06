"use client";

// Connector Marketplace — desktop-only screen (Phase 5).
//
// A React port of the WPF ConnectorMarketplaceViewModel. It lists the
// connector catalog the host exposes, lets the user filter it, and
// install / uninstall or connect / disconnect each entry. The page is a
// thin view over `hostApi`: it never sends an API key, a command line,
// or a spawn approval. Everything dangerous — the api_key prompt, the
// oauth2 system-browser flow, and the add-custom / spawn-consent dialog
// — is collected by the native host through OS dialogs, so those calls
// take no such argument here.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toast";
import { tOutside, useT } from "@/lib/i18n";
import { useDesktopOnlyRoute } from "@/hooks/useDesktopOnlyRoute";
import { ConnectorIcon } from "@/components/chat/connectorIcons";
import { ConnectorReturnNotice } from "@/components/chat/ConnectorReturnNotice";
import { connectorAccess } from "@/lib/connectorCapability";
import { hostApi, type CatalogConnectorDto, type ConnectorOAuthStatusDto, type InstalledConnectorDto } from "@/lib/hostApi";
import { catalogText, connectorStateText } from "@/lib/hostCatalogText";

export default function ConnectorsPage() {
  // FIX34.1 Part F — desktop-only. The sidebar never shows this entry on the
  // web; a typed URL lands back on /chat instead of a "use the desktop app"
  // wall. chat-edge has no MCP client yet, so the web must NOT get a working
  // marketplace here — the route stays for the desktop shell only.
  const hosted = useDesktopOnlyRoute("/chat");
  if (!hosted) return null;
  return <ConnectorMarketplace />;
}

function ConnectorMarketplace() {
  const t = useT();
  const [connectors, setConnectors] = useState<CatalogConnectorDto[]>([]);
  // FIX68: connectors the user added with "Add custom connector" are NOT in
  // the catalogue (the sidecar keeps them in connectors.json) — the page used
  // to list only the catalogue, so an added server never showed a card and
  // could not be uninstalled from here. They lead the list, with their LIVE
  // tool names straight from the server the sidecar dialled.
  const [custom, setCustom] = useState<InstalledConnectorDto[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState("");
  // Ids with an action in flight — disables that card's button so a
  // double-tap can't fire two native dialogs.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [addingCustom, setAddingCustom] = useState(false);
  // FIX69: "Add custom connector" is a two-way choice — a program on this
  // computer (stdio) or a remote https server (url + key, host-owned form).
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  // FIX31 Part 2: the host's view of each oauth2 connector (configured /
  // enabled / connected). The sidecar's `installed` flag is not the truth for
  // oauth2 — those are never installed in the sidecar; the host holds them.
  const [oauth, setOauth] = useState<Record<string, ConnectorOAuthStatusDto>>({});

  async function reload() {
    try {
      const list = await hostApi.listConnectorCatalog();
      setConnectors(list);
      try {
        const installed = await hostApi.listInstalledConnectors();
        setCustom(installed.filter((c) => c.isCustom));
      } catch {
        setCustom([]); // an older host without listInstalled: catalogue only
      }
      const ids = list.filter((c) => c.authType === "oauth2").map((c) => c.id);
      if (ids.length) {
        try {
          const st = await hostApi.oauthStatus(ids);
          setOauth(Object.fromEntries(st.map((s) => [s.connectorId, s])));
        } catch {
          setOauth({}); // an older host: every oauth2 card reads "not available"
        }
      }
    } catch {
      toast(tOutside("connectors.toast.loadFailed"));
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  function setBusy(id: string, busy: boolean) {
    setBusyIds((prev) => {
      const next = new Set(prev);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  // Mirrors the WPF ApplyFilter: case-insensitive match on name,
  // description, or category. Empty query shows everything.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    // FIX69: a key-based catalogue card whose server URL is a placeholder (the
    // embedded starter catalogue) has no destination — no button, no card
    // (FIX11 / FIX34.1: nothing to do, nothing to click). OAuth cards stay:
    // their server comes from the owner's registration file, not the catalogue.
    // FIX89 Part B: that rule stands — what it lacked was a TRACE. The names
    // are listed below the cards with the reason (see `unavailable`), so
    // "four cards, where did the rest go?" has an answer on the page instead
    // of only in docs/CONNECTOR_SETUP.md. Collapse, never delete.
    const showable = connectors.filter((c) => !(c.authType === "api_key" && c.placeholder === true));
    if (!q) return showable;
    // a person searches for the words on the card — the catalogue's own and the ones shown in their language
    return showable.filter((c) => {
      const shown = catalogText(c, t);
      return [c.name, c.description, c.category, shown.description, shown.category].some((text) => text.toLowerCase().includes(q));
    });
  }, [connectors, query, t]);
  /** FIX89 Part B — exactly the cards the filter above removed: real
   *  catalogue entries with nowhere to go. Never filtered by the search box:
   *  it answers "is that everything?", which is not a search result. */
  const unavailable = useMemo(
    () => connectors.filter((c) => c.authType === "api_key" && c.placeholder === true),
    [connectors],
  );
  const filteredCustom = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return custom;
    return custom.filter((c) => (c.name ?? c.id).toLowerCase().includes(q) || c.tools.some((t) => t.toLowerCase().includes(q)));
  }, [custom, query]);

  async function handleUninstallCustom(c: InstalledConnectorDto) {
    setBusy(c.id, true);
    try {
      const res = await hostApi.uninstallConnector(c.id);
      if (!res.ok) {
        toast(tOutside("connectors.toast.uninstallFailed"));
        return;
      }
      await reload();
    } finally {
      setBusy(c.id, false);
    }
  }

  async function handleAddCustom(kind: "local" | "remote") {
    // Spawn consent for a custom stdio MCP server is a NATIVE host
    // dialog — approving a child process is never rendered by the page
    // (Phase 5 §2.4). We only ask the host to open it and reload on success.
    // FIX69: a remote server's url and key are collected in a HOST-owned
    // window the same way; the page learns only the outcome.
    setAddMenuOpen(false);
    setAddingCustom(true);
    try {
      const res = kind === "remote" ? await hostApi.addRemoteConnector() : await hostApi.addCustomConnector();
      if (!res.ok) {
        toast(addErrorText(res.error ?? ""));
        return;
      }
      if (res.data?.added) await reload();
    } finally {
      setAddingCustom(false);
    }
  }

  async function handleAction(c: CatalogConnectorDto) {
    setBusy(c.id, true);
    try {
      if (c.authType === "oauth2") {
        const connected = oauth[c.id]?.connected === true;
        const res = connected
          ? await hostApi.oauthDisconnect(c.id)
          : await hostApi.oauthConnect(c.id);
        if (!res.ok) {
          toast(oauthErrorText(res.error ?? "") ?? (connected ? tOutside("connectors.toast.disconnectFailed") : tOutside("connectors.toast.connectFailed")));
          await reload();
          return;
        }
        // FIX89 Part B — was a hard-coded English template literal, which
        // the ratchet's rule does not see (it flags JSX text and string
        // literals in attributes, not a template inside a call).
        toast(
          connected
            ? tOutside("connectors.toast.disconnected", { name: c.name })
            : tOutside("connectors.toast.connected", { name: c.name }),
        );
        await reload();
        return;
      }

      // authType "none" | "api_key". For api_key the host opens a native
      // key prompt; the page never sees or sends the key.
      if (c.installed) {
        const res = await hostApi.uninstallConnector(c.id);
        if (!res.ok) {
          toast(tOutside("connectors.toast.uninstallFailed"));
          return;
        }
        await reload();
        return;
      }

      // oauth2 connectors are handled by the branch above; here authType is
      // "none" | "api_key". For api_key the host opens a native key prompt (the
      // page never sees or sends the key); the host still refuses anything odd.
      const res = await hostApi.installConnector(c.id);
      if (!res.ok) {
        toast(
          res.error === "api-key-unsupported"
            ? tOutside("connectors.toast.keyUnsupported")
            : tOutside("connectors.toast.installFailed"),
        );
        return;
      }
      // User dismissed the native prompt — no state change, no toast.
      if (res.data?.cancelled) return;
      await reload();
    } finally {
      setBusy(c.id, false);
    }
  }

  return (
    <div className="scrollbar-thin flex flex-1 flex-col overflow-y-auto bg-bg-base">
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 pt-8 pb-12 md:px-6">
        <Link
          href="/chat"
          className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-text-secondary"
        >
          <BackIcon />
          {t("connectors.back")}
        </Link>

        <div className="mt-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
              {t("connectors.title")}
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              {t("connectors.subtitle")}
            </p>
          </div>
          <div className="relative shrink-0">
            <Button
              variant="secondary"
              className="shrink-0"
              onClick={() => setAddMenuOpen((v) => !v)}
              loading={addingCustom}
              aria-haspopup="menu"
              aria-expanded={addMenuOpen}
            >
              <PlusIcon />
              {t("connectors.addCustom")}
            </Button>
            {addMenuOpen ? (
              <div
                role="menu"
                aria-label={t("connectors.addCustom.menuAria")}
                data-testid="add-custom-menu"
                className="absolute right-0 z-10 mt-2 w-72 overflow-hidden rounded-xl border border-border bg-bg-surface shadow-lg"
              >
                <button
                  type="button"
                  role="menuitem"
                  data-testid="add-custom-local"
                  className="block w-full px-4 py-3 text-left text-sm text-text-primary hover:bg-bg-base"
                  onClick={() => void handleAddCustom("local")}
                >
                  <span className="block font-medium">{t("connectors.addCustom.local")}</span>
                  <span className="block text-xs text-text-muted">{t("connectors.addCustom.localHint")}</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="add-custom-remote"
                  className="block w-full border-t border-border px-4 py-3 text-left text-sm text-text-primary hover:bg-bg-base"
                  onClick={() => void handleAddCustom("remote")}
                >
                  <span className="block font-medium">{t("connectors.addCustom.remote")}</span>
                  <span className="block text-xs text-text-muted">{t("connectors.addCustom.remoteHint")}</span>
                </button>
              </div>
            ) : null}
          </div>
        </div>

        {/* a consent flow that ended in the system browser lands here; the notice says what the broker holds */}
        <ConnectorReturnNotice />

        <div className="mt-6">
          <Input
            placeholder={tOutside("connectors.search.placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={tOutside("connectors.search.aria")}
          />
        </div>

        <div className="mt-6">
          {!loaded ? (
            <div className="flex items-center justify-center py-10 text-text-secondary">
              <Spinner size={20} />
            </div>
          ) : filtered.length === 0 && filteredCustom.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border bg-transparent px-8 py-16 text-center text-sm text-text-muted">
              {connectors.length === 0 && custom.length === 0
                ? tOutside("connectors.empty")
                : tOutside("connectors.noMatch")}
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {filteredCustom.map((c) => (
                <CustomConnectorCard
                  key={c.id}
                  connector={c}
                  busy={busyIds.has(c.id)}
                  onUninstall={() => void handleUninstallCustom(c)}
                />
              ))}
              {filtered.map((c) => (
                <ConnectorCard
                  key={c.id}
                  connector={c}
                  oauthStatus={oauth[c.id]}
                  busy={busyIds.has(c.id)}
                  onAction={() => void handleAction(c)}
                />
              ))}
            </div>
          )}
        </div>

        {/* FIX89 Part B — the connectors this build cannot offer yet, named.
            FIX69's rule stands (a card with no destination is not a card), so
            there is no button here and nothing that looks usable: just what
            exists, why it is not a card, and the way out. Without this the
            page silently showed four of eight. */}
        {loaded && unavailable.length > 0 ? (
          <section
            className="mt-8 rounded-xl border border-dashed border-border px-5 py-4"
            data-testid="connectors-unavailable"
            aria-label={t("connectors.unavailable.aria")}
          >
            <h2 className="text-sm font-medium text-text-secondary">
              {t("connectors.unavailable.title", { n: String(unavailable.length) })}
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-text-muted">
              {t("connectors.unavailable.body", {
                names: unavailable.map((c) => c.name).join(", "),
              })}
            </p>
          </section>
        ) : null}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Custom connector card (FIX68)
// ---------------------------------------------------------------------------

/** A connector the user added themselves (a local MCP server the sidecar
 *  spawns). The tool names are the ones the server announced on tools/list —
 *  nothing here is hard-coded — and the only action is Uninstall, which
 *  ends the server process and removes its tools from the model's list. */
function CustomConnectorCard({
  connector,
  busy,
  onUninstall,
}: {
  connector: InstalledConnectorDto;
  busy: boolean;
  onUninstall: () => void;
}) {
  const t = useT();
  // FIX69: a remote server shows where it points (the user's own URL, which
  // carries no key) and THAT a key is set — the page never has the key.
  const remote = connector.transport === "http";
  const origin = (() => {
    if (!remote || !connector.serverUrl) return null;
    try {
      return new URL(connector.serverUrl).origin;
    } catch {
      return connector.serverUrl;
    }
  })();
  return (
    <section
      className="rounded-xl border border-border bg-bg-surface p-5 transition-colors hover:border-border-strong"
      data-testid={`connector-card-${connector.id}`}
    >
      <div className="flex items-start justify-between gap-4">
        <ConnectorIcon connectorId={connector.id} name={connector.name ?? connector.id} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <HealthDot status={connector.status} />
            <h3 className="truncate text-base font-medium text-text-primary">
              {connector.name ?? connector.id}
            </h3>
            <span className="shrink-0 rounded-full border border-border bg-bg-base px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
              {remote ? t("connectors.custom.remoteBadge") : t("connectors.custom.badge")}
            </span>
            {connector.hasKey ? (
              <span
                className="shrink-0 rounded-full border border-status-success/40 bg-status-success/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-status-success"
                data-testid="connector-key-set"
              >
                {t("connectors.custom.keySet")}
              </span>
            ) : null}
          </div>
          <p className="mt-2 text-sm text-text-secondary">
            {remote ? t("connectors.custom.remoteDescription") : t("connectors.custom.description")}
            {origin ? (
              <>
                {" "}
                <span className="font-mono text-xs text-text-muted" data-testid="connector-origin">{origin}</span>
              </>
            ) : null}
          </p>
          <p className="mt-3 truncate text-xs text-text-muted" data-testid="connector-tools">
            {connector.tools.length > 0 ? connector.tools.join(" · ") : t("connectors.custom.noTools")}
          </p>
        </div>
        <Button
          variant="ghost"
          className="shrink-0"
          onClick={onUninstall}
          loading={busy}
          data-testid="connector-uninstall"
        >
          {t("connectors.custom.uninstall")}
        </Button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Connector card
// ---------------------------------------------------------------------------

/** FIX31 Part 2: every way the host's OAuth flow can end, in the user's words.
 *  FIX89 Part B: the words now come from the catalogue in both languages —
 *  the wording itself is unchanged (it is the right wording; it was only
 *  English-only). */
const OAUTH_ERROR_KEY = {
  "oauth-unsupported": "connectors.oauth.unsupported",
  "oauth-not-configured": "connectors.oauth.notConfigured",
  "oauth-disabled": "connectors.oauth.disabled",
  "oauth-denied": "connectors.oauth.denied",
  "oauth-timeout": "connectors.oauth.timeout",
  "oauth-cancelled": "connectors.oauth.cancelled",
  "oauth-state-mismatch": "connectors.oauth.stateMismatch",
  "oauth-exchange-failed": "connectors.oauth.exchangeFailed",
  "oauth-browser-failed": "connectors.oauth.browserFailed",
  "oauth-storage-unavailable": "connectors.oauth.storageUnavailable",
} as const;

function oauthErrorText(error: string): string | null {
  const key = OAUTH_ERROR_KEY[error as keyof typeof OAUTH_ERROR_KEY];
  return key ? tOutside(key) : null;
}

/** FIX69: every way adding a custom connector can end, in the user's words.
 *  A refused key says so ("rejected") — never "try again". */
function addErrorText(error: string): string {
  if (error === "addcustom-unsupported" || error === "addremote-unsupported") return tOutside("connectors.toast.customUnsupported");
  if (error.startsWith("remote-refused:")) {
    const reason = error.slice("remote-refused:".length);
    if (reason === "https-required") return tOutside("connectors.toast.remoteHttps");
    if (reason === "internal") return tOutside("connectors.toast.remoteInternal");
    if (reason === "metadata") return tOutside("connectors.toast.remoteMetadata");
    if (reason === "unresolvable") return tOutside("connectors.toast.remoteUnresolvable");
    return tOutside("connectors.toast.remoteInvalid");
  }
  if (error === "https-required") return tOutside("connectors.toast.remoteHttps");
  if (error.startsWith("test-failed")) {
    const code = error.slice("test-failed".length + 1);
    if (code === "key-rejected") return tOutside("connectors.toast.keyRejected");
    if (code === "tls") return tOutside("connectors.toast.remoteTls");
    if (code === "target-internal") return tOutside("connectors.toast.remoteInternal");
    if (code === "target-metadata") return tOutside("connectors.toast.remoteMetadata");
    if (code === "unreachable" || code === "timeout") return tOutside("connectors.toast.remoteUnreachable");
    return tOutside("connectors.toast.notMcp");
  }
  return tOutside("connectors.toast.customFailed");
}

function ConnectorCard({
  connector,
  oauthStatus,
  busy,
  onAction,
}: {
  connector: CatalogConnectorDto;
  oauthStatus?: ConnectorOAuthStatusDto;
  busy: boolean;
  onAction: () => void;
}) {
  const t = useT();
  // the catalogue is the host's, written in English there: its sentence and category in the reader's language
  const shown = catalogText(connector, t);
  const isOAuth = connector.authType === "oauth2";
  // FIX31 Part 2: the host owns the provider sign-in. A card is connectable
  // only when the owner's registration file names this connector AND enables
  // it; otherwise the button is disabled with the honest reason on the card.
  const oauthConnected = isOAuth && oauthStatus?.connected === true;
  const oauthUnavailable = isOAuth && !oauthConnected && !(oauthStatus?.configured && oauthStatus?.enabled);
  const oauthReason = !isOAuth || oauthConnected
    ? null
    : !oauthStatus
      ? t("connectors.oauth.unsupported")
      : !oauthStatus.configured
        ? t("connectors.oauth.notConfigured")
        : !oauthStatus.enabled
          ? t("connectors.oauth.switchedOff")
          : null;
  const actionLabel = isOAuth
    ? oauthConnected
      ? t("connectors.action.disconnect")
      : t("connectors.action.connect")
    : connector.installed
      ? t("connectors.action.uninstall")
      : t("connectors.action.install");
  // FIX89 Part B — what it can do, not what its functions are called.
  const access = connectorAccess(connector.tools, connector.id);

  return (
    <section className="rounded-xl border border-border bg-bg-surface p-5 transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-4">
        {/* FIX32 Part 4.2 — the provider's mark, bundled locally (never a CDN). */}
        <ConnectorIcon connectorId={connector.id} name={connector.name} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <HealthDot status={connector.status} />
            <h3 className="truncate text-base font-medium text-text-primary">
              {connector.name}
            </h3>
            <span className="shrink-0 rounded-full border border-border bg-bg-base px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-text-muted" data-testid="connector-category">
              {shown.category}
            </span>
          </div>
          {shown.description ? (
            <p className="mt-2 text-sm text-text-secondary" data-testid="connector-description">
              {shown.description}
            </p>
          ) : null}
          {access.key ? (
            <p className="mt-3 text-xs text-text-muted" data-testid="connector-access">
              {t(access.key)}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1">
          <Button
            variant={connector.installed || oauthConnected ? "ghost" : "secondary"}
            className="shrink-0"
            onClick={onAction}
            loading={busy}
            disabled={oauthUnavailable}
            title={oauthReason ?? undefined}
            data-testid={isOAuth ? "oauth-action" : undefined}
          >
            {actionLabel}
          </Button>
          {oauthReason ? (
            <span className="max-w-[220px] text-right text-[11px] leading-snug text-text-muted" data-testid="oauth-reason">
              {oauthReason}
            </span>
          ) : oauthConnected ? (
            <span className="max-w-[220px] text-right text-[11px] leading-snug text-text-muted" data-testid="oauth-connected">
              {oauthStatus?.expiresAt ? t("connectors.connectedHereRenews") : t("connectors.connectedHere")}
            </span>
          ) : null}
        </div>
      </div>
    </section>
  );
}

// Health status dot — green when Connected, red when Disconnected, grey
// otherwise (e.g. an uninstalled catalog entry with no live session).
function HealthDot({ status }: { status: string }) {
  const t = useT();
  const color =
    status === "Connected"
      ? "bg-status-success"
      : status === "Disconnected"
        ? "bg-status-danger"
        : "bg-text-muted";
  // The host names the state in English; the three it is known to send are
  // said in the reader's language, any other is shown as the host wrote it.
  const label = connectorStateText(status, t);
  return (
    <span
      className={`h-2 w-2 shrink-0 rounded-full ${color}`}
      title={label}
      aria-label={label}
    />
  );
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function BackIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}
