"use client";

// AM6 — what a round costs is said before it starts, not discovered after:
// one quiet line where a person decides to start a round (the empty
// workspace; above the composer while files are staged). The sentence is
// the server's. While it is being asked for, or when asking failed, nothing
// is shown: a missing estimate never stands in the way of a round.

import { useEffect, useState } from "react";

import { amApi } from "../api";
import { useAmT } from "../i18n";
import { cls } from "../ui";
import { personText } from "./personText";

export function useEstimate(installId: string): string | null {
  const [line, setLine] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setLine(null);
    amApi.estimate(installId).then(
      (e) => alive && setLine(personText(typeof e?.line === "string" ? e.line.trim() : "") || null),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);
  return line;
}

export function EstimateLine({ line, center = false }: { line: string | null | undefined; center?: boolean }) {
  const t = useAmT();
  if (!line) return null;
  return (
    <p className={cls("text-xs text-text-muted", center ? "max-w-xl text-center" : "px-1")} aria-label={t("estimate.aria")} data-testid="am-estimate">
      {line}
    </p>
  );
}
