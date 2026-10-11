/** Decimal units everywhere (1 MB = 1,000,000 bytes) so sizes and MB/s agree. */
export function bytes(n: number | null | undefined): string {
  if (n == null) return "–";
  if (n < 1000) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n, i = -1;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(v >= 10 ? 1 : 2)} ${units[i]}`;
}

export function rate(bytesPerSec: number | null | undefined): string {
  if (bytesPerSec == null) return "–";
  return `${(bytesPerSec / 1e6).toFixed(bytesPerSec >= 1e7 ? 1 : 2)} MB/s`;
}

export function mbps(v: number | null | undefined): string {
  return v == null ? "–" : `${v.toFixed(v >= 10 ? 1 : 2)} MB/s`;
}

export function duration(sec: number | null | undefined): string {
  if (sec == null) return "–";
  if (sec < 1) return `${Math.round(sec * 1000)} ms`;
  if (sec < 60) return `${sec.toFixed(1)} s`;
  const m = Math.floor(sec / 60);
  return `${m}m ${Math.round(sec - m * 60)}s`;
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

export function dateTime(ts: number | null): string {
  return ts ? new Date(ts * 1000).toLocaleString() : "–";
}

export const STATUS_LABEL: Record<string, string> = {
  created: "Created", scanning: "Hashing file", connecting: "Connecting", transferring: "Transferring",
  paused: "Paused", verifying: "Verifying", completed: "Complete, integrity verified",
  incomplete: "Incomplete (resumable)", failed: "Failed", integrity_failed: "Integrity check failed",
  cancelled: "Cancelled (resumable)",
};

/** One colour per TCP connection (Okabe-Ito, colour-blind safe). */
export const CONN_COLORS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#7A5195", "#6B7280"];
export const connColor = (id: number): string => CONN_COLORS[((id % 8) + 8) % 8];
