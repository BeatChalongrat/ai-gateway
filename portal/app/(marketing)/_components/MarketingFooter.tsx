function ThaiFlag() {
  return (
    <svg
      viewBox="0 0 900 600"
      width="18"
      height="12"
      aria-label="Thailand"
      style={{
        display: "inline-block",
        verticalAlign: "-1px",
        borderRadius: 2,
        marginLeft: 6,
      }}
    >
      <rect width="900" height="600" fill="#A51931" />
      <rect y="100" width="900" height="400" fill="#F4F5F8" />
      <rect y="200" width="900" height="200" fill="#2D2A4A" />
    </svg>
  );
}

export function MarketingFooter() {
  return (
    <footer className="marketing-footer">
      <div className="container footer-inner">
        <div className="footer-links">
          <a href="/docs">Docs</a>
          <a href="/pricing">Pricing</a>
          <a href="/security">Security</a>
          <a href="/privacy">Privacy</a>
          <a href="/terms">Terms</a>
          <a href="mailto:support@sran.ai">Contact</a>
        </div>
        <div className="footer-copy">
          © 2026 SRAN · Built in Thailand
          <ThaiFlag />
        </div>
      </div>
    </footer>
  );
}
