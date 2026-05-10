import { createContext, useContext, useEffect, useState } from "react";

type Theme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

const STORAGE_KEY = "meeting-agent-theme";

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  try {
    const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
    if (stored && (stored === "light" || stored === "dark")) {
      return stored;
    }
  } catch {
    // ignore (e.g. jsdom without localStorage)
  }
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  return mq?.matches ? "dark" : "light";
}

// Apply theme synchronously before first paint to avoid FOIT
function applyThemeSync(theme: Theme) {
  try {
    document.documentElement.setAttribute("data-theme", theme);
  } catch {
    // ignore during SSR
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Initialize with the correct theme synchronously to prevent flash
  const [theme, setThemeState] = useState<Theme>(() => getInitialTheme());

  // Apply theme to DOM immediately on theme state change
  useEffect(() => {
    applyThemeSync(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // ignore
    }
  }, [theme]);

  const setTheme = (value: Theme) => setThemeState(value);

  const toggleTheme = () => setThemeState((prev) => (prev === "light" ? "dark" : "light"));

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return ctx;
}
