import { useEffect, useState } from "react";
import type { Nav } from "../App";
import { EventLog } from "../components/Panels";
import { Empty, Note, Section, State } from "../components/ui";
import { api } from "../services/api";
import { bytes, dateTime, duration, jobLabel, mbps } from "../services/format";
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
  const retries = rows.reduce((a, r) => a + (r.retries ?? 0), 0);
  const unfinished = rows.filter((r) => r.status !== "completed").length;

  return (
    <>
      <h1>History</h1>
      <p className="lede">
        {rows.length === 0 ? "Every finished transfer is saved here with its full event log."
          : <><span className="n">{rows.length}</span> {rows.length === 1 ? "transfer" : "transfers"} recorded, <span className="n">{unfinished}</span> not completed, <span className="n">{retries}</span> chunks sent again in total. Select a row to read its log.</>}
      </p>
      {err && <Note tone="bad">{err}</Note>}

      <Section first>
        {rows.length === 0 ? <Empty>Nothing has finished yet.</Empty> : (
          <div className="table">
            <table>
              <thead><tr><th>Finished</th><th>File</th><th>Size</th><th>Time</th><th>Sockets</th><th>Average</th><th>Resends</th><th>Failed chunks</th><th>Checksum</th><th>Result</th></tr></thead>
              <tbody>{rows.map((h) => (
                <tr key={h.job_id} className="pick" aria-selected={open === h.job_id} onClick={() => setOpen(h.job_id)}>
                  <td>{dateTime(h.finished_at)}</td><td>{jobLabel(h)}</td><td>{bytes(h.file_size)}</td>
                  <td>{duration(h.elapsed)}</td><td>{h.connections_used ?? h.connections}</td><td>{mbps(h.avg_mb_s)}</td><td>{h.retries ?? 0}</td><td>{h.failed_chunks ?? 0}</td>
                  <td>{h.checksum_match === true ? "matches" : h.checksum_match === false ? <span className="bad">differs</span> : "not compared"}</td>
                  <td>
                    <State status={h.status} />
                    {h.resumable && <><br /><button className="textbtn" onClick={(e) => { e.stopPropagation(); resume(h.job_id); }}>Resume this transfer</button></>}
                  </td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Section>

      {detail && (
        <Section title={`Log for ${detail.snap.file?.name ?? "this transfer"}`}>
          <p className="small muted" style={{ marginBottom: 12 }}>Transfer ID <code>{detail.snap.transfer_id}</code>. The last 300 events are kept with each transfer.</p>
          <p style={{ display: "flex", gap: 22, marginBottom: 16 }}>
            <button className="textbtn" onClick={() => nav.go("integrity", detail.snap.job_id)}>Integrity check</button>
            <button className="textbtn" onClick={() => nav.go("live", detail.snap.job_id)}>Timing diagram</button>
          </p>
          {detail.snap.error && detail.snap.status !== "cancelled" && <Note tone="warn">{detail.snap.error}</Note>}
          <EventLog events={detail.events} height={420} />
        </Section>
      )}
    </>
  );
}
