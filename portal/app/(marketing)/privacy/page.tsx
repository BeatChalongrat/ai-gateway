import type { Metadata } from "next";

import PageShell from "../_components/PageShell";

export const metadata: Metadata = {
  title: "Privacy Policy — SRAN",
  description: "How SRAN collects, uses, and protects your information.",
};

export default function PrivacyPage() {
  return (
    <PageShell
      eyebrow="Legal"
      title="Privacy Policy"
      subtitle="Last updated: April 2026"
    >
      <p>
        This policy explains what SRAN collects, why we collect it, and what we
        do with it. We wrote this in plain language because you should actually
        be able to read it.
      </p>

      <h2>What we collect</h2>
      <h3>Account information</h3>
      <p>
        When you sign up, we collect your email address, a password hash, and
        optionally a display name. That&apos;s it. We don&apos;t ask for your
        phone number, date of birth, or address.
      </p>
      <h3>Usage data</h3>
      <p>
        When you send requests through SRAN, we record: the model used, token
        counts, latency, timestamp, and the API key that made the request. We do{" "}
        <strong>not</strong> log request or response bodies by default.
      </p>
      <h3>Optional body logging</h3>
      <p>
        You can opt in to request/response body logging per API key. Bodies are
        encrypted with a key unique to your organization and retained per your
        plan&apos;s retention window.
      </p>
      <h3>Cookies</h3>
      <p>
        We use a session cookie for authentication. We don&apos;t use
        third-party analytics or ad trackers on our dashboard. The public
        marketing site uses privacy-respecting analytics (Plausible) that
        doesn&apos;t set cookies or collect personal data.
      </p>

      <h2>How we use data</h2>
      <ul>
        <li>
          To operate SRAN — routing your requests, enforcing policies, billing
          you accurately
        </li>
        <li>To detect abuse — rate limits, fraud signals, automated attacks</li>
        <li>
          To improve the product — aggregate usage patterns, never individual
          requests
        </li>
        <li>
          To respond to you — support tickets, security notices, product
          announcements you opted into
        </li>
      </ul>
      <p>
        We do <strong>not</strong> train AI models on your data. We do{" "}
        <strong>not</strong> sell your data. We do <strong>not</strong> share
        your data with advertisers.
      </p>

      <h2>Who we share data with</h2>
      <ul>
        <li>
          <strong>Model providers</strong> — we forward your requests to the
          model provider you chose (OpenAI, Anthropic, etc.), subject to their
          privacy terms
        </li>
        <li>
          <strong>Payment processor</strong> — Stripe handles billing; they get
          your payment details, not your request data
        </li>
        <li>
          <strong>Cloud infrastructure</strong> — AWS hosts our systems under a
          signed DPA
        </li>
        <li>
          <strong>Law enforcement</strong> — only with valid legal process. We
          publish a transparency report annually.
        </li>
      </ul>

      <h2>Your rights</h2>
      <p>You can:</p>
      <ul>
        <li>Export all your data from the dashboard</li>
        <li>Delete your account and all associated data (effective within 30 days)</li>
        <li>
          Ask us what we have on you — email{" "}
          <a href="mailto:privacy@sran.ai">privacy@sran.ai</a>
        </li>
        <li>
          Object to processing or request rectification under GDPR if you&apos;re
          in the EU
        </li>
      </ul>

      <h2>Data location</h2>
      <p>
        Our primary infrastructure is in US-East. EU customers can request
        EU-region pinning on Pro and Enterprise plans.
      </p>

      <h2>Changes to this policy</h2>
      <p>
        When we change this policy materially, we&apos;ll email account holders
        at least 30 days before the change takes effect. Minor clarifications
        will be noted with a revised date at the top of this page.
      </p>

      <h2>Contact</h2>
      <p>
        Privacy questions:{" "}
        <a href="mailto:privacy@sran.ai">privacy@sran.ai</a>
        <br />
        Data requests: <a href="mailto:privacy@sran.ai">privacy@sran.ai</a>
        <br />
        EU representative: contact us for details
      </p>
    </PageShell>
  );
}
