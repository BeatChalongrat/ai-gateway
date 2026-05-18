import type { Metadata } from "next";

import PageShell from "../_components/PageShell";

export const metadata: Metadata = {
  title: "Security — SRAN",
  description: "How SRAN protects your data, credentials, and AI traffic.",
};

export default function SecurityPage() {
  return (
    <PageShell
      eyebrow="Security"
      title="Security isn't a feature. It's the foundation."
      subtitle="Built with the assumption that every request may contain something sensitive."
    >
      <h2>Data protection on every request</h2>
      <p>
        Before a prompt reaches a model provider, SRAN scans it for sensitive
        data. 30 built-in detectors cover the categories that actually show up
        in AI workflows.
      </p>
      <h3>What we detect by default</h3>
      <ul>
        <li>
          <strong>Credentials</strong> — API keys (OpenAI, AWS, Stripe, GitHub,
          Slack, and more), JWT tokens, private SSH keys, database connection
          strings
        </li>
        <li>
          <strong>Personal data</strong> — email addresses, phone numbers, full
          names when paired with other identifiers, dates of birth, national IDs
        </li>
        <li>
          <strong>Financial data</strong> — credit card numbers, bank account
          numbers, IBAN, SWIFT codes
        </li>
        <li>
          <strong>Health data</strong> — medical record numbers, insurance IDs,
          diagnosis codes (HIPAA-adjacent contexts)
        </li>
        <li>
          <strong>Infrastructure</strong> — internal IP ranges, private
          hostnames, cloud resource ARNs
        </li>
      </ul>
      <p>
        When a match is found, you choose the policy: block the request, redact
        the match, warn and log, or allow with an audit entry.
      </p>

      <h2>Compliance</h2>
      <ul>
        <li>
          <strong>SOC 2 Type II</strong> — audited annually. Report available
          under NDA.
        </li>
        <li>
          <strong>GDPR</strong> — DPA available. EU data can be pinned to EU
          regions.
        </li>
        <li>
          <strong>ISO 27001</strong> — certified.
        </li>
        <li>
          <strong>HIPAA</strong> — BAA available on Enterprise. Healthcare
          workloads supported.
        </li>
      </ul>

      <h2>How we handle your data</h2>
      <h3>In transit</h3>
      <p>
        TLS 1.3 for every external connection. Mutual TLS available for
        Enterprise customers.
      </p>
      <h3>At rest</h3>
      <p>
        AES-256 for logs and metadata. Customer-managed keys (CMK) available on
        Enterprise.
      </p>
      <h3>Retention</h3>
      <p>
        Request bodies are never persisted by default — only metadata (tokens,
        latency, model, timestamp). Optional body logging is opt-in per API key,
        and bodies are encrypted with a tenant-specific key.
      </p>
      <h3>Access</h3>
      <p>
        Production data access is limited to on-call engineers under break-glass
        procedures. Every access is logged and reviewed.
      </p>

      <h2>Reporting a vulnerability</h2>
      <p>
        Send details to <a href="mailto:security@sran.ai">security@sran.ai</a>.
        We acknowledge within 24 hours and publish fixes on a coordinated
        disclosure schedule. We run a private bug bounty — email us for scope
        and rewards.
      </p>

      <h2>Want the details?</h2>
      <p>
        Request our security whitepaper, SOC 2 Type II report, or pen test
        summary at <a href="mailto:security@sran.ai">security@sran.ai</a>.
      </p>
    </PageShell>
  );
}
