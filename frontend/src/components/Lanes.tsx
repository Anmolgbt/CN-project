import { bytes, connColor, rate } from "../services/format";
import type { Snapshot } from "../types";

/** One lane per TCP connection: the large tile is the chunk on the wire now; faded tiles were just acknowledged. */
export function Lanes({ snap, lanes }: { snap: Snapshot; lanes: Record<number, number[]> }) {
  if (snap.connections.length === 0)
    return <p className="empty">No sockets yet. They open once the server accepts the transfer offer.</p>;
  return (
    <div className="lanes">
      {snap.connections.map((c) => {
        const col = connColor(c.conn_id);
        const live = c.status === "active" && c.current_chunk != null;
        return (
          <div key={c.conn_id} className={`lane ${["closed", "dead"].includes(c.status) ? "off" : ""}`} style={{ borderLeftColor: col }}>
            <div className="lane-id">Socket {c.conn_id}<small>{c.status}{c.reconnects ? `, ${c.reconnects} reconnect(s)` : ""}</small></div>
            <div className="lane-track" aria-label={`Connection ${c.conn_id} chunk flow`}>
              {(lanes[c.conn_id] ?? []).slice(-12).map((id) => <span key={id} className="tile done" style={{ background: col }}>{id}</span>)}
              {live ? <span key={c.current_chunk} className="tile live" style={{ background: col }}>#{c.current_chunk}</span>
                : <span className="tile idle">{c.status === "paused" ? "paused" : c.status === "dead" ? "lost" : "idle"}</span>}
            </div>
            <div className="lane-rate">{rate(c.current_rate)}<small>{c.chunks_acked} chunks, {bytes(c.bytes_acked)}</small></div>
          </div>
        );
      })}
    </div>
  );
}
