import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Nav } from "../App";
import { Empty, Notice, Progress } from "../components/ui";
import { api } from "../services/api";
import { bytes, dateTime, duration, mbps } from "../services/format";
import type { Benchmark as Bench, StagedFile } from "../types";

const ALL = [1, 2, 4, 8];

export default function Benchmark({ nav }: { nav: Nav }) {
  const [source, setSource] = useState<"size" | "file">("size");
  const [sizeMb, setSizeMb] = useState("64");
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [fileId, setFileId] = useState("");
  const [chunk, setChunk] = useState(1048576);
  const [conns, setConns] = useState<number[]>(ALL);
  const [repeats, setRepeats] = useState(1);
  const [current, setCurrent] = useState<Bench | null>(null);
  const [past, setPast] = useState<Bench[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.files().then((f) => { setFiles(f); if (f[0]) setFileId(f[0].file_id); }).catch(() => {}); api.benches().then(setPast).catch(() => {}); }, []);
  useEffect(() => {
    if (!current || current.status !== "running") return;
    const t = setInterval(() => api.bench(current.id).then((b) => { setCurrent(b); if (b.status !== "running") api.benches().then(setPast); }).catch(() => {}), 800);
    return () => clearInterval(t);
  }, [current?.id, current?.status]);

  const start = () => {
    setErr(null);
    const port = nav.config?.default_port ?? 9000;
    const body = { host: "127.0.0.1", port, chunk_size: chunk, connections: [...conns].sort((a, b) => a - b), repeats,
      ...(source === "size" ? { size_mb: Number(sizeMb) } : { file_id: fileId }) };
    api.startBench(body).then(setCurrent).catch((e) => setErr(e.message));
  };
  const running = current?.status === "running";
  const rows = current?.summary ?? [];
  const planned = (current?.connections.length ?? 0) * (current?.repeats ?? 0);
  const best = rows.length ? rows.reduce((a, b) => (b.avg_mb_s > a.avg_mb_s ? b : a)) : null;

  return (
    <>
      <div className="page-head"><div><h1>Benchmark</h1>
        <p>Sends the same bytes with the same chunk size using 1, 2, 4 and 8 sockets, one after another, and reports what was measured. Nothing here is estimated or preset.</p></div></div>
      {err && <Notice tone="bad">{err}</Notice>}
      <section className="panel">
        <h2>Run settings</h2>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div className="field"><label htmlFor="src">Input</label>
            <select id="src" value={source} onChange={(e) => setSource(e.target.value as "size" | "file")}>
              <option value="size">Generated test file (same seed every time)</option><option value="file" disabled={!files.length}>A staged file</option></select></div>
          {source === "size" ? (
            <div className="field"><label htmlFor="sz">Size (MB)</label><input id="sz" type="number" min={1} max={1024} value={sizeMb} onChange={(e) => setSizeMb(e.target.value)} /></div>
          ) : (
            <div className="field"><label htmlFor="fl">File</label><select id="fl" value={fileId} onChange={(e) => setFileId(e.target.value)}>{files.map((f) => <option key={f.file_id} value={f.file_id}>{f.name} ({bytes(f.size)})</option>)}</select></div>
          )}
          <div className="field"><label htmlFor="bc">Chunk size</label>
            <select id="bc" value={chunk} onChange={(e) => setChunk(Number(e.target.value))}><option value={262144}>256 KiB</option><option value={1048576}>1 MiB</option><option value={4194304}>4 MiB</option></select></div>
          <div className="field"><label htmlFor="rp">Repeats per setting</label>
            <select id="rp" value={repeats} onChange={(e) => setRepeats(Number(e.target.value))}>{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></div>
        </div>
        <div className="field"><label id="cc">Socket counts to compare</label>
          <div className="row">{ALL.map((n) => (
            <label key={n} style={{ display: "inline-flex", gap: "0.35rem", fontWeight: 400, margin: 0 }}>
              <input type="checkbox" checked={conns.includes(n)} onChange={(e) => setConns(e.target.checked ? [...conns, n] : conns.filter((x) => x !== n))} />{n}</label>))}</div></div>
        <div className="row">
          <button className="btn" onClick={start} disabled={running || conns.length === 0 || (source === "file" && !fileId)}>{running ? "Running…" : "Run benchmark"}</button>
          {running && <button className="btn danger" onClick={() => api.cancelBench(current!.id)}>Cancel</button>}
          {running && current!.current_job_id && <button className="btn ghost" onClick={() => nav.go("live", current!.current_job_id!)}>Watch current run</button>}
        </div>
        {running && <div style={{ marginTop: "0.8rem" }}><Progress value={planned ? current!.runs.length / planned : 0} />
          <div className="progress-meta"><span>{current!.runs.length} of {planned} runs finished</span></div></div>}
      </section>

      {current && (
        <section className="panel">
          <h2>Results for {current.file_name} ({bytes(current.file_size)}, started {dateTime(current.created_at)})</h2>
          {current.error && <Notice tone={current.status === "cancelled" ? "warn" : "bad"}>{current.error}</Notice>}
          {rows.length === 0 ? <Empty>The first run has not finished yet.</Empty> : (
            <>
              <div className="scroll"><table>
                <thead><tr><th>Sockets</th><th>Total time</th><th>Average</th><th>Peak</th><th>Bytes sent</th><th>Retries</th><th>Chunks</th><th>Speed-up vs 1</th><th>Integrity</th></tr></thead>
                <tbody>{rows.map((r) => (
                  <tr key={r.connections}><td>{r.connections}</td><td>{duration(r.elapsed)}</td><td><strong>{mbps(r.avg_mb_s)}</strong></td><td>{mbps(r.peak_mb_s)}</td>
                    <td>{bytes(r.bytes_acked)}</td><td>{r.retries}</td><td>{r.completed_chunks}/{r.expected_chunks}</td>
                    <td>{r.speedup_vs_1 != null ? `${r.speedup_vs_1.toFixed(2)}x` : "–"}</td><td>{r.integrity ? "verified" : "FAILED"}</td></tr>))}</tbody>
              </table></div>
              <div style={{ height: 260, marginTop: "1rem" }}>
                <ResponsiveContainer><BarChart data={rows.map((r) => ({ name: `${r.connections} socket${r.connections > 1 ? "s" : ""}`, avg: +r.avg_mb_s.toFixed(2), peak: +r.peak_mb_s.toFixed(2) }))}>
                  <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" /><XAxis dataKey="name" stroke="var(--muted)" /><YAxis stroke="var(--muted)" width={48} />
                  <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)" }} formatter={(v: number) => `${v} MB/s`} /><Legend />
                  <Bar dataKey="avg" name="Average MB/s" fill="#0072B2" /><Bar dataKey="peak" name="Peak MB/s" fill="#E69F00" /></BarChart></ResponsiveContainer></div>
              {best && <p style={{ marginTop: "0.8rem" }}>Fastest in this run: <strong>{best.connections} socket(s)</strong> at {mbps(best.avg_mb_s)}. This is one measurement on this machine, not a guarantee.</p>}
            </>
          )}
        </section>
      )}
      <section className="panel">
        <h2>How to read this</h2>
        <p>More connections do not always help. Extra sockets add CPU work (hashing, framing), thread switching and disk writes on both ends. On localhost there is no real network at all, so the limit is usually the CPU or the disk rather than TCP.
          On a real link, several flows can claim a larger share of a congested path, but beyond the link's capacity they only compete with each other.</p>
        <p>These sockets are application-level parallelism. They all follow the same route unless the operating system's routing sends them elsewhere.</p>
        <p style={{ marginBottom: 0 }}>Reproduce: use the same input and chunk size, close other heavy programs, and use 2-3 repeats so one noisy run does not mislead. Average speed is bytes acknowledged divided by transfer time (1 MB = 1,000,000 bytes).</p>
      </section>
      {past.length > 0 && (
        <section className="panel"><h2>Earlier benchmarks</h2>
          <div className="scroll"><table><thead><tr><th>Started</th><th>File</th><th>Status</th><th>Best average</th><th /></tr></thead>
            <tbody>{past.slice(0, 8).map((b) => {
              const top = b.summary.length ? Math.max(...b.summary.map((r) => r.avg_mb_s)) : null;
              return (<tr key={b.id}><td>{dateTime(b.created_at)}</td><td>{b.file_name}</td><td>{b.status}</td><td>{mbps(top)}</td>
                <td><button className="btn ghost small" onClick={() => setCurrent(b)}>Show</button></td></tr>);
            })}</tbody></table></div></section>
      )}
    </>
  );
}
