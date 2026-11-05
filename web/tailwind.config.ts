import type { Config } from "tailwindcss";

const v = (name: string) => `var(--${name})`;

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        page: v("page"),
        surface: v("surface-1"),
        ink: v("text-primary"),
        ink2: v("text-secondary"),
        muted: v("text-muted"),
        line: v("gridline"),
        border: v("border"),
        accent: v("series-1"),
        good: v("good"),
        warn: v("warning"),
        crit: v("critical"),
      },
      fontFamily: { sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"] },
    },
  },
  plugins: [],
} satisfies Config;
