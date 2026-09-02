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
          base:      "rgb(var(--bg-primary-rgb) / <alpha-value>)",       // main chat canvas
          secondary: "rgb(var(--bg-surface-rgb) / <alpha-value>)",       // inputs, surface cards
          surface:   "rgb(var(--bg-surface-rgb) / <alpha-value>)",       // alias kept for older code
          sidebar:   "rgb(var(--bg-sidebar-rgb) / <alpha-value>)",
          elevated:  "rgb(var(--bg-surface-hover-rgb) / <alpha-value>)",
        },
        border: {
          DEFAULT: "var(--border-default)",
          subtle:  "var(--border-subtle)",
          strong:  "var(--border-hover)",
        },
        text: {
          primary:   "rgb(var(--text-primary-rgb) / <alpha-value>)",
          secondary: "rgb(var(--text-secondary-rgb) / <alpha-value>)",
          muted:     "rgb(var(--text-tertiary-rgb) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent-rgb) / <alpha-value>)",
          hover:   "rgb(var(--accent-hover-rgb) / <alpha-value>)",
          subtle:  "var(--accent-subtle)",
        },
        bubble: {
          user: "rgb(var(--user-bubble-bg-rgb) / <alpha-value>)",
        },
        status: {
          success: "rgb(var(--success-rgb) / <alpha-value>)",
          warning: "rgb(var(--warning-rgb) / <alpha-value>)",
          danger:  "rgb(var(--error-rgb) / <alpha-value>)",
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
