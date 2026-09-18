import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "var(--paper)",
        surface: "var(--surface)",
        ink: "var(--ink)",
        rule: "var(--rule)",
        steel: "var(--steel)",
        moss: "var(--moss)",
        amber: "var(--amber)",
        signal: "var(--signal)",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "-apple-system", "Segoe UI", "sans-serif"],
      },
      fontSize: {
        28: ["28px", { lineHeight: "34px" }],
        20: ["20px", { lineHeight: "26px" }],
        15: ["15px", { lineHeight: "20px" }],
        13: ["13px", { lineHeight: "18px" }],
        11: ["11px", { lineHeight: "14px" }],
      },
      spacing: {
        row: "32px",
      },
      borderRadius: {
        DEFAULT: "6px",
      },
      keyframes: {
        "flash-once": {
          "0%, 100%": { backgroundColor: "transparent" },
          "30%": { backgroundColor: "var(--steel-10)" },
        },
      },
      animation: {
        "flash-once": "flash-once 600ms ease-out 1",
      },
    },
  },
  plugins: [],
};
export default config;
