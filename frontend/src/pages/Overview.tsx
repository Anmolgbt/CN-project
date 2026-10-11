import { useEffect, useState } from "react";
import type { Nav } from "../App";
import { Empty, Progress, Stat, StatusChip } from "../components/ui";
import { api } from "../services/api";
import { bytes, dateTime, duration, mbps, rate } from "../services/format";
import { useJob } from "../services/useJob";
import type { Summary } from "../types";

export default function Overview({ nav }: { nav: Nav }) {
  const { snap } = useJob(nav.jobId);
  const [hist, setHist] = useState<Summary[]>([]);
  useEffect(() => {
    const load = () => api.history().then(setHist).catch(() => {});
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);
  const pct = snap && snap.bytes.total ? snap.bytes.done / snap.bytes.total : snap?.status === "completed" ? 1 : 0;
  const active = snap?.connections.filter((c) => c.status === "active").length ?? 0;
  const r = snap?.result;
  return (
    <>
      <div className="page-head">
        <div><h1>Overview</h1><p>Send one file as numbered chunks over several TCP sockets at once. The receiver checks every chunk, rebuilds the file in order, and proves it with SHA-256.</p></div>
        <button className="btn" onClick={() => nav.go("new")}>New transfer</button>
      </div>
      <section className="panel">
        <h2>Current transfer</h2>
        {!snap ? <Empty>No transfer yet in this browser. Start one to see live numbers here.</Empty> : (
          <>
            <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.6rem" }}>
              <div><strong>{snap.file?.name ?? "…"}</strong> <span style={{ color: "var(--muted)" }}>{snap.file ? bytes(snap.file.size) : ""}</span></div>
              <StatusChip status={snap.status} />
            </div>
            <Progress value={pct} tone={snap.status === "completed" ? "ok" : undefined} />
            <div className="progress-meta"><span>{(pct * 100).toFixed(1)}%</span>
              <span><button className="btn ghost small" onClick={() => nav.go("live")}>Live view</button> <button className="btn ghost small" onClick={() => nav.go("integrity")}>Integrity</button></span></div>
          </>
        )}
      </section>
      <div className="stats">
        <Stat label="Bytes transferred" value={bytes(snap?.bytes.done ?? 0)} sub={snap ? `of ${bytes(snap.bytes.total)}` : undefined} />
        <Stat label="Current throughput" value={rate(snap?.throughput.current ?? 0)} />
        <Stat label="Average throughput" value={rate(snap?.throughput.average ?? 0)} />
        <Stat label="Active sockets" value={active} sub={snap ? `of ${snap.connections.length}` : undefined} />
        <Stat label="Chunks" value={snap ? `${snap.chunks.completed} / ${snap.chunks.total}` : "–"} />
        <Stat label="Integrity" value={r ? (r.checksum_match === true ? "Verified" : r.checksum_match === false ? "Mismatch" : "Not verified") : "Pending"}
          sub={r ? `elapsed ${duration(r.elapsed)}` : undefined} />
      </div>
      <section className="panel">
        <h2>Recent transfers</h2>
        {hist.length === 0 ? <Empty>No finished transfers yet.</Empty> : (
          <div className="scroll"><table>
            <thead><tr><th>Finished</th><th>File</th><th>Size</th><th>Sockets</th><th>Average</th><th>Result</th></tr></thead>
            <tbody>{hist.slice(0, 5).map((h) => (
              <tr key={h.job_id} className="click" onClick={() => nav.go("integrity", h.job_id)}>
                <td>{dateTime(h.finished_at)}</td><td>{h.file_name}{h.kind === "benchmark" ? " (benchmark)" : ""}</td><td>{bytes(h.file_size)}</td>
                <td>{h.connections_used ?? h.connections}</td><td>{mbps(h.avg_mb_s)}</td><td><StatusChip status={h.status} /></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
    </>
  );
}
