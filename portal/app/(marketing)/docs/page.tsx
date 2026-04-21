import type { Metadata } from "next";

import PageShell from "../_components/PageShell";

export const metadata: Metadata = {
  title: "Docs — SRAN",
  description: "API reference and guides for SRAN AI Gateway.",
};

export default function DocsPage() {
  return (
    <PageShell
      eyebrow="Developer docs"
      title="Documentation is on the way."
      subtitle="We're polishing a proper docs site. In the meantime, here's the quickstart and what's coming."
    >
      <h2>Quickstart</h2>
      <p>
        SRAN is an OpenAI-compatible gateway. Change your base URL, everything else
        stays the same.
      </p>
      <pre>
        <code>{`import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.SRAN_API_KEY,
  baseURL: "https://api.sran.ai/v1",
});

const response = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Hello" }],
});`}</code>
      </pre>

      <h2>What&apos;s coming</h2>
      <ul>
        <li>Full API reference — every endpoint, every parameter</li>
        <li>Guides for routing policies, DLP rules, and cost controls</li>
        <li>SDK examples in Python, TypeScript, Go, and Ruby</li>
        <li>Webhook reference for usage alerts and policy events</li>
      </ul>

      <h2>Questions?</h2>
      <p>
        Reach us at <a href="mailto:support@sran.ai">support@sran.ai</a> — we read
        every email and usually reply within a day.
      </p>
    </PageShell>
  );
}
