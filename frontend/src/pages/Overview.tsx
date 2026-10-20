import { useEffect, useState } from "react";
import type { Nav } from "../App";
import { Empty, Section, State } from "../components/ui";
import { api } from "../services/api";
import { bytes, dateTime, duration, jobLabel, mbps, rate } from "../services/format";
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

  const r = snap?.result;
  const active = snap?.connections.filter((c) => c.status === "active").length ?? 0;
  const pct = snap && snap.bytes.total ? snap.bytes.done / snap.bytes.total : snap?.status === "completed" ? 1 : 0;

  return (
    <>
      <div className="head">
        <div>
          <h1>Overview</h1>
          <p className="lede">A file is cut into chunks and sent over several TCP connections at once. The receiver checks every chunk, rebuilds the file in order and proves it is identical with SHA-256.</p>
        </div>
        <div className="actions"><button className="btn" onClick={() => nav.go("new")}>New transfer</button></div>
      </div>

      <Section title="Latest transfer" first>
        {!snap ? (
          <Empty>Nothing has been sent from this browser yet. Choose a file under New transfer and its progress will show up here.</Empty>
        ) : (
          <>
            <p style={{ fontSize: "1.3rem", fontWeight: 650 }}>{snap.file?.name ?? "Preparing…"} <span className="muted" style={{ fontWeight: 400, fontSize: "1rem" }}>{snap.file ? bytes(snap.file.size) : ""}</span></p>
            <p style={{ marginTop: 4 }}><State status={snap.status} /></p>
            <dl className="facts" style={{ marginTop: 18, maxWidth: 560 }}>
              <dt>Sent so far</dt><dd><span className="n">{bytes(snap.bytes.done)}</span> of {bytes(snap.bytes.total)} ({(pct * 100).toFixed(1)}%)</dd>
              <dt>Speed right now</dt><dd><span className="n">{rate(snap.throughput.current)}</span></dd>
              <dt>Average speed</dt><dd><span className="n">{rate(snap.throughput.average)}</span></dd>
              <dt>Sockets in use</dt><dd><span className="n">{active}</span> of {snap.connections.length}</dd>
              <dt>Chunks done</dt><dd><span className="n">{snap.chunks.completed}</span> of {snap.chunks.total}</dd>
              <dt>Integrity</dt>
              <dd>{r ? (r.checksum_match === true ? <span className="ok n">Checksums match</span> : r.checksum_match === false ? <span className="bad n">Checksums differ</span> : "Not verified") : "Checked when the last chunk arrives"}
                {r ? <span className="muted">, in {duration(r.elapsed)}</span> : null}</dd>
            </dl>
            <p style={{ marginTop: 18, display: "flex", gap: 22 }}>
              <button className="textbtn" onClick={() => nav.go("live")}>Watch it live</button>
              <button className="textbtn" onClick={() => nav.go("integrity")}>See the integrity check</button>
            </p>
          </>
        )}
      </Section>

      <Section title="Recent transfers">
        {hist.length === 0 ? <Empty>No transfer has finished yet.</Empty> : (
          <div className="table">
            <table>
              <thead><tr><th>Finished</th><th>File</th><th>Size</th><th>Sockets</th><th>Average speed</th><th>Result</th></tr></thead>
              <tbody>{hist.slice(0, 6).map((h) => (
                <tr key={h.job_id} className="pick" onClick={() => nav.go("integrity", h.job_id)}>
                  <td>{dateTime(h.finished_at)}</td><td>{jobLabel(h)}</td><td>{bytes(h.file_size)}</td>
                  <td>{h.connections_used ?? h.connections}</td><td>{mbps(h.avg_mb_s)}</td><td><State status={h.status} /></td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
