import { useEffect, useRef, useState } from "react";
import { connColor } from "../services/format";
import type { Snapshot } from "../types";

const GAP = 2;

/** Every chunk as one small square, in file order. Done chunks wear the colour of the socket that carried them. */
export function ChunkMap({ snap, chunkConn }: { snap: Snapshot; chunkConn: Record<number, number> }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [tip, setTip] = useState("");
  const states = snap.chunk_states;
  const total = snap.chunks.total;

  const layout = (width: number) => {
    let cell = 14;
    let cols = 1, rows = 1;
    for (; cell >= 3; cell--) {
      cols = Math.max(1, Math.floor((width - GAP) / (cell + GAP)));
      rows = Math.ceil(total / cols);
      if (rows * (cell + GAP) <= 150) break;
    }
    return { cell, cols, rows };
  };

  useEffect(() => {
    const cv = ref.current;
    if (!cv || total === 0) return;
    const width = cv.parentElement?.clientWidth ?? 800;
    const { cell, cols, rows } = layout(width);
    const dpr = window.devicePixelRatio || 1;
    const h = rows * (cell + GAP) + GAP;
    cv.width = width * dpr; cv.height = h * dpr; cv.style.height = `${h}px`;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr);
    const css = getComputedStyle(document.documentElement);
    const rule = css.getPropertyValue("--rule").trim(), muted = css.getPropertyValue("--muted").trim();
    const bad = css.getPropertyValue("--bad").trim(), ink = css.getPropertyValue("--ink").trim();
    for (let i = 0; i < total; i++) {
      const x = GAP + (i % cols) * (cell + GAP), y = GAP + Math.floor(i / cols) * (cell + GAP);
      const st = states[i] ?? "P";
      if (st === "C") { g.fillStyle = chunkConn[i] != null ? connColor(chunkConn[i]) : muted; g.fillRect(x, y, cell, cell); }
      else if (st === "I") {
        const o = snap.chunk_owner[String(i)];
        g.fillStyle = o != null ? connColor(o) : ink; g.fillRect(x, y, cell, cell);
        g.strokeStyle = ink; g.lineWidth = 1.5; g.strokeRect(x - 1.5, y - 1.5, cell + 3, cell + 3);
      }
      else if (st === "R") { g.fillStyle = rule; g.fillRect(x, y, cell, cell); g.strokeStyle = bad; g.lineWidth = 1.5; g.strokeRect(x + 0.75, y + 0.75, cell - 1.5, cell - 1.5); }
      else if (st === "F") { g.fillStyle = bad; g.fillRect(x, y, cell, cell); }
      else { g.fillStyle = rule; g.fillRect(x, y, cell, cell); }
    }
  }, [states, total, snap.chunk_owner, chunkConn]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const cv = ref.current!;
    const r = cv.getBoundingClientRect();
    const { cell, cols } = layout(r.width);
    const i = Math.floor((e.clientY - r.top - GAP) / (cell + GAP)) * cols + Math.floor((e.clientX - r.left - GAP) / (cell + GAP));
    if (i < 0 || i >= total) return setTip("");
    const label = { P: "waiting", I: "being sent", C: "done", R: "being sent again", F: "failed" }[states[i] ?? "P"];
    const conn = chunkConn[i] ?? snap.chunk_owner[String(i)];
    const tries = snap.chunk_attempts[String(i)];
    setTip(`Chunk ${i} of ${total}: ${label}${conn != null ? ` on socket ${conn}` : ""}${tries ? `, rejected ${tries} time${tries > 1 ? "s" : ""}` : ""}`);
  };

  if (total === 0) return <p className="empty">This file is empty, so there are no chunks. Only the final check runs.</p>;
  return (
    <div>
      <canvas ref={ref} className="strip" onMouseMove={onMove} onMouseLeave={() => setTip("")} aria-label="Map of all chunks" />
      <p className="tip" aria-live="polite">{tip || "Each square is one chunk, in file order. Hover one to see which socket carried it."}</p>
    </div>
  );
}
