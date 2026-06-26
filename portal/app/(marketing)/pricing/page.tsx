import type { Metadata } from "next";
import { Check } from "lucide-react";

import PageShell from "../_components/PageShell";

export const metadata: Metadata = {
  title: "Pricing — SRAN",
  description: "Buy tokens when you need them. No subscription. No expiry.",
};

export default function PricingPage() {
  return (
    <>
      <PageShell
        eyebrow="Pricing"
        title="Pay as you go. No subscription."
        subtitle="Start with 200,000 free tokens on signup. Top up with a token pack whenever you need more — they never expire."
      />

      <section className="pricing-section">
        <div className="container">
          <div className="pricing-grid">
            <article className="pricing-card">
              <div className="pricing-name">Starter</div>
              <div className="pricing-tokens">
                <span className="pricing-tokens-num">1</span>
                <span className="pricing-tokens-unit">M</span>
              </div>
              <div className="pricing-tokens-label">TOKENS</div>
              <div className="pricing-price">
                <span className="pricing-price-currency">$</span>
                <span className="pricing-price-num">10</span>
              </div>
              <ul className="pricing-features">
                <li><Check size={16} /> All models</li>
                <li><Check size={16} /> No expiry</li>
                <li><Check size={16} /> Email support</li>
              </ul>
              <a href="/signup" className="pricing-cta secondary">Get started</a>
            </article>

            <article className="pricing-card featured">
              <div className="pricing-name">Pro</div>
              <div className="pricing-tokens">
                <span className="pricing-tokens-num">5</span>
                <span className="pricing-tokens-unit">M</span>
              </div>
              <div className="pricing-tokens-label">TOKENS</div>
              <div className="pricing-price">
                <span className="pricing-price-currency">$</span>
                <span className="pricing-price-num">40</span>
              </div>
              <ul className="pricing-features">
                <li><Check size={16} /> All models</li>
                <li><Check size={16} /> No expiry</li>
                <li><Check size={16} /> Priority support</li>
              </ul>
              <a href="/signup" className="pricing-cta primary">Get started</a>
            </article>

            <article className="pricing-card">
              <div className="pricing-name">Business</div>
              <div className="pricing-tokens">
                <span className="pricing-tokens-num">20</span>
                <span className="pricing-tokens-unit">M</span>
              </div>
              <div className="pricing-tokens-label">TOKENS</div>
              <div className="pricing-price">
                <span className="pricing-price-currency">$</span>
                <span className="pricing-price-num">120</span>
              </div>
              <ul className="pricing-features">
                <li><Check size={16} /> All models</li>
                <li><Check size={16} /> No expiry</li>
                <li><Check size={16} /> Dedicated support</li>
              </ul>
              <a href="/signup" className="pricing-cta secondary">Get started</a>
            </article>
          </div>
        </div>
      </section>

      <section className="page-body">
        <div className="container">
          <div className="page-content">
            <h2>Free trial</h2>
            <p>
              Every new account starts with <strong>200,000 tokens</strong> on
              the house — no credit card, no trial period, no clock running
              out. Test SRAN on your actual workload before you buy a pack.
            </p>

            <h2>Questions we get asked</h2>
            <h3>What counts as a &ldquo;token&rdquo;?</h3>
            <p>
              Same as your upstream provider counts them. We pass the
              provider&apos;s token count through on every response — no
              repackaging, no markup on the raw inference cost.
            </p>
            <h3>Do tokens expire?</h3>
            <p>
              No. Tokens you buy stay in your account until you use them. Pause
              for a month or a year — they&apos;re still there.
            </p>
            <h3>What happens when I run out?</h3>
            <p>
              Requests fail with a clear &ldquo;insufficient balance&rdquo;
              error. Buy another pack from the billing page and you&apos;re
              back online instantly. No auto-renewal, no surprise charges.
            </p>
            <h3>Do you mark up model costs?</h3>
            <p>
              You pay for tokens at our pack rate. Our margin is in the pack
              pricing — the underlying provider cost is absorbed. What you see
              is what you pay.
            </p>
            <h3>Need more than 20M tokens?</h3>
            <p>
              Email <a href="mailto:sales@sran.ai">sales@sran.ai</a> for volume
              pricing, SLAs, and a DPA. We handle 100M+ token deployments on a
              case-by-case basis.
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
