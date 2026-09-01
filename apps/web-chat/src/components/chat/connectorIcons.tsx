// FIX32 Part 4.2 — connector brand marks, shipped LOCALLY.
//
// Three rules, none of them taste:
//   • official brand assets — the paths come from the simple-icons package
//     (the providers' published marks, brand hex included), bundled at build
//     time into our JS;
//   • NEVER hotlinked — no <img src="https://cdn…">: a remote logo is an
//     egress channel that tells that host who uses what, indefensible in a
//     DLP product. The strict places these render are covered by the app's
//     own CSP either way; the rule is that no asset leaves our origin.
//   • a neutral fallback mark for custom connectors and any provider whose
//     mark we cannot bundle. Slack is currently in that bucket: Slack had its
//     icon removed from simple-icons at the company's request, and its brand
//     kit requires a manual download under Slack's own terms — bundling that
//     is the owner's call, so Slack renders the neutral mark until then.
//
// FIX42 D2 — the owner's own file wins. Accepting a brand kit's terms is the
// owner's decision, not this code's, so nothing here downloads anything. What
// the code does is look for a mark the owner PLACED:
//
//     apps/web-chat/public/icons/connectors/<connectorId>.svg
//
// served from our own origin (no egress — same rule as above). Priority is
// owner file → simple-icons → neutral mark. The file is probed client-side
// with an Image() load; until it resolves, and whenever it is absent, the
// component renders exactly what it rendered before, so a missing file is
// not a broken icon and not a flash. To ship the Slack mark: download the
// SVG from Slack's brand kit (https://slack.com/media-kit), accept Slack's
// terms yourself, save it as public/icons/connectors/slack.svg, and rebuild
// the web-chat image (public/ is copied at build time — see the repo's
// "web-chat stale image" note).
//
// Google marks note: Google's OAuth BRANDING requirements apply to the
// sign-in button itself (rendered by Google's own consent page in the system
// browser — Part 2's flow); these card marks identify the product being
// connected and use Google's published product icons from simple-icons.

import { useEffect, useState } from "react";
import { siGithub, siGmail, siGooglecalendar, siGoogledrive, siJira, siNotion, siPostgresql } from "simple-icons";

interface Mark {
  path: string;
  hex: string;
  title: string;
}

const MARKS: Record<string, Mark> = {
  gmail: siGmail,
  gdrive: siGoogledrive,
  gcal: siGooglecalendar,
  notion: siNotion,
  github: siGithub,
  jira: siJira,
  postgres: siPostgresql,
};

/** Same-origin path of the owner-placed mark for a connector. Exported so a
 *  test can assert the exact URL that is probed (and that it never leaves
 *  our origin). Ids are restricted to a safe charset so a connector id can
 *  never become a path segment it should not be. */
export function ownerMarkPath(connectorId: string): string | null {
  return /^[a-z0-9][a-z0-9._-]{0,63}$/i.test(connectorId) ? `/icons/connectors/${connectorId}.svg` : null;
}

// One probe per id per page life; the answer does not change underneath a
// running page (public/ is baked into the image).
const ownerMarkCache = new Map<string, boolean>();

/** true → the owner placed a file; false → no file; null → not known yet. */
export function useOwnerMark(connectorId: string): boolean | null {
  const src = ownerMarkPath(connectorId);
  const [known, setKnown] = useState<boolean | null>(() =>
    src ? (ownerMarkCache.get(src) ?? null) : false,
  );
  useEffect(() => {
    if (!src) return;
    const cached = ownerMarkCache.get(src);
    if (cached !== undefined) {
      setKnown(cached);
      return;
    }
    if (typeof Image === "undefined") return; // SSR / non-DOM: keep the pre-FIX42 mark
    let alive = true;
    const img = new Image();
    img.onload = () => {
      ownerMarkCache.set(src, true);
      if (alive) setKnown(true);
    };
    img.onerror = () => {
      ownerMarkCache.set(src, false);
      if (alive) setKnown(false);
    };
    img.src = src;
    return () => {
      alive = false;
    };
  }, [src]);
  return known;
}

/** Test seam — forget probe results between tests. */
export function resetOwnerMarkCache(): void {
  ownerMarkCache.clear();
}

export function ConnectorIcon({ connectorId, name }: { connectorId: string; name: string }) {
  const mark = MARKS[connectorId];
  const owner = useOwnerMark(connectorId);
  if (owner === true) {
    // The owner's file, from our own origin. Not a hotlink: the path is
    // relative, so it can only ever resolve against the page's origin.
    return (
      <span
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-white"
        data-testid={`connector-icon-owner-${connectorId}`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- a plain same-origin SVG; next/image adds nothing here */}
        <img src={ownerMarkPath(connectorId) ?? undefined} alt={`${name} logo`} width={20} height={20} />
      </span>
    );
  }
  if (!mark) {
    // Neutral mark — custom connectors, and providers without a bundleable
    // official asset (Slack today; see the header comment).
    return (
      <span
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-bg-base text-text-muted"
        data-testid="connector-icon-fallback"
        aria-hidden
      >
        <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7zM12 16v5" />
        </svg>
      </span>
    );
  }
  return (
    <span
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-white"
      data-testid={`connector-icon-${connectorId}`}
    >
      <svg role="img" aria-label={`${name} logo`} viewBox="0 0 24 24" width={20} height={20} fill={`#${mark.hex}`}>
        <path d={mark.path} />
      </svg>
    </span>
  );
}
