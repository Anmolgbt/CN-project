/** Decimal units everywhere (1 MB = 1,000,000 bytes) so sizes and MB/s agree. */
export function bytes(n: number | null | undefined): string {
  if (n == null) return "none";
  if (n < 1000) return `${n}\u00A0B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n, i = -1;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(v >= 10 ? 1 : 2)}\u00A0${units[i]}`;
}

export function rate(bytesPerSec: number | null | undefined): string {
  if (bytesPerSec == null) return "none";
  return `${(bytesPerSec / 1e6).toFixed(bytesPerSec >= 1e7 ? 1 : 2)}\u00A0MB/s`;
}

export function mbps(v: number | null | undefined): string {
  return v == null ? "none" : `${v.toFixed(v >= 10 ? 1 : 2)}\u00A0MB/s`;
}

export function duration(sec: number | null | undefined): string {
  if (sec == null) return "none";
  if (sec < 1) return `${Math.round(sec * 1000)}\u00A0ms`;
  if (sec < 60) return `${sec.toFixed(1)}\u00A0s`;
  const m = Math.floor(sec / 60);
  return `${m}\u00A0min ${Math.round(sec - m * 60)}\u00A0s`;
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

export function dateTime(ts: number | null): string {
  if (!ts) return "none";
  const d = new Date(ts * 1000);
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })}, ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

export const STATUS_LABEL: Record<string, string> = {
  created: "Created", scanning: "Reading and hashing the file", connecting: "Connecting to the receiver",
  transferring: "Sending", paused: "Paused", verifying: "Receiver is checking the file",
  completed: "Complete, integrity verified", incomplete: "Stopped early, can be resumed",
  failed: "Failed", integrity_failed: "Integrity check failed", cancelled: "Cancelled, can be resumed",
};

export type Tone = "ok" | "bad" | "warn" | "run" | "";
export function tone(status: string): Tone {
  if (status === "completed") return "ok";
  if (status === "failed" || status === "integrity_failed") return "bad";
  if (["incomplete", "cancelled", "paused"].includes(status)) return "warn";
  if (["transferring", "verifying", "scanning", "connecting"].includes(status)) return "run";
  return "";
}

/** One colour per TCP connection (Okabe-Ito, colour-blind safe). These are the only saturated colours in the UI. */
export const CONN_COLORS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#7A5195", "#6B7280"];
export const connColor = (id: number): string => CONN_COLORS[((id % 8) + 8) % 8];

/** Black or white text, whichever reads better on a socket colour. */
export function onColor(id: number): string {
  const hex = connColor(id).slice(1);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.3 ? "#14171a" : "#ffffff";
}

/** What to call a finished job in lists: benchmark runs have long internal file names. */
export function jobLabel(h: { kind: string; file_name: string | null; connections: number; connections_used: number | null }): string {
  if (h.kind !== "benchmark") return h.file_name ?? "unnamed file";
  const n = h.connections_used ?? h.connections;
  return `Benchmark run, ${n} ${n === 1 ? "socket" : "sockets"}`;
}
