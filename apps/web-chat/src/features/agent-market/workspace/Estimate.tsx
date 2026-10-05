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

/** AM10: the server's sentence, and the seconds a round takes here (the
 *  organisation's own rounds, else the catalogue sample) — said again when a
 *  round starts, where waiting without knowing how long is its own kind of bad. */
export interface EstimateView {
  line: string | null;
  seconds: number | null;
}

export function useEstimate(installId: string): EstimateView {
  const [view, setView] = useState<EstimateView>({ line: null, seconds: null });
  useEffect(() => {
    let alive = true;
    setView({ line: null, seconds: null });
    amApi.estimate(installId).then(
      (e) =>
        alive &&
        setView({
          line: personText(typeof e?.line === "string" ? e.line.trim() : "") || null,
          seconds: typeof e?.seconds === "number" && Number.isFinite(e.seconds) && e.seconds > 0 ? e.seconds : null,
        }),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [installId]);
  return view;
}

/** "ใช้เวลาประมาณ 2 นาที": rounded the way a person says it. */
export function aboutTime(t: ReturnType<typeof useAmT>, seconds: number | null | undefined): string | null {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 45) return t("time.underAMinute");
  const minutes = Math.max(1, Math.round(seconds / 60));
  return t("time.aboutMinutes", { n: String(minutes) });
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
