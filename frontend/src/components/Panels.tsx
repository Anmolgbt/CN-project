import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { bytes, clock, connColor, rate } from "../services/format";
import { axisTicks, type SpeedPoint } from "../services/speed";
import type { Snapshot, TEvent } from "../types";

export function ConnectionsTable({ snap }: { snap: Snapshot }) {
  if (!snap.connections.length) return <p className="empty">No sockets are open yet.</p>;
  return (
    <div className="table">
      <table>
        <thead><tr><th>Socket</th><th>State</th><th>Chunks</th><th>Data</th><th>Speed now</th><th>Average</th><th>Resends</th><th>Reconnects</th><th>Last error</th></tr></thead>
        <tbody>
          {snap.connections.map((c) => (
            <tr key={c.conn_id}>
              <td><span className="swatch" style={{ background: connColor(c.conn_id) }} />{c.conn_id}</td>
              <td>{c.status}</td><td>{c.chunks_acked}</td><td>{bytes(c.bytes_acked)}</td>
              <td>{rate(c.current_rate)}</td><td>{rate(c.avg_rate)}</td><td>{c.retries}</td><td>{c.reconnects}</td>
              <td className="wrap">{c.last_error ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PAGE = 20;
const NAME: Record<string, string> = { P: "waiting", I: "being sent", C: "done", R: "being sent again", F: "failed" };

export function ChunkTable({ snap, chunkConn }: { snap: Snapshot; chunkConn: Record<number, number> }) {
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState("all");
  const f = snap.file;
  const ids = useMemo(() => {
    const out: number[] = [];
    for (let i = 0; i < snap.chunk_states.length; i++) if (filter === "all" || NAME[snap.chunk_states[i]] === filter) out.push(i);
    return out;
  }, [snap.chunk_states, filter]);
  if (!f || f.chunks === 0) return <p className="empty">There are no chunks to list.</p>;
  const pages = Math.max(1, Math.ceil(ids.length / PAGE));
  const cur = Math.min(page, pages - 1);
  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="cf">Show chunks that are</label>
          <select id="cf" value={filter} onChange={(e) => { setFilter(e.target.value); setPage(0); }}>
            {["all", ...Object.values(NAME)].map((o) => <option key={o}>{o}</option>)}
          </select>
        </div>
      </div>
      <div className="table">
        <table>
          <thead><tr><th>Chunk</th><th>Starts at byte</th><th>Size</th><th>State</th><th>Socket</th><th>Rejections</th></tr></thead>
          <tbody>
            {ids.slice(cur * PAGE, cur * PAGE + PAGE).map((i) => {
              const conn = chunkConn[i] ?? snap.chunk_owner[String(i)];
              return (
                <tr key={i}>
                  <td>{i}</td><td>{(i * snap.config.chunk_size).toLocaleString()}</td>
                  <td>{Math.min(snap.config.chunk_size, f.size - i * snap.config.chunk_size).toLocaleString()} B</td>
                  <td>{NAME[snap.chunk_states[i]]}</td>
                  <td>{conn != null ? <><span className="swatch" style={{ background: connColor(conn) }} />{conn}</> : <span className="muted">not seen</span>}</td>
                  <td>{snap.chunk_attempts[String(i)] ?? 0}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button className="textbtn" disabled={cur === 0} onClick={() => setPage(cur - 1)}>Previous</button>
        <span>Page {cur + 1} of {pages}, {ids.length} chunks</span>
        <button className="textbtn" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>Next</button>
      </div>
    </div>
  );
}

export function EventLog({ events, height }: { events: TEvent[]; height?: number }) {
  const [all, setAll] = useState(false);
  const shown = events.filter((e) => all || (e.type !== "chunk_started" && e.type !== "chunk_acked")).slice(-200);
  return (
    <div>
      <label className="check" style={{ marginBottom: 10 }}>
        <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />Show every chunk sent and acknowledged
      </label>
      <div className="log" style={height ? { maxHeight: height } : undefined} role="log" aria-live="off">
        {shown.length === 0 ? <div>Nothing has happened yet.</div> : shown.map((e) => (
          <div key={e.seq} className={e.type}><time>{clock(e.ts)}</time><span>{e.message}</span></div>
        ))}
      </div>
    </div>
  );
}

export function SpeedChart({ samples }: { samples: SpeedPoint[] }) {
  if (samples.length < 2) return <p className="empty">The speed curve is drawn from acknowledged chunks, so it appears once a couple have arrived.</p>;
  const { max, ticks } = axisTicks(Math.max(...samples.map((s) => Math.max(s.current, s.average))) * 1.05);
  return (
    <div>
      <p className="small muted" style={{ marginBottom: 8 }}>Speed in MB/s. Solid line: over the last two seconds or so. Dashed line: average since the first chunk. Both come from the chunks the receiver acknowledged.</p>
      <div style={{ height: 210 }}>
        <ResponsiveContainer>
          <LineChart data={samples} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--rule)" vertical={false} />
            <XAxis dataKey="t" type="number" domain={[0, "dataMax"]} tickCount={7} stroke="var(--muted)" tickLine={false}
              tickFormatter={(v) => `${Number(v).toFixed(Number(v) < 10 ? 1 : 0)} s`} />
            <YAxis stroke="var(--muted)" tickLine={false} axisLine={false} width={40} domain={[0, max]} ticks={ticks}
              tickFormatter={(v) => String(v)} />
            <Tooltip contentStyle={{ background: "var(--paper)", border: "1px solid var(--rule)", borderRadius: 3 }}
              formatter={(v: number) => `${v.toFixed(2)} MB/s`} labelFormatter={(v) => `${Number(v).toFixed(1)} s in`} />
            <Line type="monotone" dataKey="current" stroke="var(--ink)" dot={false} strokeWidth={2} isAnimationActive={false} />
            <Line type="monotone" dataKey="average" stroke="var(--muted)" dot={false} strokeWidth={1.5} strokeDasharray="5 4" isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
