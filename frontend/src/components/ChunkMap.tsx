import { useEffect, useRef, useState } from "react";
import { connColor } from "../services/format";
import type { Snapshot } from "../types";

const CELL = 11, GAP = 2;

/** Canvas map of every chunk: colour = connection that delivered it; outline = in flight / retrying / failed. */
export function ChunkMap({ snap, chunkConn }: { snap: Snapshot; chunkConn: Record<number, number> }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [tip, setTip] = useState("Hover a square to inspect a chunk.");
  const states = snap.chunk_states;
  const total = snap.chunks.total;

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const width = cv.parentElement?.clientWidth ?? 600;
    const cols = Math.max(1, Math.floor((width - GAP) / (CELL + GAP)));
    const rows = Math.max(1, Math.ceil(total / cols));
    const dpr = window.devicePixelRatio || 1;
    cv.width = width * dpr; cv.height = (rows * (CELL + GAP) + GAP) * dpr;
    cv.style.height = `${rows * (CELL + GAP) + GAP}px`;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr);
    const css = getComputedStyle(document.documentElement);
    const pending = css.getPropertyValue("--line").trim(), muted = css.getPropertyValue("--muted").trim();
    const bad = css.getPropertyValue("--bad").trim(), ink = css.getPropertyValue("--ink").trim();
    for (let i = 0; i < total; i++) {
      const x = GAP + (i % cols) * (CELL + GAP), y = GAP + Math.floor(i / cols) * (CELL + GAP);
      const st = states[i] ?? "P";
      const owner = snap.chunk_owner[String(i)];
      if (st === "C") { g.fillStyle = chunkConn[i] != null ? connColor(chunkConn[i]) : muted; g.globalAlpha = 0.9; g.fillRect(x, y, CELL, CELL); }
      else if (st === "I") { g.globalAlpha = 1; g.fillStyle = owner != null ? connColor(owner) : ink; g.fillRect(x, y, CELL, CELL); g.strokeStyle = ink; g.lineWidth = 2; g.strokeRect(x - 1, y - 1, CELL + 2, CELL + 2); }
      else if (st === "R") { g.globalAlpha = 1; g.fillStyle = pending; g.fillRect(x, y, CELL, CELL); g.strokeStyle = "#E69F00"; g.lineWidth = 2; g.strokeRect(x, y, CELL, CELL); }
      else if (st === "F") { g.globalAlpha = 1; g.fillStyle = bad; g.fillRect(x, y, CELL, CELL); }
      else { g.globalAlpha = 1; g.fillStyle = pending; g.fillRect(x, y, CELL, CELL); }
    }
    g.globalAlpha = 1;
  }, [states, total, snap.chunk_owner, chunkConn]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const cv = ref.current!;
    const r = cv.getBoundingClientRect();
    const cols = Math.max(1, Math.floor((r.width - GAP) / (CELL + GAP)));
    const i = Math.floor((e.clientY - r.top - GAP) / (CELL + GAP)) * cols + Math.floor((e.clientX - r.left - GAP) / (CELL + GAP));
    if (i < 0 || i >= total) return setTip("Hover a square to inspect a chunk.");
    const label = { P: "pending", I: "in progress", C: "completed", R: "retrying", F: "failed" }[states[i] ?? "P"];
    const conn = chunkConn[i] ?? snap.chunk_owner[String(i)];
    setTip(`Chunk ${i}: ${label}${conn != null ? `, connection ${conn}` : ""}${snap.chunk_attempts[String(i)] ? `, ${snap.chunk_attempts[String(i)]} failed attempt(s)` : ""}`);
  };

  if (total === 0) return <p className="empty">This file has no chunks (it is empty), so there is nothing to map.</p>;
  return (
    <div>
      <canvas ref={ref} className="map" onMouseMove={onMove} aria-label="Chunk map" />
      <div className="row" style={{ justifyContent: "space-between", marginTop: "0.5rem" }}>
        <span className="mono" aria-live="polite">{tip}</span>
      </div>
      <div className="legend">
        <span style={{ ["--c" as string]: "var(--line)" }}>pending</span>
        <span style={{ ["--c" as string]: "#E69F00" }}>retrying (amber outline)</span>
        <span style={{ ["--c" as string]: "var(--bad)" }}>failed</span>
        <span style={{ ["--c" as string]: "var(--muted)" }}>done (colour = delivering connection)</span>
      </div>
    </div>
  );
}
