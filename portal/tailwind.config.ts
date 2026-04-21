import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          dark: "#0A1A38",
          navy: "#1E3A5F",
          blue: "#3B6CF6",
          green: "#10B981",
          orange: "#F59E0B",
          red: "#EF4444",
        },
        bg: {
          page: "#F8F9FB",
          card: "#FFFFFF",
        },
      },
      fontFamily: {
        heading: ["Outfit", "sans-serif"],
        body: ["Inter", "sans-serif"],
      },
      keyframes: {
        "aurora-1": {
          "0%, 100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(50px,40px) scale(1.15)" },
        },
        "aurora-2": {
          "0%, 100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(-40px,-30px) scale(1.2)" },
        },
        "aurora-3": {
          "0%, 100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(30px,-40px) scale(1.1)" },
        },
        "text-shine": {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
        "pulse-ring": {
          "0%": { transform: "scale(1)", opacity: "0.7" },
          "100%": { transform: "scale(1.2)", opacity: "0" },
        },
        "sheen": {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
        "sweep": {
          "0%, 100%": { transform: "translateY(-100px)", opacity: "0" },
          "50%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      animation: {
        "aurora-1": "aurora-1 18s ease-in-out infinite",
        "aurora-2": "aurora-2 22s ease-in-out infinite reverse",
        "aurora-3": "aurora-3 26s ease-in-out infinite",
        "text-shine": "text-shine 6s linear infinite",
        "pulse-ring": "pulse-ring 2.5s ease-out infinite",
        "sheen": "sheen 4s linear infinite",
        "sweep": "sweep 8s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
