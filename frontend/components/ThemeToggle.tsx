"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

/**
 * Toggles the app between light and dark by adding/removing the `dark` class on
 * <html> (which flips the semantic CSS-variable palette) and persisting the
 * choice to localStorage. The initial class is set before paint by the inline
 * script in app/layout.tsx, so there is no flash.
 */
export default function ThemeToggle({
  className = "",
  showLabel = false,
}: {
  className?: string;
  showLabel?: boolean;
}) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const el = document.documentElement;
    const next = !el.classList.contains("dark");
    el.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* ignore */
    }
    setDark(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="สลับโหมดสว่าง/มืด"
      title={dark ? "สลับเป็นโหมดสว่าง" : "สลับเป็นโหมดมืด"}
      className={className}
    >
      {dark ? <Sun size={16} /> : <Moon size={16} />}
      {showLabel && <span>{dark ? "โหมดสว่าง" : "โหมดมืด"}</span>}
    </button>
  );
}
