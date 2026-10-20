import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Nav } from "../App";
import { Empty, Note, Section } from "../components/ui";
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
      <h1>Benchmark</h1>
      <p className="lede">Sends the same bytes with the same chunk size over 1, 2, 4 and 8 sockets, one run after another, and reports what was measured. Nothing is estimated or preset.</p>
      {err && <Note tone="bad">{err}</Note>}

      <Section title="Settings" first>
        <div className="row" style={{ marginTop: 14 }}>
          <div className="field"><label htmlFor="src">Test data</label>
            <select id="src" value={source} onChange={(e) => setSource(e.target.value as "size" | "file")}>
              <option value="size">Generated file (same bytes every time)</option><option value="file" disabled={!files.length}>A file I added</option></select></div>
          {source === "size" ? (
            <div className="field"><label htmlFor="sz">Size in MB</label><input id="sz" type="number" min={1} max={1024} value={sizeMb} onChange={(e) => setSizeMb(e.target.value)} style={{ maxWidth: 120 }} /></div>
          ) : (
            <div className="field"><label htmlFor="fl">File</label><select id="fl" value={fileId} onChange={(e) => setFileId(e.target.value)}>{files.map((f) => <option key={f.file_id} value={f.file_id}>{f.name} ({bytes(f.size)})</option>)}</select></div>
          )}
          <div className="field"><label htmlFor="bc">Chunk size</label>
            <select id="bc" value={chunk} onChange={(e) => setChunk(Number(e.target.value))}><option value={262144}>256 KiB</option><option value={1048576}>1 MiB</option><option value={4194304}>4 MiB</option></select></div>
          <div className="field"><label htmlFor="rp">Runs per setting</label>
            <select id="rp" value={repeats} onChange={(e) => setRepeats(Number(e.target.value))} style={{ maxWidth: 120 }}>{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></div>
        </div>
        <div className="field"><label id="cc">Socket counts to compare</label>
          <div className="row" style={{ gap: 22 }}>{ALL.map((n) => (
            <label key={n} className="check" style={{ alignItems: "center" }}>
              <input type="checkbox" checked={conns.includes(n)} style={{ marginTop: 0 }} onChange={(e) => setConns(e.target.checked ? [...conns, n] : conns.filter((x) => x !== n))} />{n}</label>))}</div></div>
        <div className="actions" style={{ paddingTop: 0 }}>
          <button className="btn" onClick={start} disabled={running || conns.length === 0 || (source === "file" && !fileId)}>{running ? "Running…" : "Run benchmark"}</button>
          {running && <button className="btn stop" onClick={() => api.cancelBench(current!.id)}>Cancel</button>}
          {running && current!.current_job_id && <button className="textbtn" onClick={() => nav.go("live", current!.current_job_id!)}>Watch the current run</button>}
        </div>
        {running && <p className="small muted" style={{ marginTop: 14 }}>{current!.runs.length} of {planned} runs finished.</p>}
      </Section>

      {current && (
        <Section title={`Results for ${current.file_name}`} note={`${bytes(current.file_size)}, started ${dateTime(current.created_at)}.`}>
          {current.error && <Note tone={current.status === "cancelled" ? "warn" : "bad"}>{current.error}</Note>}
          {rows.length === 0 ? <Empty>The first run has not finished yet.</Empty> : (
            <>
              <div className="table">
                <table>
                  <thead><tr><th>Sockets</th><th>Total time</th><th>Average speed</th><th>Best speed</th><th>Data sent</th><th>Resends</th><th>Chunks</th><th>Versus 1 socket</th><th>Integrity</th></tr></thead>
                  <tbody>{rows.map((r) => (
                    <tr key={r.connections}><td>{r.connections}</td><td>{duration(r.elapsed)}</td><td><span className="n">{mbps(r.avg_mb_s)}</span></td><td>{mbps(r.peak_mb_s)}</td>
                      <td>{bytes(r.bytes_acked)}</td><td>{r.retries}</td><td>{r.completed_chunks} of {r.expected_chunks}</td>
                      <td>{r.speedup_vs_1 != null ? `${r.speedup_vs_1.toFixed(2)} times as fast` : "none"}</td><td>{r.integrity ? "verified" : <span className="bad">failed</span>}</td></tr>))}</tbody>
                </table>
              </div>
              <div style={{ height: 240, marginTop: 26 }}>
                <ResponsiveContainer><BarChart data={rows.map((r) => ({ name: `${r.connections} ${r.connections > 1 ? "sockets" : "socket"}`, avg: +r.avg_mb_s.toFixed(2), peak: +r.peak_mb_s.toFixed(2) }))}>
                  <CartesianGrid stroke="var(--rule)" vertical={false} />
                  <XAxis dataKey="name" stroke="var(--muted)" tickLine={false} />
                  <YAxis stroke="var(--muted)" tickLine={false} axisLine={false} width={72} tickFormatter={(v) => `${v} MB/s`} />
                  <Tooltip cursor={{ fill: "var(--faint)" }} contentStyle={{ background: "var(--paper)", border: "1px solid var(--rule)", borderRadius: 3 }} formatter={(v: number, n: string) => [`${v} MB/s`, n === "avg" ? "Average" : "Best"]} />
                  <Bar dataKey="avg" fill="var(--ink)" radius={0} isAnimationActive={false} />
                  <Bar dataKey="peak" fill="var(--muted)" radius={0} isAnimationActive={false} />
                </BarChart></ResponsiveContainer>
              </div>
              <p className="small muted" style={{ marginTop: 8 }}>Dark bars: average speed. Grey bars: best speed seen in any short window.</p>
              {best && <p style={{ marginTop: 16 }}>Fastest here: <span className="n">{best.connections} {best.connections === 1 ? "socket" : "sockets"}</span> at {mbps(best.avg_mb_s)}. That is one measurement on this computer, not a promise.</p>}
            </>
          )}
        </Section>
      )}

      <Section title="Reading the numbers">
        <div style={{ maxWidth: "68ch" }}>
          <p>More connections do not always help. Each extra socket adds work on both ends (hashing, framing, thread switches, disk writes). On localhost there is no real network at all, so the limit is usually the processor or the disk rather than TCP.</p>
          <p style={{ marginTop: 12 }}>On a real link, several flows can take a larger share of a busy path, but once the link is full they only compete with each other. These sockets are parallelism in the application. They normally follow the same route.</p>
          <p style={{ marginTop: 12 }}>To repeat a run: use the same data and chunk size, close other heavy programs, and use two or three runs per setting. Average speed is bytes acknowledged divided by transfer time, with 1 MB meaning 1,000,000 bytes.</p>
        </div>
      </Section>

      {past.length > 0 && (
        <Section title="Earlier benchmarks">
          <div className="table"><table><thead><tr><th>Started</th><th>Data</th><th>Outcome</th><th>Best average</th><th /></tr></thead>
            <tbody>{past.slice(0, 8).map((b) => {
              const top = b.summary.length ? Math.max(...b.summary.map((r) => r.avg_mb_s)) : null;
              return (<tr key={b.id}><td>{dateTime(b.created_at)}</td><td>{b.file_name}</td><td>{b.status}</td><td>{mbps(top)}</td>
                <td><button className="textbtn" onClick={() => setCurrent(b)}>Show</button></td></tr>);
            })}</tbody></table></div>
        </Section>
      )}
    </>
  );
}
