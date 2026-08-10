import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

// Semantic colours backed by CSS variables (see app/globals.css). Using
// `<alpha-value>` keeps opacity utilities working, e.g. bg-surface/80.
const themeColor = (v: string) => `rgb(var(${v}) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        app: themeColor("--c-app"),
        surface: themeColor("--c-surface"),
        surface2: themeColor("--c-surface2"),
        surface3: themeColor("--c-surface3"),
        content: themeColor("--c-content"),
        content2: themeColor("--c-content2"),
        muted: themeColor("--c-muted"),
        faint: themeColor("--c-faint"),
        line: themeColor("--c-line"),
      },
    },
  },
  plugins: [typography],
};

export default config;
