/* Colored pill badge for DLP detector categories.
   Light-theme palette — tinted bg, saturated text, subtle border. */

const PALETTE: Record<string, { bg: string; fg: string; border: string }> = {
  network:    { bg: "rgba(59,130,246,0.07)",  fg: "#2563eb", border: "rgba(59,130,246,0.15)" },
  identity:   { bg: "rgba(139,92,246,0.07)",  fg: "#7c3aed", border: "rgba(139,92,246,0.15)" },
  financial:  { bg: "rgba(5,150,105,0.07)",   fg: "#059669", border: "rgba(5,150,105,0.15)" },
  pii:        { bg: "rgba(202,138,4,0.07)",   fg: "#b45309", border: "rgba(202,138,4,0.15)" },
  credential: { bg: "rgba(220,38,38,0.07)",   fg: "#dc2626", border: "rgba(220,38,38,0.15)" },
  secret:     { bg: "rgba(100,116,139,0.07)", fg: "#475569", border: "rgba(100,116,139,0.15)" },
  ner:        { bg: "rgba(20,184,166,0.07)",  fg: "#0d9488", border: "rgba(20,184,166,0.15)" },
};

export default function CategoryPill({ category }: { category: string }) {
  const c = PALETTE[category] ?? { bg: "rgba(100,116,139,0.07)", fg: "#475569", border: "rgba(100,116,139,0.15)" };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "0.0625rem 0.4375rem",
        borderRadius: 4,
        fontSize: "0.6875rem",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.03em",
        background: c.bg,
        color: c.fg,
        border: `1px solid ${c.border}`,
        lineHeight: 1.5,
      }}
    >
      {category}
    </span>
  );
}
