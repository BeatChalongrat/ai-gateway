// FIX92 — an icon that was meant to be centred, and is not.
//
// The defect, measured on the Scheduled tasks empty state before the fix:
// the wrapper was `<div className="mx-auto mb-5 text-text-muted">` holding
// only a 40 px CloudIcon. Its computed style was `display: block`,
// `width: 1166px` (the full content width of the card) and margins
// `0px / 0px` — so `mx-auto` centred nothing, and the icon sat 563 px left
// of the heading on a 1232 px card. The card's own `text-center` could not
// help either, because Tailwind's preflight ships `svg,video{display:block}`
// (it is in the built stylesheet), so the icon is not inline content.
//
// Why the four sibling empty states (knowledge, projects, skills, tasks)
// were fine with the same `mx-auto`: there it sits directly on the <svg>,
// which carries width={48}. A block box with an INTRINSIC width does
// centre under auto margins. `mx-auto` is not the bug — `mx-auto` on a box
// whose width is `auto` is.
//
// Hence the rule below, which is the honest generalisation:
//
//   `mx-auto` on a block-level element that sets no width is a no-op.
//
// It is scoped to elements holding NO text, because that is the shape where
// the no-op is actually visible as a misalignment: with text the parent's
// `text-align` still centres what you see, so a redundant `mx-auto` there is
// untidy rather than broken, and this round does not restyle. An element
// that is inline-level, or that centres its child with flex, is exempt —
// those centre for real.
//
// Cross-tree in the FIX76/86/87 pattern: this file reads the portal and
// admin trees too, so the same mistake made over there fails the web-chat
// lane, which is the one every push runs.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = resolve(__dirname, "../../../..");

const TREES = [
  "apps/web-chat/src",
  "portal/app",
  "portal/components",
  "admin-ui/app",
  "admin-ui/components",
];

function tsxFiles(dir: string): string[] {
  let out: string[] = [];
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const e of entries) {
    if (e === "node_modules" || e === ".next" || e === "dist") continue;
    const p = resolve(dir, e);
    if (statSync(p).isDirectory()) out = out.concat(tsxFiles(p));
    else if (e.endsWith(".tsx") && !e.includes(".test.")) out.push(p);
  }
  return out;
}

/** Tailwind classes that give the box a width, so auto margins can centre it. */
const SETS_WIDTH = /(?:^|\s)(?:w-|max-w-|min-w-|size-|basis-)\S/;
/**
 * Replaced elements size themselves from their content or their width/height
 * attributes, so a block one centres under auto margins with no width class.
 * This is exactly why `mx-auto` on the <svg width={48}> of the knowledge,
 * projects, skills and tasks empty states works.
 */
const INTRINSIC_TAGS = new Set(["svg", "img", "video", "canvas", "iframe", "object"]);
const HAS_WIDTH_ATTR = /\bwidth\s*=/;
/** Inline-level boxes are centred by the parent's text-align, not by margins. */
const INLINE_LEVEL = /(?:^|\s)inline(?:-block|-flex|-grid)?(?:\s|$)/;
/** A flex parent that really does centre its children. */
const FLEX_CENTRES = /(?:^|\s)justify-center(?:\s|$)/;

/** The class string of a `className="…"` or `className={`…`}` attribute. */
function classOf(attrs: string): string | null {
  const m = attrs.match(/className=(?:"([^"]*)"|\{`([^`]*)`\})/);
  if (!m) return null;
  return (m[1] ?? m[2] ?? "").replace(/\$\{[^}]*\}/g, " ");
}

/** Text a reader would see, ignoring tags, expressions and JSX comments. */
function visibleText(inner: string): string {
  return inner
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/<[^>]*>/g, "")
    .replace(/\{[^}]*\}/g, "")
    .trim();
}

type Finding = { file: string; line: number; tag: string; cls: string };

/**
 * Every plain lowercase HTML element whose only centring mechanism is an
 * `mx-auto` that cannot work. Components are skipped: what `mx-auto` reaches
 * inside one is that component's business.
 */
function scanSource(src: string, label: string): Finding[] {
  const out: Finding[] = [];
  const open = /<([a-z][a-z0-9]*)\b([^>]*?)(\/?)>/gs;
  let m: RegExpExecArray | null;
  while ((m = open.exec(src)) !== null) {
    const [full, tag, attrs, selfClosing] = m;
    if (selfClosing) continue;

    if (INTRINSIC_TAGS.has(tag) || HAS_WIDTH_ATTR.test(attrs)) continue;

    const cls = classOf(attrs);
    if (!cls || !/(?:^|\s)mx-auto(?:\s|$)/.test(cls)) continue;
    if (SETS_WIDTH.test(cls)) continue; // has a width — auto margins work
    if (INLINE_LEVEL.test(cls)) continue; // inline-level — text-align centres it
    if (FLEX_CENTRES.test(cls)) continue; // centres its children for real

    // The element's inner content, up to its matching close tag.
    const start = m.index + full.length;
    const close = new RegExp(`</${tag}>`, "g");
    close.lastIndex = start;
    const c = close.exec(src);
    if (!c) continue;
    const inner = src.slice(start, c.index);

    // Only the shape where the no-op is visible: the element has no text of
    // its own, so nothing in it is being centred by the parent's text-align.
    if (visibleText(inner) !== "") continue;
    if (!/<[A-Za-z]/.test(inner)) continue; // holds no element either

    out.push({ file: label, line: src.slice(0, m.index).split("\n").length, tag, cls });
  }
  return out;
}

const scanFile = (file: string): Finding[] =>
  scanSource(readFileSync(file, "utf8"), file.slice(ROOT.length + 1).replace(/\\/g, "/"));

describe("FIX92 — mx-auto centres only a box that has a width", () => {
  const files = TREES.flatMap((t) => tsxFiles(resolve(ROOT, t)));

  it("reads all three trees", () => {
    // Guards the walker itself: a broken path would make the rule vacuous.
    expect(files.length).toBeGreaterThan(150);
  });

  it("no block wrapper relies on an mx-auto that cannot centre", () => {
    const report = files
      .flatMap(scanFile)
      .map(
        (f) =>
          `${f.file}:${f.line} <${f.tag} className="${f.cls}"> — mx-auto is a no-op here ` +
          `(block box, width:auto). Use "flex justify-center", or put mx-auto on the icon ` +
          `itself, which has an intrinsic width.`,
      )
      .join("\n");
    expect(report).toBe("");
  });

  it("fails on the markup this round replaced", () => {
    // CloudTasksSection's empty state as it stood before FIX92, verbatim,
    // through the same scanner the sweep above uses.
    const before = `
      <div className="rounded-xl border border-dashed px-8 py-14 text-center">
        <div className="mx-auto mb-5 text-text-muted">
          <CloudIcon size={40} />
        </div>
        <h3 className="text-base font-semibold">{t("cloud.empty.title")}</h3>
      </div>`;
    const findings = scanSource(before, "before.tsx");
    expect(findings).toHaveLength(1);
    expect(findings[0].cls).toBe("mx-auto mb-5 text-text-muted");
  });

  it("passes the markup that replaced it, and the idiom the other empty states use", () => {
    const after = `
      <div className="mb-5 flex justify-center text-text-muted">
        <CloudIcon size={40} />
      </div>`;
    // knowledge / projects / skills / tasks: mx-auto directly on a sized <svg>.
    const onTheIcon = `
      <svg width={48} height={48} className="mx-auto mb-6 text-text-muted">
        <path d="M14 3H6a2 2 0 0 0-2 2v14z" />
      </svg>`;
    // A centred text column keeps its mx-auto: max-w-* gives it a width.
    const textColumn = `<p className="mx-auto mt-2 max-w-sm text-sm">{t("x")}</p>`;
    expect(scanSource(after, "after.tsx")).toHaveLength(0);
    expect(scanSource(onTheIcon, "icon.tsx")).toHaveLength(0);
    expect(scanSource(textColumn, "text.tsx")).toHaveLength(0);
  });
});
