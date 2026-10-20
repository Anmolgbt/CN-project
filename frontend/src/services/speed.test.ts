import { describe, expect, it } from "vitest";
import { axisTicks, niceStep, speedSeries } from "./speed";
import type { Snapshot } from "../types";
import type { Span } from "./useJob";

const snap = { config: { chunk_size: 1000 }, file: { size: 10_500 } } as unknown as Snapshot;
const span = (chunk: number, t0: number, t1: number): Span => ({ chunk, conn: 0, t0, t1, outcome: "ok" });

describe("speedSeries", () => {
  it("is empty until two chunks have been acknowledged", () => {
    expect(speedSeries([], snap)).toEqual([]);
    expect(speedSeries([span(0, 0, 1)], snap)).toEqual([]);
    expect(speedSeries([{ ...span(0, 0, 1), outcome: "rejected" }, span(1, 1, 2)], snap)).toEqual([]);
  });
  it("derives the average from real acknowledged bytes (the short last chunk counts as 500 B)", () => {
    const spans = [...Array(10).keys()].map((i) => span(i, i, i + 1)).concat([span(10, 10, 11)]);
    const pts = speedSeries(spans, snap);
    const last = pts[pts.length - 1];
    expect(last.average * 1e6).toBeCloseTo(10_500 / 11, 0);        // 10,500 bytes over 11 s
    expect(pts.every((p, i) => i === 0 || p.t > pts[i - 1].t)).toBe(true);
  });
  it("ignores rejected and open spans", () => {
    const spans: Span[] = [span(0, 0, 1), span(1, 1, 2), { chunk: 2, conn: 0, t0: 2, t1: null, outcome: "open" }];
    const last = speedSeries(spans, snap).pop()!;
    expect(last.average * 1e6).toBeCloseTo(2000 / 2, 0);
  });
});

describe("axis ticks", () => {
  it("uses round steps that cover the data", () => {
    expect(niceStep(180)).toBe(50);
    const { max, ticks } = axisTicks(173);
    expect(max).toBeGreaterThanOrEqual(173);
    expect(ticks[0]).toBe(0);
    expect(ticks[ticks.length - 1]).toBe(max);
    expect(ticks.every((t, i) => i === 0 || t > ticks[i - 1])).toBe(true);
  });
  it("handles tiny and zero speeds", () => {
    expect(axisTicks(0.3).max).toBeGreaterThanOrEqual(0.3);
    expect(axisTicks(0).ticks.length).toBeGreaterThan(1);
  });
});
