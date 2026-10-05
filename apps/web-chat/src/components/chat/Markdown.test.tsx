// Markdown.tsx — shared markdown pipeline tests (Phase 12).
//
// Three jobs:
//  1. Regression-pin the pre-existing markdown behaviour (GFM tables,
//     rehype-highlight code blocks, hardened links, citation pills) so
//     extracting the shared component stays behaviour-identical.
//  2. Prove the new LaTeX support (remark-math + rehype-katex): inline
//     $...$, block $$...$$, and graceful degradation on malformed /
//     truncated (mid-stream) input.
//  3. Enforce the security posture: no rehype-raw (raw HTML stays
//     inert), KaTeX trust:false (no \href URL injection), and a
//     source-level scan that every render site uses THIS component.

import fs from "node:fs";
import path from "node:path";

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { KATEX_OPTIONS, Markdown } from "./Markdown";

afterEach(cleanup);

// ── 1. Regression pins for the pre-extraction pipeline ────────────────

describe("existing markdown behaviour (regression pins)", () => {
  it("renders a GFM table as real table elements", () => {
    const md = [
      "| name | qty |",
      "| ---- | --- |",
      "| apples | 3 |",
    ].join("\n");
    const { container } = render(<Markdown>{md}</Markdown>);
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
    expect(table!.querySelector("th")?.textContent).toBe("name");
    expect(table!.querySelectorAll("td")[0]?.textContent).toBe("apples");
  });

  it("wraps tables in a keyboard-reachable horizontal scroll container", () => {
    // The frame + overflow live on a wrapper so the <table> can stay
    // display:table (real column sizing) and still scroll on narrow
    // screens. Pinned because reverting to `display:block` on the table
    // itself silently breaks column widths and rounded corners.
    const md = [
      "| a | b | c |",
      "| - | - | - |",
      "| 1 | 2 | 3 |",
    ].join("\n");
    const { container } = render(<Markdown>{md}</Markdown>);
    const wrap = container.querySelector(".md-table-wrap");
    expect(wrap).not.toBeNull();
    // Table is a direct child of the wrapper, not a sibling.
    expect(wrap!.querySelector(":scope > table")).not.toBeNull();
    // Reachable by keyboard so the hidden columns are not mouse-only.
    expect(wrap!.getAttribute("tabindex")).toBe("0");
    expect(wrap!.getAttribute("role")).toBe("region");
  });

  it("keeps table markup semantic (thead/tbody/th/td survive the wrapper)", () => {
    const md = [
      "| name | qty |",
      "| ---- | --- |",
      "| apples | 3 |",
      "| pears | 5 |",
    ].join("\n");
    const { container } = render(<Markdown>{md}</Markdown>);
    const table = container.querySelector(".md-table-wrap table");
    expect(table).not.toBeNull();
    expect(table!.querySelectorAll("thead th").length).toBe(2);
    expect(table!.querySelectorAll("tbody tr").length).toBe(2);
  });

  it("renders a fenced code block through CodeBlock with highlight classes", () => {
    const md = "```js\nconst x = 1;\n```";
    const { container } = render(<Markdown>{md}</Markdown>);
    // CodeBlock wrapper (custom `pre` component) is in place…
    expect(container.querySelector(".code-block")).not.toBeNull();
    // …and rehype-highlight tokenized the contents.
    expect(container.querySelector("code.hljs")).not.toBeNull();
    expect(container.querySelector(".hljs-keyword")?.textContent).toBe(
      "const",
    );
  });

  it("hardens regular links with target=_blank + noopener", () => {
    const { container } = render(
      <Markdown>{"[docs](https://example.com/docs)"}</Markdown>,
    );
    const a = container.querySelector("a");
    expect(a).not.toBeNull();
    expect(a!.getAttribute("target")).toBe("_blank");
    expect(a!.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("renders [Source: …] markers as citation pills when citations is on", () => {
    const { container } = render(
      <Markdown citations>
        {"See [Source: handbook.pdf] for details"}
      </Markdown>,
    );
    // AM10 F — the pill's tooltip is the dictionary's sentence (kb.cite.from),
    // the one the sources list under an answer already used.
    const pill = container.querySelector(
      '[title="From the knowledge base: handbook.pdf"]',
    );
    expect(pill).not.toBeNull();
    expect(pill!.textContent).toContain("handbook.pdf");
    // The marker must NOT leak through as a normal anchor.
    expect(container.querySelector("a")).toBeNull();
  });

  it("leaves [Source: …] markers as literal text when citations is off (share page behaviour)", () => {
    const { container } = render(
      <Markdown>{"See [Source: handbook.pdf] for details"}</Markdown>,
    );
    expect(container.textContent).toContain("[Source: handbook.pdf]");
    expect(container.querySelector("a")).toBeNull();
  });
});

// ── 2. LaTeX rendering ─────────────────────────────────────────────────

describe("LaTeX via remark-math + rehype-katex", () => {
  it("renders inline $...$ math as KaTeX markup", () => {
    const { container } = render(
      <Markdown>{"Euler: $e^{i\\pi} + 1 = 0$ inline"}</Markdown>,
    );
    expect(container.querySelector(".katex")).not.toBeNull();
    // Inline math must NOT be display-mode.
    expect(container.querySelector(".katex-display")).toBeNull();
  });

  it("renders block $$...$$ math in display mode", () => {
    const md = "Area:\n\n$$\n\\int_0^1 x^2\\,dx = \\tfrac{1}{3}\n$$";
    const { container } = render(<Markdown>{md}</Markdown>);
    expect(container.querySelector(".katex-display")).not.toBeNull();
  });

  // ── Batch 2 T1 — GPT-style \[…\]/\(…\) delimiters ─────────────────
  it("renders \\[…\\] as display math (GPT-family default delimiters)", () => {
    const { container } = render(<Markdown>{"\\[x^2 + 1\\]"}</Markdown>);
    expect(container.querySelector(".katex-display")).not.toBeNull();
  });

  it("renders \\(…\\) as inline math", () => {
    const { container } = render(<Markdown>{"value \\(a+b\\) here"}</Markdown>);
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.querySelector(".katex-display")).toBeNull();
  });

  it("leaves \\[ inside a code fence as literal code", () => {
    const { container } = render(
      <Markdown>{"```latex\n\\[x^2\\]\n```"}</Markdown>,
    );
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.querySelector("code")?.textContent).toContain("\\[x^2\\]");
  });

  it("leaves an unterminated \\[ literal while streaming", () => {
    const { container } = render(<Markdown>{"result is \\[x^2 + "}</Markdown>);
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("[x^2 +");
  });

  it("keeps an unclosed single $ literal (dollar amounts survive)", () => {
    const { container } = render(
      <Markdown>{"The fee is $5 per month"}</Markdown>,
    );
    expect(container.textContent).toContain("$5");
    expect(container.querySelector(".katex")).toBeNull();
  });

  it("degrades gracefully on invalid TeX (no throw, source stays visible)", () => {
    const md = "bad math: $\\frac{a}{$ end";
    let container: HTMLElement | undefined;
    expect(() => {
      container = render(<Markdown>{md}</Markdown>).container;
    }).not.toThrow();
    // rehype-katex catches the KaTeX parse error and renders the raw
    // source in an error span instead of crashing the message.
    expect(container!.textContent).toContain("\\frac{a}{");
  });

  it("does not throw on an unterminated $$ block", () => {
    expect(() =>
      render(<Markdown>{"before\n\n$$\n\\frac{a}{b"}</Markdown>),
    ).not.toThrow();
  });

  it("survives a truncated mid-stream chunk ending inside $$ (streaming interplay)", () => {
    // Simulates what StreamingMarkdown feeds the component while
    // tokens are still arriving: markdown cut mid-formula.
    const truncated =
      "Deriving:\n\n- step 1\n- step 2\n\n$$\n\\begin{aligned} x &= 1";
    let container: HTMLElement | undefined;
    expect(() => {
      container = render(<Markdown citations>{truncated}</Markdown>).container;
    }).not.toThrow();
    // The list before the math still renders normally.
    expect(container!.querySelectorAll("li").length).toBe(2);
  });
});

// ── 3. Security posture ────────────────────────────────────────────────

describe("XSS / injection hardening", () => {
  it("never materializes raw HTML (<script>, <img onerror>) into DOM nodes", () => {
    const md =
      'hello <script>window.__pwned = 1</script> world\n\n<img src=x onerror="window.__pwned = 2">';
    const { container } = render(<Markdown>{md}</Markdown>);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(
      (window as unknown as { __pwned?: number }).__pwned,
    ).toBeUndefined();
  });

  it("does not let \\href inject links (trust stays false)", () => {
    const md = "$\\href{javascript:alert(1)}{click me}$";
    const { container } = render(<Markdown>{md}</Markdown>);
    // With trust:false KaTeX refuses \href — no anchor may exist at
    // all, let alone a javascript: one.
    expect(container.querySelector("a")).toBeNull();
    expect(container.innerHTML).not.toContain("javascript:alert");
  });

  it("pins the KaTeX options: trust must remain false", () => {
    expect(KATEX_OPTIONS.trust).toBe(false);
    expect(KATEX_OPTIONS.output).toBe("html");
  });
});

// ── 4. Single-pipeline invariant (source-level scan) ───────────────────

describe("shared-component invariant", () => {
  const srcRoot = path.join(process.cwd(), "src");
  // FIX133 — the ~430 source files are read ONCE, at collection, and the
  // scans below run on the texts. Each scan used to re-read every file
  // inside its own 5 s test budget; under the full lane's parallel workers
  // the second scan took 10 s on Windows and failed as a timeout.
  const sources = fs
    .readdirSync(srcRoot, { recursive: true })
    .map(String)
    .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.(ts|tsx)$/.test(p))
    .map((p) => path.join(srcRoot, p))
    .map((file) => ({ file, text: fs.readFileSync(file, "utf8") }));

  it("scans the source tree (an empty scan would prove nothing)", () => {
    expect(sources.length).toBeGreaterThan(100);
    expect(sources.some(({ file }) => file.endsWith(path.join("components", "chat", "Markdown.tsx")))).toBe(true);
  });

  it("only Markdown.tsx imports react-markdown (every render site goes through the shared component)", () => {
    const offenders = sources
      .filter(({ file }) => !file.endsWith(path.join("components", "chat", "Markdown.tsx")))
      .filter(({ text }) => /from\s+["']react-markdown["']/.test(text))
      .map(({ file }) => file);
    expect(offenders).toEqual([]);
  });

  it("no source file imports rehype-raw (raw HTML passthrough stays off)", () => {
    const offenders = sources.filter(({ text }) => /["']rehype-raw["']/.test(text)).map(({ file }) => file);
    expect(offenders).toEqual([]);
    // Belt and braces: it must not even be a dependency.
    const pkg = JSON.parse(
      fs.readFileSync(path.join(process.cwd(), "package.json"), "utf8"),
    ) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
    expect(pkg.dependencies?.["rehype-raw"]).toBeUndefined();
    expect(pkg.devDependencies?.["rehype-raw"]).toBeUndefined();
  });

  it("MessageBubble and the public share page import the SAME shared component", () => {
    const bubble = fs.readFileSync(
      path.join(srcRoot, "components", "chat", "MessageBubble.tsx"),
      "utf8",
    );
    const share = fs.readFileSync(
      path.join(srcRoot, "app", "shared", "[token]", "page.tsx"),
      "utf8",
    );
    expect(bubble).toMatch(/import\s+\{\s*Markdown\s*\}\s+from\s+["']\.\/Markdown["']/);
    expect(share).toMatch(
      /import\s+\{\s*Markdown\s*\}\s+from\s+["']@\/components\/chat\/Markdown["']/,
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Currency vs inline math (audit A4). The rule is Markdig's, ported so the
// two clients agree: a closing `$` immediately followed by a digit is not a
// math delimiter, it is the start of the next amount. Everything else that
// rendered as math before must still render as math.
// ─────────────────────────────────────────────────────────────────────────
describe("currency is not math", () => {
  afterEach(cleanup);

  it("does not typeset the text between two Thai baht amounts", () => {
    const { container } = render(
      <Markdown>{"ราคาอยู่ระหว่าง $100 ถึง $200"}</Markdown>,
    );

    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("$100");
    expect(container.textContent).toContain("$200");
    expect(container.textContent).toContain("ถึง");
  });

  it("does not typeset the text between two English amounts", () => {
    const { container } = render(
      <Markdown>{"It costs between $100 and $200 per month"}</Markdown>,
    );

    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("$100 and $200");
  });

  it("does not typeset hyphenated amount ranges", () => {
    const { container } = render(<Markdown>{"costs $100-$200 total"}</Markdown>);

    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("$100-$200");
  });

  it("still renders genuine single-dollar inline math", () => {
    const { container } = render(<Markdown>{"let $x$ be the count"}</Markdown>);

    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.querySelector(".katex-display")).toBeNull();
  });

  it("still renders inline math that merely starts with a digit", () => {
    const { container } = render(<Markdown>{"solve $2x + 5$ now"}</Markdown>);

    expect(container.querySelector(".katex")).not.toBeNull();
  });

  it("still renders a lone amount wrapped as math", () => {
    // "$100$" is a formula by every rule we have; only a digit AFTER the
    // closing delimiter means "second amount".
    const { container } = render(<Markdown>{"exactly $100$ baht"}</Markdown>);

    expect(container.querySelector(".katex")).not.toBeNull();
  });

  it("leaves T1's backslash math untouched while amounts stay literal", () => {
    const { container } = render(
      <Markdown>{"ราคา $100 ถึง $200 แล้ว \\(a+b\\) ครับ"}</Markdown>,
    );

    // The \(…\) formula still typesets (T1 routes it through $…$)...
    expect(container.querySelectorAll(".katex").length).toBe(1);
    // ...while both amounts stay literal text.
    expect(container.textContent).toContain("$100");
    expect(container.textContent).toContain("$200");
  });

  it("leaves $$ display math alone", () => {
    const { container } = render(
      <Markdown>{"ราคา $100 ถึง $200\n\n$$c+d$$"}</Markdown>,
    );

    expect(container.querySelectorAll(".katex").length).toBe(1);
    expect(container.textContent).toContain("$100");
  });
});
