"use client";

// /agent-market — the organisation's Agent Market (AM7: moved here from the
// chat app's /chat/agents/admin). chat-edge says who is signed in and which
// sections they may open (GET agent-market/me): an admin gets the first
// seven (and the mail relay once the server offers it), the person who runs
// the agents (agent_publisher) the catalog and the problems queue. Only those sections
// render; chat-edge enforces the same boundary on every endpoint.
//
// The selected section lives in the URL (?section=), so a link can point at
// one. While Agent Market is switched off the whole door answers 404 and the
// page says so calmly.

import { Suspense, useEffect, useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import PageHeader from "../../components/ui/PageHeader";
import LoadingState from "../../components/ui/LoadingState";
import { useT, type DictKey } from "../../lib/i18n";
import { amAdmin, amIsOff, type AmMe, type Section } from "../../lib/agentMarketApi";
import { ChatEdgeApiError } from "../../lib/chatEdgeApi";

import AccessSection from "./_sections/Access";
import CatalogSection from "./_sections/Catalog";
import DepartmentsSection from "./_sections/Departments";
import MailSection from "./_sections/Mail";
import ModelsSection from "./_sections/Models";
import ProblemsSection from "./_sections/Problems";
import RunsSection from "./_sections/Runs";
import SpendSection from "./_sections/Spend";
import WebSection from "./_sections/Web";
import { LoadError, h2, secondary } from "./_sections/shared";

const SECTIONS: { id: Section; label: DictKey }[] = [
  { id: "access", label: "am.tab.access" },
  { id: "departments", label: "am.tab.departments" },
  { id: "spend", label: "am.tab.spend" },
  { id: "models", label: "am.tab.models" },
  { id: "web", label: "am.tab.web" },
  { id: "catalog", label: "am.tab.catalog" },
  { id: "problems", label: "am.tab.problems" },
  { id: "runs", label: "am.tab.runs" },
  { id: "mail", label: "am.tab.mail" },
];

export default function AgentMarketPage() {
  // useSearchParams needs a Suspense boundary when the page is prerendered.
  return (
    <Suspense fallback={<LoadingState />}>
      <AgentMarket />
    </Suspense>
  );
}

function AgentMarket() {
  const t = useT();
  const router = useRouter();
  const params = useSearchParams();
  const asked = params?.get("section") ?? null;
  const [me, setMe] = useState<AmMe | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [n, setN] = useState(0);
  const [picked, setPicked] = useState<string | null>(asked);

  useEffect(() => setPicked(asked), [asked]);

  useEffect(() => {
    let alive = true;
    setError(null);
    amAdmin.me().then(
      (r) => {
        if (alive) setMe(r);
      },
      (err) => {
        if (alive) setError(err);
      },
    );
    return () => {
      alive = false;
    };
  }, [n]);

  const allowed = me ? SECTIONS.filter((s) => (me.sections ?? []).includes(s.id)) : [];
  const current = allowed.find((s) => s.id === picked)?.id ?? allowed[0]?.id ?? null;
  const select = (id: Section) => {
    setPicked(id);
    router.replace(`/agent-market?section=${id}`, { scroll: false });
  };

  const subtitle = me && me.role !== "admin" ? t("am.subtitlePublisher") : t("am.subtitle");

  let body: ReactNode;
  if (error) {
    if (amIsOff(error)) {
      body = <Notice testId="am-off" title={t("am.off.title")} text={t("am.off.body")} />;
    } else if (error instanceof ChatEdgeApiError && (error.status === 401 || error.status === 403)) {
      body = <Notice testId="am-denied" title={t("am.denied.title")} text={t("am.denied.body")} />;
    } else {
      body = <LoadError error={error} onRetry={() => setN((x) => x + 1)} />;
    }
  } else if (!me) {
    body = <LoadingState />;
  } else if (!current) {
    body = <Notice testId="am-denied" title={t("am.denied.title")} text={t("am.denied.body")} />;
  } else {
    body = (
      <>
        <div
          role="tablist"
          aria-label={t("am.tabs")}
          style={{ display: "flex", gap: "0.25rem", overflowX: "auto", borderBottom: "1px solid var(--border-default)", marginBottom: "1.25rem" }}
        >
          {allowed.map((s) => {
            const active = s.id === current;
            return (
              <button
                key={s.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => select(s.id)}
                data-testid={`am-tab-${s.id}`}
                style={{
                  background: "none",
                  border: "none",
                  borderBottom: `2px solid ${active ? "var(--accent-primary)" : "transparent"}`,
                  marginBottom: -1,
                  padding: "0.55rem 0.8rem",
                  fontSize: "0.8125rem",
                  fontWeight: active ? 600 : 500,
                  color: active ? "var(--text-primary)" : "var(--text-secondary)",
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                {t(s.label)}
              </button>
            );
          })}
        </div>
        <div role="tabpanel" data-testid={`am-panel-${current}`}>
          {current === "access" ? (
            <AccessSection />
          ) : current === "departments" ? (
            <DepartmentsSection />
          ) : current === "spend" ? (
            <SpendSection />
          ) : current === "models" ? (
            <ModelsSection />
          ) : current === "web" ? (
            <WebSection />
          ) : current === "catalog" ? (
            <CatalogSection canDisable={!!me.can_disable} />
          ) : current === "problems" ? (
            <ProblemsSection admin={me.role === "admin"} />
          ) : current === "runs" ? (
            <RunsSection />
          ) : (
            <MailSection defaultTo={me.email ?? ""} />
          )}
        </div>
      </>
    );
  }

  return (
    <div>
      <PageHeader title={t("nav.agentMarket")} subtitle={subtitle} />
      {body}
    </div>
  );
}

function Notice({ title, text, testId }: { title: string; text: string; testId: string }) {
  return (
    <div className="sran-card" role="status" data-testid={testId} style={{ maxWidth: "40rem" }}>
      <h2 style={h2}>{title}</h2>
      <p style={{ ...secondary, marginTop: "0.35rem" }}>{text}</p>
    </div>
  );
}
