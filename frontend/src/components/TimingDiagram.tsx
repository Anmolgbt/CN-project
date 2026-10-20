import { useEffect, useRef, useState } from "react";
import { connColor, duration, onColor } from "../services/format";
import type { Span } from "../services/useJob";
import { TERMINAL, type Snapshot } from "../types";

const LEFT = 78, RIGHT = 14, TRACK = 30, GAP = 6, AXIS = 30, WINDOW = 30;

function niceStep(range: number, target = 8): number {
  const raw = range / target;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

/**
 * Time runs left to right, one track per TCP socket. Each bar is one chunk, from the instant it was sent
 * to the instant the receiver acknowledged it. Everything is drawn from real chunk_started / chunk_acked /
 * chunk_nack / conn_error events; nothing is simulated.
 */
export function TimingDiagram({ snap, spans }: { snap: Snapshot; spans: Span[] }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [tip, setTip] = useState("");

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => setWidth(Math.max(640, el.clientWidth - 4));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const live = !TERMINAL.includes(snap.status);
  const conns = snap.connections.map((c) => c.conn_id);
  if (conns.length === 0 || spans.length === 0)
    return <p className="empty">The diagram starts as soon as the first chunk is sent.</p>;

  const origin = spans[0].t0;
  const lastSeen = spans.reduce((m, s) => Math.max(m, s.t1 ?? s.t0), origin);
  const end = Math.max(live ? snap.now : lastSeen, origin + 0.05);
  const windowed = live && end - origin > WINDOW;
  const start = windowed ? end - WINDOW : origin;
  const range = end - start;
  const plotW = width - LEFT - RIGHT;
  const x = (t: number) => LEFT + ((t - start) / range) * plotW;
  const height = conns.length * (TRACK + GAP) + AXIS;
  const step = niceStep(range);
  const ticks: number[] = [];
  for (let t = Math.ceil((start - origin) / step) * step; t <= end - origin + 1e-9; t += step) ticks.push(t);
  const fmt = (s: number) => (step < 1 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`);

  const drawn = spans.filter((s) => (s.t1 ?? end) >= start);
  return (
    <div>
      <div className="diagram" ref={wrap}>
        <svg width={width} height={height} role="img"
          aria-label={`Timing diagram: ${conns.length} sockets, ${drawn.length} chunk transfers`}>
          <defs>
            <pattern id="inflight" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="3" height="6" fill="var(--paper)" opacity="0.55" />
            </pattern>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(origin + t)} x2={x(origin + t)} y1={0} y2={height - AXIS + 4} stroke="var(--rule)" strokeWidth={1} />
              <text className="axis" x={x(origin + t)} y={height - 10} textAnchor="middle">{fmt(t)}</text>
            </g>
          ))}
          {conns.map((id, row) => {
            const y = row * (TRACK + GAP);
            return (
              <g key={id}>
                <rect x={LEFT} y={y} width={plotW} height={TRACK} fill="var(--faint)" opacity={0.7} />
                <text className="track-label" x={0} y={y + TRACK / 2 + 4}>Socket {id}</text>
                {drawn.filter((s) => s.conn === id).map((s, i) => {
                  const x0 = Math.max(LEFT, x(s.t0));
                  const x1 = Math.min(LEFT + plotW, x(s.t1 ?? end));
                  const w = Math.max(2, x1 - x0);
                  const col = connColor(id);
                  const label = `Chunk ${s.chunk} on socket ${id}: ` + (
                    s.outcome === "ok" ? `acknowledged after ${duration((s.t1 ?? s.t0) - s.t0)}`
                    : s.outcome === "open" ? "still in flight"
                    : s.outcome === "rejected" ? "rejected by the receiver, sent again later"
                    : "connection broke mid-chunk, sent again later");
                  const bad = s.outcome === "rejected" || s.outcome === "dropped";
                  return (
                    <g key={`${s.chunk}-${i}`} onMouseEnter={() => setTip(label)} onMouseLeave={() => setTip("")}>
                      <rect className="bar" x={x0} y={y + 3} width={w} height={TRACK - 6}
                        fill={bad ? "var(--paper)" : col}
                        stroke={s.outcome === "rejected" ? "var(--bad)" : s.outcome === "dropped" ? "var(--ink)" : "var(--paper)"}
                        strokeWidth={bad ? 1.5 : 1} strokeDasharray={s.outcome === "dropped" ? "3 2" : undefined}>
                        <title>{label}</title>
                      </rect>
                      {s.outcome === "open" && <rect x={x0} y={y + 3} width={w} height={TRACK - 6} fill="url(#inflight)" pointerEvents="none" />}
                      {w >= 24 && (
                        <text className="bar-label" x={x0 + w / 2} y={y + TRACK / 2 + 4} textAnchor="middle"
                          fill={bad ? "var(--ink)" : onColor(id)}>{s.chunk}</text>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
      <p className="tip" aria-live="polite">{tip || (windowed ? "Showing the last 30 seconds. Hover a bar for details." : "Hover a bar for details.")}</p>
      <div className="key">
        <span><i className="k-ok" />acknowledged</span>
        <span><i className="k-open" />still in flight</span>
        <span><i className="k-rej" />rejected, sent again</span>
        <span><i className="k-drop" />connection broke</span>
      </div>
    </div>
  );
}
