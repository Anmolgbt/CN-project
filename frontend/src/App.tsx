import { useEffect, useState } from "react";
import { api } from "./services/api";
import type { AppConfig, Page } from "./types";
import Overview from "./pages/Overview";
import NewTransfer from "./pages/NewTransfer";
import Live from "./pages/Live";
import Integrity from "./pages/Integrity";
import History from "./pages/History";
import Benchmark from "./pages/Benchmark";

const TABS: { id: Page; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "new", label: "New transfer" }, { id: "live", label: "Live transfer" },
  { id: "integrity", label: "Integrity check" }, { id: "history", label: "History" }, { id: "benchmark", label: "Benchmark" },
];

export interface Nav { go: (p: Page, jobId?: string) => void; jobId: string | null; config: AppConfig | null }

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem("mpft.job"));
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("mpft.theme") ?? "system");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    localStorage.setItem("mpft.theme", theme);
  }, [theme]);

  useEffect(() => {
    const ping = () => api.health().then(() => setOnline(true)).catch(() => setOnline(false));
    ping();
    api.config().then(setConfig).catch(() => {});
    const t = setInterval(ping, 5000);
    return () => clearInterval(t);
  }, [online === false]);

  const go = (p: Page, id?: string) => {
    if (id) { setJobId(id); localStorage.setItem("mpft.job", id); }
    setPage(p);
    window.scrollTo({ top: 0 });
  };
  const nav: Nav = { go, jobId, config };

  return (
    <>
      <header className="top">
        <div className="top-in">
          <div className="brand">Multipath file transfer</div>
          <nav className="tabs" aria-label="Pages">
            {TABS.map((t) => (
              <button key={t.id} aria-current={page === t.id ? "page" : undefined} onClick={() => go(t.id)}>{t.label}</button>
            ))}
          </nav>
          <span className={`link-state ${online === false ? "bad" : ""}`} role="status">
            {online === false ? "API not reachable" : online === null ? "Connecting to the API" : config?.embedded_receiver ? `Receiver listening on port ${config.default_port}` : "Using an external receiver"}
          </span>
        </div>
      </header>
      <main className="page">
        {online === false && (
          <p className="note-block bad" role="alert">The dashboard cannot reach the API. Start it with <code>python scripts/run_dashboard.py</code>, then reload this page.</p>
        )}
        {page === "overview" && <Overview nav={nav} />}
        {page === "new" && <NewTransfer nav={nav} />}
        {page === "live" && <Live nav={nav} />}
        {page === "integrity" && <Integrity nav={nav} />}
        {page === "history" && <History nav={nav} />}
        {page === "benchmark" && <Benchmark nav={nav} />}
      </main>
      <footer className="foot">
        <span>Appearance</span>
        {["system", "light", "dark"].map((t) => (
          <button key={t} className="textbtn" style={{ fontWeight: theme === t ? 700 : 400 }} aria-pressed={theme === t} onClick={() => setTheme(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </footer>
    </>
  );
}
