import { useState } from "react";
import type { Nav } from "../App";
import { ChunkMap } from "../components/ChunkMap";
import { ChunkTable, ConnectionsTable, EventLog, SpeedChart } from "../components/Panels";
import { TimingDiagram } from "../components/TimingDiagram";
import { Empty, Note, Section, State } from "../components/ui";
import { api } from "../services/api";
import { bytes, duration, rate } from "../services/format";
import { speedSeries } from "../services/speed";
import { useJob } from "../services/useJob";
import { TERMINAL } from "../types";

export default function Live({ nav }: { nav: Nav }) {
  const { snap, error, acc } = useJob(nav.jobId);
  const [err, setErr] = useState<string | null>(null);
  const act = (fn: () => Promise<unknown>) => fn().then(() => setErr(null)).catch((e) => setErr(e.message));

  if (!nav.jobId) return (
    <>
      <h1>Live transfer</h1>
      <Empty>No transfer has been started in this browser yet. <button className="textbtn" onClick={() => nav.go("new")}>Start one</button> and it will appear here.</Empty>
    </>
  );
  if (!snap) return <><h1>Live transfer</h1><Empty>{error ?? "Loading the transfer…"}</Empty></>;

  const f = snap.file;
  const done = snap.bytes.total ? snap.bytes.done / snap.bytes.total : snap.status === "completed" ? 1 : 0;
  const terminal = TERMINAL.includes(snap.status);
  const resumable = ["incomplete", "failed", "cancelled"].includes(snap.status) && !!f;
  const running = ["transferring", "verifying"].includes(snap.status);
  const c = snap.chunks;

  return (
    <>
      <div className="head">
        <div>
          <h1>Live transfer</h1>
          <p className="lede">
            {f ? <><b>{f.name}</b>, {bytes(f.size)} in {f.chunks} {f.chunks === 1 ? "chunk" : "chunks"}, sent to {snap.config.host}:{snap.config.port} over {snap.config.connections} {snap.config.connections === 1 ? "socket" : "sockets"}.</> : "Preparing the file…"}
          </p>
        </div>
        <div className="actions">
          {snap.status === "transferring" && <button className="btn plain" onClick={() => act(() => api.pause(snap.job_id))}>Pause</button>}
          {snap.status === "paused" && <button className="btn" onClick={() => act(() => api.unpause(snap.job_id))}>Continue</button>}
          {!terminal && <button className="btn stop" onClick={() => act(() => api.cancel(snap.job_id))}>Cancel</button>}
          {resumable && <button className="btn" onClick={() => act(() => api.resume(snap.job_id).then((r) => nav.go("live", r.job_id)))}>Resume transfer</button>}
          {terminal && <button className="btn plain" onClick={() => nav.go("integrity")}>Integrity check</button>}
        </div>
      </div>

      {(err || error) && <Note tone="bad">{err ?? error}</Note>}
      {snap.error && terminal && snap.status !== "completed" && snap.status !== "cancelled" && (
        <Note tone={snap.status === "integrity_failed" || snap.status === "failed" ? "bad" : "warn"}>{snap.error}</Note>
      )}
      {snap.status === "paused" && <Note tone="warn">Paused. Each socket finished the chunk it was on and closed. Continue reconnects them; the receiver kept every chunk it had validated.</Note>}

      <Section first>
        <p style={{ fontSize: "1.25rem" }}>
          <State status={snap.status} />
          {snap.status === "scanning" ? <span className="muted"> {Math.round(snap.scan_progress * 100)}%</span> : f && f.chunks > 0 || terminal ? <span className="muted">, <span className="n">{(done * 100).toFixed(1)}%</span> done</span> : null}
        </p>
        <p style={{ marginTop: 10, maxWidth: "74ch" }}>
          <span className="n">{rate(snap.throughput.current)}</span> right now, <span className="n">{rate(snap.throughput.average)}</span> on average,{" "}
          <span className="n">{rate(snap.throughput.peak)}</span> at best. <span className="n">{bytes(snap.bytes.done)}</span> of {bytes(snap.bytes.total)} has been delivered
          {snap.bytes.resumed > 0 ? ` (${bytes(snap.bytes.resumed)} of that was already on the receiver)` : ""} in <span className="n">{duration(snap.elapsed)}</span>.{" "}
          <span className="n">{c.completed}</span> of {c.total} chunks are done{c.in_progress ? `, ${c.in_progress} on the wire` : ""}.{" "}
          {snap.retries ? <><span className="n">{snap.retries}</span> had to be sent again.</> : "None has needed a retry."}{" "}
          {running && snap.eta_seconds != null && <>About <span className="n">{duration(snap.eta_seconds)}</span> to go (an estimate from recent speed).</>}
        </p>
        <div style={{ marginTop: 20 }}><ChunkMap snap={snap} chunkConn={acc.chunkConn} /></div>
      </Section>

      <Section title="Sockets over time" note="Each row is one TCP connection. Each bar is one chunk, from the moment it was sent until the receiver acknowledged it. Chunks overlapping in time are being sent in parallel.">
        <TimingDiagram snap={snap} spans={acc.spans} />
      </Section>

      <Section title="Speed"><SpeedChart samples={speedSeries(acc.spans, snap)} /></Section>
      <Section title="Sockets"><ConnectionsTable snap={snap} /></Section>
      <Section title="Chunks">
        <details><summary>Show the table of all {c.total} chunks</summary><div style={{ marginTop: 14 }}><ChunkTable snap={snap} chunkConn={acc.chunkConn} /></div></details>
      </Section>
      <Section title="Event log"><EventLog events={acc.events} /></Section>
    </>
  );
}
