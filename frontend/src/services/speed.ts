import type { Snapshot } from "../types";
import type { Span } from "./useJob";

export interface SpeedPoint { t: number; current: number; average: number }

/**
 * Speed curve drawn from the chunks the receiver acknowledged (real bytes at real times), so it works
 * while a transfer runs, when the page is opened half way through, and for finished transfers.
 * `current` uses a trailing window of about 2 s; `average` is everything acknowledged so far.
 */
export function speedSeries(spans: Span[], snap: Snapshot): SpeedPoint[] {
  const cs = snap.config.chunk_size, size = snap.file?.size ?? 0;
  const acked = spans.filter((s) => s.outcome === "ok" && s.t1 != null).sort((a, b) => (a.t1 as number) - (b.t1 as number));
  if (acked.length < 2 || cs <= 0) return [];
  const t0 = Math.min(...acked.map((s) => s.t0));
  const tEnd = acked[acked.length - 1].t1 as number;
  const total = tEnd - t0;
  if (total <= 0) return [];
  const step = Math.max(total / 70, 0.1);
  const window = Math.max(2, step * 2);
  const len = (id: number) => Math.max(0, Math.min(cs, size - id * cs));
  const pts: SpeedPoint[] = [];
  let i = 0, cum = 0;
  const done: { t: number; b: number }[] = [];
  for (let k = 1; ; k++) {
    const t = Math.min(t0 + k * step, tEnd);           // never look past the last acknowledgment
    while (i < acked.length && (acked[i].t1 as number) <= t) { const b = len(acked[i].chunk); cum += b; done.push({ t: acked[i].t1 as number, b }); i++; }
    const inWin = done.filter((d) => d.t > t - window).reduce((a, d) => a + d.b, 0);
    const span = Math.max(Math.min(window, t - t0), Math.min(window, 1));   // too-short windows give misleading spikes
    pts.push({ t: t - t0, current: inWin / span / 1e6, average: cum / (t - t0) / 1e6 });
    if (t >= tEnd) break;
  }
  return pts;
}

/** A round step (1, 2, 5 times a power of ten) that gives about `target` intervals over `range`. */
export function niceStep(range: number, target = 4): number {
  const raw = Math.max(range, 1e-9) / target;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

/** Axis ticks 0..max in round steps that cover `top`. */
export function axisTicks(top: number): { max: number; ticks: number[] } {
  if (!(top > 0)) top = 1;                                 // no data yet: show a plain 0 to 1 axis
  const step = niceStep(top, 4);
  const max = Math.ceil(top / step) * step;
  return { max, ticks: Array.from({ length: Math.round(max / step) + 1 }, (_, i) => +(i * step).toFixed(6)) };
}
