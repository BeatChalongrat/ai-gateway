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
import { useHostedMode } from "@/hooks/useHostedMode";
import { hostApi, type CatalogConnectorDto } from "@/lib/hostApi";

export default function ConnectorsPage() {
  const hosted = useHostedMode();
  // Non-hosted guard: this screen only makes sense inside the desktop
  // shell where the host bridge is present. On the web build, show a
  // muted notice instead of attempting host.query calls that can't land.
  if (!hosted) {
    return (
      <div className="flex flex-1 items-center justify-center bg-bg-base px-6">
        <p className="max-w-sm text-center text-sm text-text-muted">
          This screen is available in the SRAN Desktop app.
        </p>
      </div>
    );
  }
  return <ConnectorMarketplace />;
}

function ConnectorMarketplace() {
  const [connectors, setConnectors] = useState<CatalogConnectorDto[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState("");
  // Ids with an action in flight — disables that card's button so a
  // double-tap can't fire two native dialogs.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [addingCustom, setAddingCustom] = useState(false);

  async function reload() {
    try {
      const list = await hostApi.listConnectorCatalog();
      setConnectors(list);
    } catch {
      toast("Couldn't load the connector catalog.");
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
    if (!q) return connectors;
    return connectors.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q),
    );
  }, [connectors, query]);

  async function handleAddCustom() {
    // Spawn consent for a custom stdio MCP server is a NATIVE host
    // dialog — approving a child process is never rendered by the page
    // (Phase 5 §2.4). We only ask the host to open it and reload on success.
    setAddingCustom(true);
    try {
      const res = await hostApi.addCustomConnector();
      if (!res.ok) {
        toast(
          res.error?.startsWith("test-failed")
            ? "That program didn't respond as an MCP server."
            : res.error === "addcustom-unsupported"
              ? "Adding custom connectors isn't available in this build."
              : "Couldn't add a custom connector.",
        );
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
        const res = c.installed
          ? await hostApi.oauthDisconnect(c.id)
          : await hostApi.oauthConnect(c.id);
        if (!res.ok) {
          toast(
            res.error === "oauth-unsupported"
              ? "OAuth connectors aren't available in the desktop app yet — they need your sign-in to reach the provider, which a later round wires up. API-key and no-auth connectors work now."
              : c.installed
                ? "Couldn't disconnect this connector."
                : "Couldn't connect this connector.",
          );
          return;
        }
        await reload();
        return;
      }

      // authType "none" | "api_key". For api_key the host opens a native
      // key prompt; the page never sees or sends the key.
      if (c.installed) {
        const res = await hostApi.uninstallConnector(c.id);
        if (!res.ok) {
          toast("Couldn't uninstall this connector.");
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
            ? "Key-based connectors need a secure key input, coming in a later round. No-auth and custom connectors work now."
            : "Couldn't install this connector.",
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
          Back
        </Link>

        <div className="mt-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
              Connectors
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              Install tools the assistant can call. API keys and access
              approvals are handled by the desktop app.
            </p>
          </div>
          <Button
            variant="secondary"
            className="shrink-0"
            onClick={() => void handleAddCustom()}
            loading={addingCustom}
          >
            <PlusIcon />
            Add custom connector
          </Button>
        </div>

        <div className="mt-6">
          <Input
            placeholder="Search connectors…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search connectors"
          />
        </div>

        <div className="mt-6">
          {!loaded ? (
            <div className="flex items-center justify-center py-10 text-text-secondary">
              <Spinner size={20} />
            </div>
          ) : filtered.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border bg-transparent px-8 py-16 text-center text-sm text-text-muted">
              {connectors.length === 0
                ? "No connectors are available yet."
                : "No connectors match your search."}
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {filtered.map((c) => (
                <ConnectorCard
                  key={c.id}
                  connector={c}
                  busy={busyIds.has(c.id)}
                  onAction={() => void handleAction(c)}
                />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connector card
// ---------------------------------------------------------------------------

function ConnectorCard({
  connector,
  busy,
  onAction,
}: {
  connector: CatalogConnectorDto;
  busy: boolean;
  onAction: () => void;
}) {
  const isOAuth = connector.authType === "oauth2";
  const actionLabel = isOAuth
    ? connector.installed
      ? "Disconnect"
      : "Connect"
    : connector.installed
      ? "Uninstall"
      : "Install";

  return (
    <section className="rounded-xl border border-border bg-bg-surface p-5 transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <HealthDot status={connector.status} />
            <h3 className="truncate text-base font-medium text-text-primary">
              {connector.name}
            </h3>
            <span className="shrink-0 rounded-full border border-border bg-bg-base px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
              {connector.category}
            </span>
          </div>
          {connector.description ? (
            <p className="mt-2 text-sm text-text-secondary">
              {connector.description}
            </p>
          ) : null}
          {connector.tools.length > 0 ? (
            <p className="mt-3 truncate text-xs text-text-muted">
              {connector.tools.join(" · ")}
            </p>
          ) : null}
        </div>

        <Button
          variant={connector.installed ? "ghost" : "secondary"}
          className="shrink-0"
          onClick={onAction}
          loading={busy}
        >
          {actionLabel}
        </Button>
      </div>
    </section>
  );
}

// Health status dot — green when Connected, red when Disconnected, grey
// otherwise (e.g. an uninstalled catalog entry with no live session).
function HealthDot({ status }: { status: string }) {
  const color =
    status === "Connected"
      ? "bg-status-success"
      : status === "Disconnected"
        ? "bg-status-danger"
        : "bg-text-muted";
  return (
    <span
      className={`h-2 w-2 shrink-0 rounded-full ${color}`}
      title={status}
      aria-label={status}
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
