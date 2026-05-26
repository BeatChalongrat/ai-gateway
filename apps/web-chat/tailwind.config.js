/** @type {import('tailwindcss').Config} */
// SRAN Web Chat palette — every color resolves through a CSS
// variable so swapping dark ↔ light only flips a `data-theme`
// attribute on <html>, never a rebuild. The legacy token names
// (bg-base, bg-secondary, text-primary, …) stay because the entire
// component tree references them.
module.exports = {
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          base:      "var(--bg-primary)",       // main chat canvas
          secondary: "var(--bg-surface)",       // inputs, surface cards
          surface:   "var(--bg-surface)",       // alias kept for older code
          sidebar:   "var(--bg-sidebar)",
          elevated:  "var(--bg-surface-hover)",
        },
        border: {
          DEFAULT: "var(--border-default)",
          subtle:  "var(--border-subtle)",
          strong:  "var(--border-hover)",
        },
        text: {
          primary:   "var(--text-primary)",
          secondary: "var(--text-secondary)",
          muted:     "var(--text-tertiary)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          hover:   "var(--accent-hover)",
          subtle:  "var(--accent-subtle)",
        },
        bubble: {
          user: "var(--user-bubble-bg)",
        },
        status: {
          success: "var(--success)",
          warning: "var(--warning)",
          danger:  "var(--error)",
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
        brand: ['var(--font-brand)'],
      },
      maxWidth: {
        chat: "768px",
      },
      borderRadius: {
        bubble: "20px",
        input:  "24px",
      },
      keyframes: {
        blink:        { "0%,100%": { opacity: "1" }, "50%": { opacity: "0" } },
        fadeInUp:     { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        pulseDot:     { "0%,80%,100%": { transform: "scale(0.6)", opacity: "0.5" }, "40%": { transform: "scale(1)", opacity: "1" } },
        messageSlide: { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "translateY(0)" } },
      },
      animation: {
        blink:        "blink 1s infinite",
        fadeInUp:     "fadeInUp 240ms ease-out",
        pulseDot:     "pulseDot 1.2s infinite ease-in-out",
        messageSlide: "messageSlide 200ms ease-out",
      },
    },
  },
  plugins: [],
};
