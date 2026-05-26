/** @type {import('tailwindcss').Config} */
// SRAN Web Chat palette — mirrors the admin-ui design system but
// trimmed to the few tokens the chat surface actually uses.
module.exports = {
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          base: "#060A1D",         // app canvas (deep navy)
          secondary: "#0F1835",    // raised surfaces
          surface: "#0A1230",      // cards
          sidebar: "#080F28",      // sidebar
          elevated: "#101A3A",     // hover surfaces
        },
        border: {
          DEFAULT: "#1E293B",
          subtle: "#162041",
          strong: "#293f6a",
        },
        text: {
          primary: "#F1F5F9",
          secondary: "#94A3B8",
          muted: "#64748B",
        },
        accent: {
          DEFAULT: "#3B82F6",
          hover: "#2563EB",
          subtle: "#1D4ED8",
        },
        bubble: {
          user: "#1E3A5F",
        },
        status: {
          success: "#10B981",
          warning: "#F59E0B",
          danger: "#EF4444",
        },
      },
      fontFamily: {
        sans: ['var(--font-outfit)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-jetbrains)', 'ui-monospace', 'monospace'],
      },
      keyframes: {
        blink: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
        fadeInUp: {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        pulseDot: {
          "0%, 80%, 100%": { transform: "scale(0.6)", opacity: "0.5" },
          "40%": { transform: "scale(1)", opacity: "1" },
        },
      },
      animation: {
        blink: "blink 1s infinite",
        fadeInUp: "fadeInUp 240ms ease-out",
        pulseDot: "pulseDot 1.2s infinite ease-in-out",
      },
    },
  },
  plugins: [],
};
