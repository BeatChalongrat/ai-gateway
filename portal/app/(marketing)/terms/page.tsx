import type { Metadata } from "next";

import PageShell from "../_components/PageShell";

export const metadata: Metadata = {
  title: "Terms of Service — SRAN",
  description: "The agreement between you and SRAN.",
};

export default function TermsPage() {
  return (
    <PageShell
      eyebrow="Legal"
      title="Terms of Service"
      subtitle="The agreement between you and SRAN — in plain English."
    >
      <p className="legal-updated">Last updated: April 2026</p>

      <nav className="legal-toc" aria-label="On this page">
        <p className="legal-toc-title">On this page</p>
        <ul>
          <li><a href="#the-basics">The basics</a></li>
          <li><a href="#your-account">Your account</a></li>
          <li><a href="#acceptable-use">Acceptable use</a></li>
          <li><a href="#your-content-your-ownership">Your content, your ownership</a></li>
          <li><a href="#our-service-our-ownership">Our service, our ownership</a></li>
          <li><a href="#payment">Payment</a></li>
          <li><a href="#cancellation">Cancellation</a></li>
          <li><a href="#uptime-and-reliability">Uptime and reliability</a></li>
          <li><a href="#limitation-of-liability">Limitation of liability</a></li>
          <li><a href="#indemnification">Indemnification</a></li>
          <li><a href="#changes-to-these-terms">Changes to these terms</a></li>
          <li><a href="#governing-law">Governing law</a></li>
          <li><a href="#contact">Contact</a></li>
        </ul>
      </nav>

      <p>
        These are the terms that govern your use of SRAN. By creating an account
        you agree to them. We tried to write them in plain English. If
        anything&apos;s unclear, <a href="mailto:legal@sran.ai">email us</a>.
      </p>

      <h2 id="the-basics">The basics</h2>
      <p>
        SRAN is an AI gateway service. You send us API requests, we route them
        to model providers, we return the responses. You own your content, we
        run the pipes.
      </p>

      <h2 id="your-account">Your account</h2>
      <ul>
        <li>You must be 18 or older, or have parental consent</li>
        <li>You&apos;re responsible for keeping your credentials safe</li>
        <li>
          One human per account; share credentials and you&apos;re liable for
          what happens
        </li>
        <li>Provide accurate information when you sign up</li>
      </ul>

      <h2 id="acceptable-use">Acceptable use</h2>
      <p>Don&apos;t use SRAN to:</p>
      <ul>
        <li>Break the law in any jurisdiction that applies to you or us</li>
        <li>
          Generate content that sexualizes minors (immediate termination,
          reported to authorities)
        </li>
        <li>
          Build automated systems to cause harm, including harassment or doxing
        </li>
        <li>Attempt to extract, probe, or reverse-engineer model weights</li>
        <li>Circumvent our rate limits, DLP policies, or billing</li>
        <li>Resell raw model access without explicit written permission</li>
      </ul>
      <p>
        You&apos;re also bound by the acceptable use policies of whatever model
        provider you route to.
      </p>

      <h2 id="your-content-your-ownership">Your content, your ownership</h2>
      <p>
        You own the prompts you send and the completions you receive. We
        don&apos;t claim any rights to your content. We don&apos;t train models
        on your content. We process your content only to provide the service.
      </p>

      <h2 id="our-service-our-ownership">Our service, our ownership</h2>
      <p>
        SRAN&apos;s software, brand, design, and documentation are ours. You get
        a limited license to use the service while your account is active and
        you&apos;re paying your bill (if applicable).
      </p>

      <h2 id="payment">Payment</h2>
      <ul>
        <li>
          Free tier has a monthly token allowance. Exceed it and requests fail
          until next cycle or you upgrade.
        </li>
        <li>
          Pro is billed monthly in advance. Overage is billed at the end of each
          cycle.
        </li>
        <li>Enterprise is billed per the order form.</li>
        <li>
          Late payments accrue interest at 1.5% per month or the max allowed by
          law, whichever is lower.
        </li>
      </ul>

      <h2 id="cancellation">Cancellation</h2>
      <p>
        You can cancel anytime from the dashboard. Paid plans run through the
        end of the current billing period. We don&apos;t prorate refunds for
        partial months.
      </p>

      <h2 id="uptime-and-reliability">Uptime and reliability</h2>
      <p>
        We target 99.9% monthly uptime. Enterprise has contractual SLAs with
        credits. Free and Pro are provided as-is — we do our best, but
        we&apos;re not liable for outages.
      </p>

      <h2 id="limitation-of-liability">Limitation of liability</h2>
      <p>
        To the maximum extent allowed by law, our total liability for any claim
        arising from your use of SRAN is capped at the amount you paid us in the
        12 months before the claim arose. We&apos;re not liable for indirect,
        consequential, or punitive damages.
      </p>
      <p>
        Nothing here excludes liability for fraud, death or personal injury
        caused by negligence, or anything else that can&apos;t legally be
        limited.
      </p>

      <h2 id="indemnification">Indemnification</h2>
      <p>
        You&apos;ll cover our costs if someone sues us because of how you used
        the service — for example, if you generated content that infringed
        their rights.
      </p>

      <h2 id="changes-to-these-terms">Changes to these terms</h2>
      <p>
        We&apos;ll email you at least 30 days before material changes take
        effect. If you don&apos;t agree with the changes, you can cancel before
        they take effect.
      </p>

      <h2 id="governing-law">Governing law</h2>
      <p>
        These terms are governed by the laws of the jurisdiction where SRAN is
        incorporated. Disputes go to the courts of that jurisdiction unless
        you&apos;re a consumer with statutory rights elsewhere.
      </p>

      <h2 id="contact">Contact</h2>
      <p>
        Legal questions: <a href="mailto:legal@sran.ai">legal@sran.ai</a>
      </p>
    </PageShell>
  );
}
