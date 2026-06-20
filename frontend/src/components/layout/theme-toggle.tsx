"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";

type Theme = "light" | "dark";

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
  localStorage.setItem("arkon-theme", theme);
  window.dispatchEvent(new CustomEvent<Theme>("arkon-theme-change", { detail: theme }));
}

export function ThemeToggle({ className }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const current = document.documentElement.classList.contains("dark") ? "dark" : "light";
    setTheme(current);
    setMounted(true);

    const syncTheme = (event: Event) => setTheme((event as CustomEvent<Theme>).detail);
    window.addEventListener("arkon-theme-change", syncTheme);
    return () => window.removeEventListener("arkon-theme-change", syncTheme);
  }, []);

  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={() => {
        setTheme(nextTheme);
        applyTheme(nextTheme);
      }}
      className={cn(
        "theme-toggle inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-background/60 px-3 text-xs font-semibold text-muted-foreground transition-all hover:border-primary/40 hover:text-primary",
        className
      )}
      aria-label={`Switch to ${nextTheme} theme`}
      title={`Switch to ${nextTheme} theme`}
    >
      <span className="relative size-4" aria-hidden="true">
        <Sun className={cn("absolute inset-0 size-4 transition-all", theme === "light" ? "rotate-0 scale-100" : "rotate-90 scale-0")} />
        <Moon className={cn("absolute inset-0 size-4 transition-all", theme === "dark" ? "rotate-0 scale-100" : "-rotate-90 scale-0")} />
      </span>
      <span>{mounted ? (theme === "dark" ? "Dark" : "Light") : "Theme"}</span>
    </button>
  );
}
