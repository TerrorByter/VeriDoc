import { useEffect, useState } from "react";
import { href, type Route } from "../route";
import { Icon } from "./ui";

type Theme = "light" | "dark";
const THEME_KEY = "veridoc-theme-v2";

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

/** Light unless the person picks dark; the system setting is not followed, so the default is the soft light one. */
function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? "light");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* storage blocked: the choice lasts for this visit */
    }
    setTheme(next);
  };
  return [theme, toggle];
}

const NAV: { label: string; to: Route; active: (r: Route) => boolean }[] = [
  { label: "New case", to: { screen: "new" }, active: (r) => r.screen === "new" },
  { label: "Past cases", to: { screen: "cases" }, active: (r) => ["cases", "run", "case"].includes(r.screen) },
  { label: "Model & controls", to: { screen: "controls" }, active: (r) => r.screen === "controls" },
];

export function TopBar({ route }: { route: Route }) {
  const [theme, toggle] = useTheme();
  return (
    <header className="topbar">
      <a className="logo" href={href({ screen: "new" })}>
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
          <rect x="1" y="1" width="20" height="20" rx="5" fill="var(--brand)" />
          <path d="M7 6h5.5L15 8.5V16H7z" stroke="var(--on-brand)" strokeWidth="1.4" strokeLinejoin="round" />
          <path d="M9 11.6l1.4 1.4 2.6-2.8" stroke="var(--on-brand)" strokeWidth="1.4"
            strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>VeriDoc</span>
        <small>Trade finance document review</small>
      </a>
      <nav aria-label="Main">
        {NAV.map((item) => (
          <a
            key={item.label}
            href={href(item.to)}
            className="nav-link"
            aria-current={item.active(route) ? "page" : undefined}
          >
            {item.label}
          </a>
        ))}
      </nav>
      <div className="topbar-end">
        <span className="env-tag" title="Synthetic data only. Findings support a reviewer; they do not prove or rule out fraud.">
          Prototype · synthetic data
        </span>
        <button
          type="button"
          className="icon-btn"
          onClick={toggle}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          <Icon name={theme === "dark" ? "sun" : "moon"} />
        </button>
      </div>
    </header>
  );
}
