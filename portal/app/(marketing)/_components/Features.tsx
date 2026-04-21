// Each feature card is a slide-up reveal: front face is the original
// icon/title/body/metric, back face is a centred big-stat panel —
// one number, one short label, one one-sentence caption, all tinted by
// the card's accent colour. No links inside the back face, so the
// <article> itself carries tabIndex={0}; :focus-within on the card
// drives the same slide-up reveal as :hover.
export function Features() {
  return (
    <section className="features" id="features">
      <div className="container">
        <div className="section-head reveal">
          <div className="eyebrow">Built for teams shipping AI</div>
          <h2 className="section-title">Three things every AI app needs.</h2>
          <p className="section-sub">
            Safety, savings, and visibility — without adding complexity to your code.
          </p>
        </div>

        <div className="feat-grid">
          {/* Card 1 — Data safety */}
          <article className="feat feat-card safety reveal reveal-delay-1" tabIndex={0}>
            <div className="feat-face feat-face--front">
              <div className="feat-icon">
                <svg viewBox="0 0 32 32" width="28" height="28" fill="none" aria-hidden="true">
                  <path d="M16 3l11 4v8c0 6-5 11-11 13-6-2-11-7-11-13V7l11-4z" fill="#6E5EF6" fillOpacity="0.15" />
                  <path d="M16 3l11 4v8c0 6-5 11-11 13-6-2-11-7-11-13V7l11-4z" stroke="#6E5EF6" strokeWidth="2" strokeLinejoin="round" />
                  <path d="M11 16l3.5 3.5L21 13" stroke="#6E5EF6" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <h3>Keep your data safe</h3>
              <p>
                Automatic scanning for emails, credit cards, API keys, and 19 other
                sensitive types — before they leave your app.
              </p>
              <div className="feat-highlight">
                <span className="feat-highlight-label">Prevents leaks across</span>
                <span className="feat-highlight-val violet">22 data types</span>
              </div>
            </div>
            <div className="feat-face feat-face--back">
              <div className="feat-stat-num">22</div>
              <div className="feat-stat-label">data types detected</div>
              <div className="feat-stat-caption">
                Credentials, personal info, financial data, and custom patterns.
              </div>
            </div>
          </article>

          {/* Card 2 — Cost routing */}
          <article className="feat feat-card cost reveal reveal-delay-2" tabIndex={0}>
            <div className="feat-face feat-face--front">
              <div className="feat-icon">
                <svg viewBox="0 0 32 32" width="28" height="28" fill="none" aria-hidden="true">
                  <ellipse cx="16" cy="9" rx="10" ry="4" fill="#10B981" fillOpacity="0.15" />
                  <ellipse cx="16" cy="9" rx="10" ry="4" stroke="#10B981" strokeWidth="2" />
                  <path d="M6 9v6c0 2.2 4.5 4 10 4s10-1.8 10-4V9" stroke="#10B981" strokeWidth="2" fill="#10B981" fillOpacity="0.08" />
                  <path d="M6 15v6c0 2.2 4.5 4 10 4s10-1.8 10-4v-6" stroke="#10B981" strokeWidth="2" />
                  <path d="M16 5v4M14 7h4" stroke="#10B981" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </div>
              <h3>Spend less, automatically</h3>
              <p>
                Our router sends easy questions to fast, cheap models — and saves
                the expensive ones for when they matter.
              </p>
              <div className="feat-highlight">
                <span className="feat-highlight-label">Average bill reduction</span>
                <span className="feat-highlight-val green">−38%</span>
              </div>
            </div>
            <div className="feat-face feat-face--back">
              {/* U+2212 (Unicode minus) so the typographic weight matches
                  the "38" — hyphen-minus reads too short next to it. */}
              <div className="feat-stat-num">
                −38<span className="unit">%</span>
              </div>
              <div className="feat-stat-label">average bill reduction</div>
              <div className="feat-stat-caption">
                Easy tasks route to cheap models. Expensive ones save for when it matters.
              </div>
            </div>
          </article>

          {/* Card 3 — Observability */}
          <article className="feat feat-card insight reveal reveal-delay-3" tabIndex={0}>
            <div className="feat-face feat-face--front">
              <div className="feat-icon">
                <svg viewBox="0 0 32 32" width="28" height="28" fill="none" aria-hidden="true">
                  <rect x="3" y="3" width="26" height="26" rx="4" fill="#3DB5C7" fillOpacity="0.12" />
                  <rect x="3" y="3" width="26" height="26" rx="4" stroke="#3DB5C7" strokeWidth="2" />
                  <path d="M8 20l5-6 4 4 7-8" stroke="#3DB5C7" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                  <circle cx="24" cy="10" r="2.5" fill="#3DB5C7" />
                </svg>
              </div>
              <h3>See what&apos;s happening</h3>
              <p>
                Every request logged, every dollar tracked. Spot problems fast with
                real-time alerts on cost, latency, and errors.
              </p>
              <div className="feat-highlight">
                <span className="feat-highlight-label">Full log retention</span>
                <span className="feat-highlight-val">90 days</span>
              </div>
            </div>
            <div className="feat-face feat-face--back">
              <div className="feat-stat-num">
                90<span className="unit">d</span>
              </div>
              <div className="feat-stat-label">full log retention</div>
              <div className="feat-stat-caption">
                Latency, cost, errors — searchable for 3 months.
              </div>
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
