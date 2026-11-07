// FIX81 — the desktop half of the producer tripwire. The rule: a thing
// the assistant produced is a durable, downloadable file in the
// conversation. On the desktop, delivery = attachedFiles on the
// HostToolResult (the FIX80 pipeline: message_files frame + assistant-row
// file_ids at import). This test reads host-tools.ts and fails when a
// host tool produces image/file bytes without either delivering them or
// standing in the documented card-only list — so the next producer
// someone adds is correct by construction, not by memory.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = readFileSync(join(__dirname, "..", "src", "main", "host-tools.ts"), "utf8");

// Tools allowed to return bytes WITHOUT persisting, each with its reason.
// Screenshots are working evidence of an ongoing browser/computer run,
// not a deliverable — auto-filing 20 of them per session would bury the
// conversation's real files (they are savable on demand via the FIX81
// context-menu "Save Image As", which is the intended affordance). NOTE:
// those tools live in the C# sidecar, not here — this list exists for the
// day one moves host-side.
const CARD_ONLY_BY_DESIGN = new Set<string>([]);

function caseBlocks(source: string): Map<string, string> {
  const out = new Map<string, string>();
  const re = /case "([a-z_]+)": \{/g;
  let m: RegExpExecArray | null;
  const starts: { name: string; idx: number }[] = [];
  while ((m = re.exec(source))) starts.push({ name: m[1]!, idx: m.index });
  // The switch's own `default:` caps the LAST case — without this the
  // final block swallows the helper functions below the switch and the
  // tripwire fires on their variable names.
  const defaultIdx = source.lastIndexOf("default:");
  for (let i = 0; i < starts.length; i++) {
    const end = i + 1 < starts.length ? starts[i + 1]!.idx : defaultIdx > 0 ? defaultIdx : source.length;
    out.set(starts[i]!.name, source.slice(starts[i]!.idx, end));
  }
  return out;
}

describe("FIX81 — desktop producer delivery tripwire", () => {
  const blocks = caseBlocks(src);

  it("every host tool that produces bytes delivers them (attachedFiles) or is exempt with a reason", () => {
    for (const [tool, body] of blocks) {
      const produces = /imageDataUri: (?!null)/.test(body) || body.includes("data_uri") || body.includes("dataUri");
      if (!produces || CARD_ONLY_BY_DESIGN.has(tool)) continue;
      expect(
        body.includes("attachedFiles"),
        `host tool "${tool}" produces bytes but never delivers them as ConversationFiles — route them through chatEdge.attachGeneratedFile / the /code/run pipeline (FIX81: one delivery door), or add it to CARD_ONLY_BY_DESIGN with a written reason`,
      ).toBe(true);
    }
  });

  it("the two known producers really are wired", () => {
    expect(blocks.get("generate_image")).toContain("attachGeneratedFile");
    expect(blocks.get("run_python")).toContain("attachedFiles");
  });
});
