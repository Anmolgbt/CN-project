import { useEffect, useState } from "react";
import { api } from "./services/api";
import type { AppConfig, Page } from "./types";
import Overview from "./pages/Overview";
import NewTransfer from "./pages/NewTransfer";
import Live from "./pages/Live";
import Integrity from "./pages/Integrity";
import History from "./pages/History";
import Benchmark from "./pages/Benchmark";

const NAV: { id: Page; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "new", label: "New transfer" }, { id: "live", label: "Live transfer" },
  { id: "integrity", label: "Integrity check" }, { id: "history", label: "History and logs" }, { id: "benchmark", label: "Benchmark" },
];

export interface Nav { go: (p: Page, jobId?: string) => void; jobId: string | null; config: AppConfig | null }

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem("mpft.job"));
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("mpft.theme") ?? "auto");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
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
  };
  const nav: Nav = { go, jobId, config };

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">Multipath File Transfer<small>Parallel TCP sockets, verified with SHA-256</small></div>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <button key={n.id} aria-current={page === n.id ? "page" : undefined} onClick={() => setPage(n.id)}>{n.label}</button>
          ))}
        </nav>
        <div className="side-foot">
          <span className={`chip ${online ? "ok" : online === false ? "bad" : ""}`}><i />{online ? "API connected" : online === false ? "API offline" : "Checking API"}</span>
          {config && <span className="chip"><i />{config.embedded_receiver ? `Receiver on :${config.default_port}` : "External receiver"}</span>}
          <label style={{ margin: 0 }}>Theme
            <select value={theme} onChange={(e) => setTheme(e.target.value)} style={{ marginTop: 4 }}>
              <option value="auto">Match system</option><option value="light">Light</option><option value="dark">Dark</option>
            </select>
          </label>
        </div>
      </aside>
      <main className="main">
        {online === false && (
          <div className="notice bad" role="alert">The dashboard cannot reach the API. Start it with <code>python scripts/run_dashboard.py</code> and reload.</div>
        )}
        {page === "overview" && <Overview nav={nav} />}
        {page === "new" && <NewTransfer nav={nav} />}
        {page === "live" && <Live nav={nav} />}
        {page === "integrity" && <Integrity nav={nav} />}
        {page === "history" && <History nav={nav} />}
        {page === "benchmark" && <Benchmark nav={nav} />}
      </main>
    </div>
  );
}
