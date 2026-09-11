// FIX107 Part B2 — how to choose a format, byte for byte chat-edge's canonical
// services/chat-edge-api/internal/service/prompt_output_format.txt (the web
// prompt's <output_format> layer). The desktop had no format rule at all.
// test/prompt-output-format.test.ts fails on drift. Regenerated from the
// asset, not edited by hand.
export const OUTPUT_FORMAT_BLOCK = "<output_format>\n- Choose the format that fits the content; do not default to bullet points.\n- Use a markdown table for structured or comparative data — comparisons, test results, feature lists, status updates, anything with 2+ attributes across multiple items.\n- Use a numbered list for sequential steps, prose for explanations and discussion, and code blocks for code.\n</output_format>";
