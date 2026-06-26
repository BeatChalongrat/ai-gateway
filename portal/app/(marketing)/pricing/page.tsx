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
            <article className="pricing-card reveal">
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
              <div className="pricing-rate">$10 / 1M tokens</div>
              <ul className="pricing-features">
                <li><Check size={16} /> All models</li>
                <li><Check size={16} /> No expiry</li>
                <li><Check size={16} /> Email support</li>
              </ul>
              <a href="/signup" className="pricing-cta secondary">Get started</a>
            </article>

            <article className="pricing-card featured reveal reveal-delay-1">
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
              <div className="pricing-rate">$8 / 1M tokens</div>
              <ul className="pricing-features">
                <li><Check size={16} /> All models</li>
                <li><Check size={16} /> No expiry</li>
                <li><Check size={16} /> Priority support</li>
              </ul>
              <a href="/signup" className="pricing-cta primary">Get started</a>
            </article>

            <article className="pricing-card reveal reveal-delay-2">
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
              <div className="pricing-rate">$6 / 1M tokens</div>
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
            <div className="faq-list">
              <details className="faq-item">
                <summary>What counts as a &ldquo;token&rdquo;?</summary>
                <p>
                  Same as your upstream provider counts them. We pass the
                  provider&apos;s token count through on every response — no
                  repackaging, no markup on the raw inference cost.
                </p>
              </details>
              <details className="faq-item">
                <summary>Do tokens expire?</summary>
                <p>
                  No. Tokens you buy stay in your account until you use them.
                  Pause for a month or a year — they&apos;re still there.
                </p>
              </details>
              <details className="faq-item">
                <summary>What happens when I run out?</summary>
                <p>
                  Requests fail with a clear &ldquo;insufficient balance&rdquo;
                  error. Buy another pack from the billing page and you&apos;re
                  back online instantly. No auto-renewal, no surprise charges.
                </p>
              </details>
              <details className="faq-item">
                <summary>Do you mark up model costs?</summary>
                <p>
                  You pay for tokens at our pack rate. Our margin is in the pack
                  pricing — the underlying provider cost is absorbed. What you
                  see is what you pay.
                </p>
              </details>
              <details className="faq-item">
                <summary>Need more than 20M tokens?</summary>
                <p>
                  Email <a href="mailto:sales@sran.ai">sales@sran.ai</a> for
                  volume pricing, SLAs, and a DPA. We handle 100M+ token
                  deployments on a case-by-case basis.
                </p>
              </details>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
