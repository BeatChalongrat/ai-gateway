const STEPS = [
  {
    n: 1,
    title: "Sign up — it's free",
    body:
      "Create your account and get 200,000 free tokens to test with. No credit card needed.",
  },
  {
    n: 2,
    title: "Swap one line",
    body:
      "Change your base URL to point at SRAN. Your existing code keeps working — same SDKs, same responses.",
  },
  {
    n: 3,
    title: "Ship with confidence",
    body:
      "Watch requests flow through your dashboard. Adjust routes and policies whenever you need.",
  },
];

export function HowItWorks() {
  return (
    <section className="how" id="how">
      <div className="container">
        <div className="section-head reveal">
          <div className="eyebrow">Get started in 3 steps</div>
          <h2 className="section-title">Up and running in under 5 minutes.</h2>
        </div>

        {/* id="steps" lets the ScrollReveal client component find this
            element and add ``revealed`` to draw the connector line. */}
        {/* Steps are made keyboard-focusable (tabIndex=0) so the
            :has(.step:focus-within) selector in marketing.css can
            apply the focus-blur effect for keyboard users. */}
        <div className="steps" id="steps">
          {STEPS.map((s, i) => (
            <div
              key={s.n}
              className={`step reveal reveal-delay-${i + 1}`}
              tabIndex={0}
            >
              <div className="step-num">{s.n}</div>
              <h4>{s.title}</h4>
              <p>{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
