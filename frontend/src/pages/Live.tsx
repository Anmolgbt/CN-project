import { useState } from "react";
import type { Nav } from "../App";
import { ChunkMap } from "../components/ChunkMap";
import { Lanes } from "../components/Lanes";
import { ChunkTable, ConnectionsTable, EventLog, ThroughputChart } from "../components/Panels";
import { Empty, Notice, Progress, Stat, StatusChip } from "../components/ui";
import { api } from "../services/api";
import { bytes, duration, rate, STATUS_LABEL } from "../services/format";
import { useJob } from "../services/useJob";
import { TERMINAL } from "../types";

export default function Live({ nav }: { nav: Nav }) {
  const { snap, error, acc } = useJob(nav.jobId);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"map" | "table">("map");
  const act = (fn: () => Promise<unknown>) => fn().then(() => setErr(null)).catch((e) => setErr(e.message));

  if (!nav.jobId) return <Empty>No transfer selected. <button className="btn small" onClick={() => nav.go("new")}>Start one</button></Empty>;
  if (!snap) return <Empty>{error ?? "Loading transfer…"}</Empty>;

  const done = snap.bytes.total ? snap.bytes.done / snap.bytes.total : snap.status === "completed" ? 1 : 0;
  const terminal = TERMINAL.includes(snap.status);
  const resumable = ["incomplete", "failed", "cancelled"].includes(snap.status) && !!snap.file;
  const stage = snap.status === "scanning" ? ` (${Math.round(snap.scan_progress * 100)}%)` : "";

  return (
    <>
      <div className="page-head">
        <div><h1>Live transfer</h1>
          <p>{snap.file ? `${snap.file.name}, ${bytes(snap.file.size)}, ${snap.file.chunks} chunks` : "Preparing…"} to {snap.config.host}:{snap.config.port} over {snap.config.connections} socket(s)</p></div>
        <div className="row">
          <StatusChip status={snap.status} />
          {snap.status === "transferring" && <button className="btn ghost" onClick={() => act(() => api.pause(snap.job_id))}>Pause</button>}
          {snap.status === "paused" && <button className="btn" onClick={() => act(() => api.unpause(snap.job_id))}>Continue</button>}
          {!terminal && <button className="btn danger" onClick={() => act(() => api.cancel(snap.job_id))}>Cancel</button>}
          {resumable && <button className="btn" onClick={() => act(() => api.resume(snap.job_id).then((r) => nav.go("live", r.job_id)))}>Resume transfer</button>}
          {terminal && <button className="btn ghost" onClick={() => nav.go("integrity")}>Open integrity check</button>}
        </div>
      </div>
      {(err || error) && <Notice tone="bad">{err ?? error}</Notice>}
      {snap.error && terminal && snap.status !== "completed" && <Notice tone={snap.status === "integrity_failed" ? "bad" : "warn"}>{snap.error}</Notice>}
      {snap.status === "paused" && <Notice tone="warn">Paused. Each socket finished its current chunk, then closed. Continue reconnects them; the receiver kept every validated chunk.</Notice>}

      <section className="panel">
        <Progress value={done} tone={snap.status === "completed" ? "ok" : ["failed", "integrity_failed"].includes(snap.status) ? "bad" : undefined} />
        <div className="progress-meta">
          <span><strong>{(done * 100).toFixed(1)}%</strong> &nbsp;{bytes(snap.bytes.done)} of {bytes(snap.bytes.total)}
            {snap.bytes.resumed > 0 && ` (${bytes(snap.bytes.resumed)} already on the receiver)`}</span>
          <span>{STATUS_LABEL[snap.status]}{stage}</span>
        </div>
      </section>

      <div className="stats">
        <Stat label="Elapsed" value={duration(snap.elapsed)} />
        <Stat label="Current throughput" value={rate(snap.throughput.current)} sub={`peak ${rate(snap.throughput.peak)}`} />
        <Stat label="Average throughput" value={rate(snap.throughput.average)} />
        <Stat label="Time remaining (estimate)" value={snap.eta_seconds != null ? `~${duration(snap.eta_seconds)}` : "–"} sub="based on recent speed" />
        <Stat label="Chunks" value={`${snap.chunks.completed} / ${snap.chunks.total}`} sub={`${snap.chunks.in_progress} in flight, ${snap.chunks.failed} failed`} />
        <Stat label="Retries" value={snap.retries} sub={`${bytes(snap.bytes.sent)} sent on the wire`} />
      </div>

      <section className="panel">
        <h2>Parallel sockets</h2>
        <p style={{ color: "var(--muted)", fontSize: "0.88rem" }}>Each lane is one TCP connection. The outlined tile is the chunk on the wire right now; faded tiles were just acknowledged by the receiver.</p>
        <Lanes snap={snap} lanes={acc.lanes} />
      </section>

      <div className="grid2">
        <section className="panel">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.6rem" }}>
            <h2 style={{ margin: 0 }}>Chunks</h2>
            <div className="seg"><button aria-pressed={tab === "map"} onClick={() => setTab("map")}>Map</button><button aria-pressed={tab === "table"} onClick={() => setTab("table")}>Table</button></div>
          </div>
          {tab === "map" ? <ChunkMap snap={snap} chunkConn={acc.chunkConn} /> : <ChunkTable snap={snap} chunkConn={acc.chunkConn} />}
        </section>
        <section className="panel"><h2>Throughput</h2><ThroughputChart samples={acc.samples} /></section>
      </div>
      <section className="panel"><h2>Connection statistics</h2><ConnectionsTable snap={snap} /></section>
      <section className="panel"><h2>Event log</h2><EventLog events={acc.events} /></section>
    </>
  );
}
