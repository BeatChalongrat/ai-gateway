import type { Metadata } from "next";
import {
  BadgeCheck,
  CreditCard,
  FileCheck,
  Globe,
  HeartPulse,
  Key,
  Server,
  ShieldCheck,
  User,
} from "lucide-react";

import PageShell from "../_components/PageShell";

export const metadata: Metadata = {
  title: "Security — SRAN",
  description: "How SRAN protects your data, credentials, and AI traffic.",
};

const DETECTORS = [
  {
    Icon: Key,
    title: "Credentials",
    body: "API keys (OpenAI, AWS, Stripe, GitHub, Slack, and more), JWT tokens, private SSH keys, and database connection strings.",
  },
  {
    Icon: User,
    title: "Personal data",
    body: "Email addresses, phone numbers, full names paired with other identifiers, dates of birth, and national IDs.",
  },
  {
    Icon: CreditCard,
    title: "Financial data",
    body: "Credit card numbers, bank account numbers, IBAN, and SWIFT codes.",
  },
  {
    Icon: HeartPulse,
    title: "Health data",
    body: "Medical record numbers, insurance IDs, and diagnosis codes for HIPAA-adjacent contexts.",
  },
  {
    Icon: Server,
    title: "Infrastructure",
    body: "Internal IP ranges, private hostnames, and cloud resource ARNs.",
  },
];

const COMPLIANCE = [
  { Icon: ShieldCheck, name: "SOC 2 Type II", note: "Audited annually · report under NDA" },
  { Icon: Globe, name: "GDPR", note: "DPA available · EU data pinned to EU" },
  { Icon: BadgeCheck, name: "ISO 27001", note: "Certified" },
  { Icon: FileCheck, name: "HIPAA", note: "BAA on Enterprise" },
];

export default function SecurityPage() {
  return (
    <PageShell
      eyebrow="Security"
      title="Security isn't a feature. It's the foundation."
      subtitle="Built with the assumption that every request may contain something sensitive."
    >
      <div className="reveal">
        <h2>Data protection on every request</h2>
        <p>
          Before a prompt reaches a model provider, SRAN scans it for sensitive
          data. 30 built-in detectors cover the categories that actually show up
          in AI workflows. When a match is found, you choose the policy: block
          the request, redact the match, warn and log, or allow with an audit
          entry.
        </p>
        <div className="sec-grid">
          {DETECTORS.map(({ Icon, title, body }) => (
            <div key={title} className="sec-card">
              <div className="sec-card-icon">
                <Icon size={20} strokeWidth={1.75} />
              </div>
              <h3>{title}</h3>
              <p>{body}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="reveal">
        <h2>Compliance</h2>
        <div className="compliance-grid">
          {COMPLIANCE.map(({ Icon, name, note }) => (
            <div key={name} className="compliance-item">
              <div className="compliance-item-icon">
                <Icon size={22} strokeWidth={1.75} />
              </div>
              <div>
                <div className="compliance-item-name">{name}</div>
                <div className="compliance-item-note">{note}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="reveal">
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
          latency, model, timestamp). Optional body logging is opt-in per API
          key, and bodies are encrypted with a tenant-specific key.
        </p>
        <h3>Access</h3>
        <p>
          Production data access is limited to on-call engineers under
          break-glass procedures. Every access is logged and reviewed.
        </p>
      </div>

      <div className="reveal">
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
      </div>
    </PageShell>
  );
}
