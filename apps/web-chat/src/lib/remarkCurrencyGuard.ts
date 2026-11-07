// Currency guard for single-dollar inline math.
//
// remark-math treats `$…$` as inline math, so "ราคาอยู่ระหว่าง $100 ถึง $200"
// typesets "100 ถึง " as a formula — frequent in a product whose users
// discuss prices (audit A4).
//
// The desktop client never had this bug: Markdig's Mathematics extension
// refuses a closing delimiter that is immediately followed by a DIGIT, which
// is exactly what separates "$100 … $200" (two amounts) from "$x$", "$100$"
// or "$2x + 5$" (real math). Verified against Markdig by observation, not
// assumption — see MathPipelineTests.Currency_Pairs_Are_Not_Math.
//
// So this plugin ports that one rule to the web pipeline rather than
// disabling single-dollar math wholesale: disabling would also kill `$x$`,
// which desktop renders and which T1's \(…\) → $…$ conversion depends on,
// trading a common annoyance for a broken feature AND a new cross-client
// divergence. Mirroring Markdig removes the divergence that exists today.
//
// Scope: single-dollar spans only. Currency never appears as `$$100 … $$200`,
// and leaving `$$…$$` untouched keeps display math and T1's mid-paragraph
// output byte-identical.
//
// Runs AFTER remark-math, so the node is already parsed: the guard reverts it
// to the literal source text it came from, which is what the user typed.

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  position?: {
    start: { offset?: number };
    end: { offset?: number };
  };
}

/** A closing `$` followed by a digit means the next `$…` is another amount. */
function closerIsFollowedByDigit(source: string, node: MdNode): boolean {
  const end = node.position?.end?.offset;
  if (end === undefined) return false;
  const next = source.charAt(end);
  return next >= "0" && next <= "9";
}

function usesSingleDollar(source: string, node: MdNode): boolean {
  const start = node.position?.start?.offset;
  if (start === undefined) return false;
  return source.charAt(start) === "$" && source.charAt(start + 1) !== "$";
}

function rawSource(source: string, node: MdNode): string | null {
  const { start, end } = node.position ?? {};
  if (start?.offset === undefined || end?.offset === undefined) return null;
  return source.slice(start.offset, end.offset);
}

export function remarkCurrencyGuard() {
  return (tree: MdNode, file: { value?: unknown }) => {
    const source = typeof file?.value === "string" ? file.value : String(file ?? "");
    if (!source.includes("$")) return;

    const walk = (node: MdNode) => {
      const children = node.children;
      if (!children) return;
      for (let i = 0; i < children.length; i++) {
        const child = children[i];
        if (
          child.type === "inlineMath" &&
          usesSingleDollar(source, child) &&
          closerIsFollowedByDigit(source, child)
        ) {
          const raw = rawSource(source, child);
          if (raw !== null) {
            // Back to plain text — the amounts render as the user wrote them.
            children[i] = { type: "text", value: raw, position: child.position };
            continue;
          }
        }
        walk(child);
      }
    };

    walk(tree);
  };
}
