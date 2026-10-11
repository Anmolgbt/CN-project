import type { ReactNode } from "react";
import type { JobStatus } from "../types";
import { STATUS_LABEL } from "../services/format";

export function StatusChip({ status }: { status: JobStatus | string }) {
  const cls = status === "completed" ? "ok"
    : ["failed", "integrity_failed"].includes(status) ? "bad"
    : ["incomplete", "cancelled", "paused"].includes(status) ? "warn"
    : ["transferring", "verifying", "scanning", "connecting"].includes(status) ? "run" : "";
  return <span className={`chip ${cls}`}><i />{STATUS_LABEL[status] ?? status}</span>;
}

export function Progress({ value, tone }: { value: number; tone?: "ok" | "bad" }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <div className={`progress ${tone ?? ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

export function Notice({ tone, children }: { tone?: "bad" | "warn" | "ok"; children: ReactNode }) {
  return <div className={`notice ${tone ?? ""}`} role={tone === "bad" ? "alert" : undefined}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
