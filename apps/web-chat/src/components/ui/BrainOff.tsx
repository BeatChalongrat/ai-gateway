// AM19 — "memory off": the Brain glyph with the slash every lucide *Off icon carries.
//
// One icon per meaning. Until AM19 the chat drew "memory off", "temporary chat"
// and "thinking mode" with the same BrainCircuit glyph — a person who saw it on
// the composer (the reasoning toggle) and in the sidebar (the temporary-chat
// door) was told they meant the same thing. They do not. Thinking keeps
// BrainCircuit; a temporary chat is the dashed bubble (MessageSquareDashed);
// memory switched off on an ordinary chat is this: the Brain, struck through.
// lucide ships no BrainOff, so it is built from lucide's own Brain paths
// (lucide-react, ISC) plus the slash from its *Off icons, through the same
// factory every other icon in the app comes from, so it takes the same props.

import { createLucideIcon } from "lucide-react";

export const BrainOff = createLucideIcon("brain-off", [
  ["path", { d: "M12 18V5", key: "adv99a" }],
  ["path", { d: "M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4", key: "1e3is1" }],
  ["path", { d: "M17.598 6.5A3 3 0 1 0 12 5a3 3 0 1 0-5.598 1.5", key: "1gqd8o" }],
  ["path", { d: "M17.997 5.125a4 4 0 0 1 2.526 5.77", key: "iwvgf7" }],
  ["path", { d: "M18 18a4 4 0 0 0 2-7.464", key: "efp6ie" }],
  ["path", { d: "M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517", key: "1gq6am" }],
  ["path", { d: "M6 18a4 4 0 0 1-2-7.464", key: "k1g0md" }],
  ["path", { d: "M6.003 5.125a4 4 0 0 0-2.526 5.77", key: "q97ue3" }],
  ["path", { d: "m2 2 20 20", key: "am19off" }],
]);
