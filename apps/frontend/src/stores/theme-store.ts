// Dark/light mode store. Reads/writes localStorage and applies the `dark`
// class to <html>. Default is dark.

import { create } from "zustand";

export type Theme = "light" | "dark";

interface ThemeStoreState {
  theme: Theme;
  setTheme(t: Theme): void;
  toggle(): void;
  hydrate(): void;
}

const STORAGE_KEY = "factory.theme";

function applyTheme(t: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("dark", t === "dark");
}

export const useThemeStore = create<ThemeStoreState>((set, get) => ({
  theme: "dark",
  setTheme: (t) => {
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(STORAGE_KEY, t);
      } catch {
        // ignore quota / disabled storage
      }
    }
    applyTheme(t);
    set({ theme: t });
  },
  toggle: () => {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    get().setTheme(next);
  },
  hydrate: () => {
    if (typeof window === "undefined") return;
    let stored: Theme | null = null;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw === "light" || raw === "dark") stored = raw;
    } catch {
      // ignore
    }
    const t: Theme = stored ?? "dark";
    applyTheme(t);
    set({ theme: t });
  },
}));
