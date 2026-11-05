"use client";

import { useEffect, useState } from "react";

export function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => {
    try {
      const t = localStorage.getItem("dcr-theme");
      if (t) {
        document.documentElement.dataset.theme = t;
        setTheme(t);
      }
    } catch {
      /* storage unavailable */
    }
  }, []);
  const toggle = () => {
    const cur = theme ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try {
      localStorage.setItem("dcr-theme", next);
    } catch {
      /* ignore */
    }
  };
  return (
    <button onClick={toggle} className="text-xs text-ink2 hover:text-ink" aria-label="Toggle theme">
      {theme === "dark" ? "Light" : "Dark"} mode
    </button>
  );
}
