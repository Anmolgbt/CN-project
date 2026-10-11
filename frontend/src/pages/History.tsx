import { useEffect, useState } from "react";
import type { Nav } from "../App";
import { EventLog } from "../components/Panels";
import { Empty, Notice, StatusChip } from "../components/ui";
import { api } from "../services/api";
import { bytes, dateTime, duration, mbps } from "../services/format";
import type { Snapshot, Summary, TEvent } from "../types";

export default function History({ nav }: { nav: Nav }) {
  const [rows, setRows] = useState<Summary[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ snap: Snapshot; events: TEvent[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const load = () => api.history().then(setRows).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!open) { setDetail(null); return; }
    Promise.all([api.transfer(open), api.events(open, 0)]).then(([snap, events]) => setDetail({ snap, events })).catch((e) => setErr(e.message));
  }, [open]);

  const resume = (id: string) => api.resume(id).then((r) => nav.go("live", r.job_id)).catch((e) => setErr(e.message));
  const totalRetries = rows.reduce((a, r) => a + (r.retries ?? 0), 0);
  const failures = rows.filter((r) => r.status !== "completed").length;

  return (
    <>
      <div className="page-head"><div><h1>History and diagnostics</h1>
        <p>Every finished transfer is saved on the API host. Select a row to read its full event log.</p></div>
        <span className="chip">{rows.length} transfers, {failures} not completed, {totalRetries} retries in total</span></div>
      {err && <Notice tone="bad">{err}</Notice>}
      <section className="panel">
        {rows.length === 0 ? <Empty>No transfers recorded yet.</Empty> : (
          <div className="scroll"><table>
            <thead><tr><th>Finished</th><th>File</th><th>Size</th><th>Time</th><th>Sockets</th><th>Average</th><th>Retries</th><th>Failed chunks</th><th>Integrity</th><th>Result</th><th /></tr></thead>
            <tbody>{rows.map((h) => (
              <tr key={h.job_id} className="click" onClick={() => setOpen(h.job_id)} aria-selected={open === h.job_id}>
                <td>{dateTime(h.finished_at)}</td><td>{h.file_name}{h.kind === "benchmark" ? " (benchmark)" : ""}</td><td>{bytes(h.file_size)}</td>
                <td>{duration(h.elapsed)}</td><td>{h.connections_used ?? h.connections}</td><td>{mbps(h.avg_mb_s)}</td><td>{h.retries ?? 0}</td><td>{h.failed_chunks ?? 0}</td>
                <td>{h.checksum_match === true ? "match" : h.checksum_match === false ? "MISMATCH" : "none"}</td>
                <td><StatusChip status={h.status} /></td>
                <td>{h.resumable && <button className="btn small" onClick={(e) => { e.stopPropagation(); resume(h.job_id); }}>Resume</button>}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
      {detail && (
        <section className="panel">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.6rem" }}>
            <h2 style={{ margin: 0 }}>Log for {detail.snap.file?.name}</h2>
            <div className="row"><button className="btn ghost small" onClick={() => nav.go("integrity", detail.snap.job_id)}>Integrity view</button>
              <button className="btn ghost small" onClick={() => nav.go("live", detail.snap.job_id)}>Live view</button></div>
          </div>
          {detail.snap.error && <Notice tone="warn">{detail.snap.error}</Notice>}
          <p style={{ color: "var(--muted)", fontSize: "0.86rem" }}>Transfer ID <code>{detail.snap.transfer_id}</code>. Showing up to the last 300 events saved with this transfer.</p>
          <EventLog events={detail.events} height={420} />
        </section>
      )}
    </>
  );
}
