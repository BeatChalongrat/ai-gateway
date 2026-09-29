"use client";

// AM7 — one read-only line on the Models and Policies pages: Agent Market
// keeps its own switch for documents leaving the organisation (and its
// retention), set on the Agent Market page, not here.

import Link from "next/link";

import { useT } from "../lib/i18n";

export default function AgentMarketPointer({ page }: { page: "models" | "policies" }) {
  const t = useT();
  const sentence = t(page === "models" ? "am.pointer.models" : "am.pointer.policies");
  const at = sentence.indexOf("{link}");
  const link = (
    <Link href="/agent-market?section=models" style={{ color: "var(--accent-primary)", textDecoration: "underline", textUnderlineOffset: 2 }}>
      {t("am.pointer.link")}
    </Link>
  );
  return (
    <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", margin: "-0.5rem 0 1rem" }} data-testid="am-pointer">
      {at < 0 ? (
        sentence
      ) : (
        <>
          {sentence.slice(0, at)}
          {link}
          {sentence.slice(at + "{link}".length)}
        </>
      )}
    </p>
  );
}
