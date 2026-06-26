import type { Metadata } from "next";

import PageShell from "../_components/PageShell";
import CodeTabs from "../_components/CodeTabs";

export const metadata: Metadata = {
  title: "Docs — SRAN",
  description: "API reference and guides for SRAN AI Gateway.",
};

const QUICKSTART: { label: string; code: string }[] = [
  {
    label: "curl",
    code: `curl https://api.sran.ai/v1/chat/completions \\
  -H "Authorization: Bearer $SRAN_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-4o",
    "messages": [{ "role": "user", "content": "Hello" }]
  }'`,
  },
  {
    label: "Python",
    code: `from openai import OpenAI

client = OpenAI(
    api_key=os.environ["SRAN_API_KEY"],
    base_url="https://api.sran.ai/v1",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
)
print(resp.choices[0].message.content)`,
  },
  {
    label: "Node",
    code: `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.SRAN_API_KEY,
  baseURL: "https://api.sran.ai/v1",
});

const res = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Hello" }],
});
console.log(res.choices[0].message.content);`,
  },
];

export default function DocsPage() {
  return (
    <PageShell
      eyebrow="Developer docs"
      title="Start sending requests in minutes."
      subtitle="SRAN is OpenAI-compatible. Point your SDK at our base URL — your existing code stays the same."
    >
      <div className="reveal">
        <h2>Quickstart</h2>
        <p>
          Change two things — the base URL and your API key — and every request
          is routed, DLP-scanned, and logged through SRAN.
        </p>
        <CodeTabs tabs={QUICKSTART} />
      </div>

      <div className="reveal">
        <h2>Reference</h2>
        <p>The essentials to get a request through the gateway today:</p>
        <dl className="docs-ref">
          <div className="docs-ref-row">
            <dt>Base URL</dt>
            <dd>
              <code>https://api.sran.ai/v1</code>
            </dd>
          </div>
          <div className="docs-ref-row">
            <dt>Authentication</dt>
            <dd>
              <code>Authorization: Bearer &lt;SRAN_API_KEY&gt;</code> — create a
              key in your project under <strong>Projects → API Keys</strong>.
            </dd>
          </div>
          <div className="docs-ref-row">
            <dt>Endpoint</dt>
            <dd>
              <code>POST /v1/chat/completions</code> — OpenAI-compatible request
              and response shape.
            </dd>
          </div>
          <div className="docs-ref-row">
            <dt>Models</dt>
            <dd>
              Pass any model your tenant has enabled (e.g. <code>gpt-4o</code>),
              or <code>auto</code> to let routing pick by your policy.
            </dd>
          </div>
        </dl>
        <p>
          Responses match the OpenAI schema, so existing SDKs and tools work
          unchanged. Errors return standard HTTP status codes with a JSON{" "}
          <code>error</code> body.
        </p>
      </div>

      <div className="reveal">
        <h2>What&apos;s coming</h2>
        <ul>
          <li>Full API reference — every endpoint, every parameter</li>
          <li>Guides for routing policies, DLP rules, and cost controls</li>
          <li>SDK examples in Python, TypeScript, Go, and Ruby</li>
          <li>Webhook reference for usage alerts and policy events</li>
        </ul>
      </div>

      <div className="reveal">
        <h2>Questions?</h2>
        <p>
          Reach us at <a href="mailto:support@sran.ai">support@sran.ai</a> — we
          read every email and usually reply within a day.
        </p>
      </div>
    </PageShell>
  );
}
