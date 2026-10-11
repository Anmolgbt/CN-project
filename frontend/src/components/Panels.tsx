import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { bytes, clock, connColor, rate } from "../services/format";
import type { Sample } from "../services/useJob";
import type { Snapshot, TEvent } from "../types";

export function ConnectionsTable({ snap }: { snap: Snapshot }) {
  if (!snap.connections.length) return <p className="empty">No connections yet.</p>;
  return (
    <div className="scroll">
      <table>
        <thead><tr><th>Socket</th><th>Status</th><th>Chunks</th><th>Bytes</th><th>Now</th><th>Average</th><th>Retries</th><th>Reconnects</th><th>Last error</th></tr></thead>
        <tbody>
          {snap.connections.map((c) => (
            <tr key={c.conn_id}>
              <td><span className="swatch" style={{ background: connColor(c.conn_id) }} />{c.conn_id}</td>
              <td>{c.status}</td><td>{c.chunks_acked}</td><td>{bytes(c.bytes_acked)}</td>
              <td>{rate(c.current_rate)}</td><td>{rate(c.avg_rate)}</td><td>{c.retries}</td><td>{c.reconnects}</td>
              <td className="mono" style={{ whiteSpace: "normal" }}>{c.last_error ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PAGE = 25;
const STATE_NAME: Record<string, string> = { P: "pending", I: "in progress", C: "completed", R: "retrying", F: "failed" };

export function ChunkTable({ snap, chunkConn }: { snap: Snapshot; chunkConn: Record<number, number> }) {
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState("all");
  const f = snap.file;
  const ids = useMemo(() => {
    const out: number[] = [];
    for (let i = 0; i < snap.chunk_states.length; i++) {
      const s = snap.chunk_states[i];
      if (filter === "all" || STATE_NAME[s] === filter) out.push(i);
    }
    return out;
  }, [snap.chunk_states, filter]);
  if (!f || f.chunks === 0) return <p className="empty">No chunks.</p>;
  const pages = Math.max(1, Math.ceil(ids.length / PAGE));
  const cur = Math.min(page, pages - 1);
  return (
    <div>
      <div className="row" style={{ marginBottom: "0.5rem" }}>
        <label htmlFor="cf" style={{ margin: 0 }}>Show</label>
        <select id="cf" value={filter} onChange={(e) => { setFilter(e.target.value); setPage(0); }} style={{ width: "auto" }}>
          {["all", "pending", "in progress", "completed", "retrying", "failed"].map((o) => <option key={o}>{o}</option>)}
        </select>
      </div>
      <div className="scroll">
        <table>
          <thead><tr><th>Chunk</th><th>Offset</th><th>Size</th><th>Status</th><th>Socket</th><th>Failed attempts</th></tr></thead>
          <tbody>
            {ids.slice(cur * PAGE, cur * PAGE + PAGE).map((i) => {
              const conn = chunkConn[i] ?? snap.chunk_owner[String(i)];
              return (
                <tr key={i}>
                  <td>{i}</td><td>{i * snap.config.chunk_size}</td>
                  <td>{Math.min(snap.config.chunk_size, f.size - i * snap.config.chunk_size)}</td>
                  <td>{STATE_NAME[snap.chunk_states[i]]}</td>
                  <td>{conn != null ? <><span className="swatch" style={{ background: connColor(conn) }} />{conn}</> : "–"}</td>
                  <td>{snap.chunk_attempts[String(i)] ?? 0}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button className="btn ghost small" disabled={cur === 0} onClick={() => setPage(cur - 1)}>Previous</button>
        <span>Page {cur + 1} of {pages} ({ids.length} chunks)</span>
        <button className="btn ghost small" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>Next</button>
      </div>
    </div>
  );
}

export function EventLog({ events, height }: { events: TEvent[]; height?: number }) {
  const [noisy, setNoisy] = useState(false);
  const shown = events.filter((e) => noisy || (e.type !== "chunk_started" && e.type !== "chunk_acked")).slice(-200);
  return (
    <div>
      <label style={{ display: "inline-flex", gap: "0.4rem", fontWeight: 400 }}>
        <input type="checkbox" checked={noisy} onChange={(e) => setNoisy(e.target.checked)} /> Include every chunk start and acknowledgment
      </label>
      <div className="log" style={height ? { maxHeight: height } : undefined} role="log" aria-live="off">
        {shown.length === 0 ? <div>No events yet.</div> : shown.map((e) => (
          <div key={e.seq} className={e.type}><span className="t">{clock(e.ts)}</span>{e.message}</div>
        ))}
      </div>
    </div>
  );
}

export function ThroughputChart({ samples }: { samples: Sample[] }) {
  if (samples.length < 2) return <p className="empty">Throughput appears here once data is flowing.</p>;
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer>
        <LineChart data={samples} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
          <XAxis dataKey="t" tickFormatter={(v) => `${Number(v).toFixed(0)}s`} stroke="var(--muted)" />
          <YAxis stroke="var(--muted)" tickFormatter={(v) => `${Number(v).toFixed(0)}`} width={44} unit="" />
          <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }}
            formatter={(v: number) => `${v.toFixed(2)} MB/s`} labelFormatter={(v) => `${Number(v).toFixed(1)} s`} />
          <Legend />
          <Line type="monotone" dataKey="current" name="Current MB/s" stroke="#0072B2" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line type="monotone" dataKey="average" name="Average MB/s" stroke="#D55E00" dot={false} strokeWidth={2} strokeDasharray="5 3" isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
