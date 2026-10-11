import { useEffect, useRef, useState } from "react";
import type { Nav } from "../App";
import { Notice } from "../components/ui";
import { api, uploadFile } from "../services/api";
import { bytes, dateTime } from "../services/format";
import type { StagedFile } from "../types";

const CHUNKS = [{ v: 262144, l: "256 KiB" }, { v: 1048576, l: "1 MiB" }, { v: 4194304, l: "4 MiB" }];
const CONNS = [1, 2, 4, 8];

export default function NewTransfer({ nav }: { nav: Nav }) {
  const cfg = nav.config;
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [host, setHost] = useState("127.0.0.1");
  const [port, setPort] = useState("9000");
  const [conns, setConns] = useState(4);
  const [chunk, setChunk] = useState(1048576);
  const [restart, setRestart] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const refresh = () => api.files().then(setFiles).catch((e) => setError(e.message));
  useEffect(() => { refresh(); }, []);
  useEffect(() => { if (cfg) setPort(String(cfg.default_port)); }, [cfg?.default_port]);

  const picked = files.find((f) => f.file_id === sel) ?? null;
  const chunkCount = picked ? Math.ceil(picked.size / chunk) : 0;
  const portNum = Number(port);
  const problems: string[] = [];
  if (!picked) problems.push("Choose a file.");
  if (!host.trim()) problems.push("Enter the receiver address.");
  if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) problems.push("Port must be between 1 and 65535.");
  if (chunkCount > (cfg?.max_chunks ?? 65536)) problems.push(`This file would need ${chunkCount} chunks (limit ${cfg?.max_chunks}). Pick a larger chunk size.`);

  const upload = async (f: File) => {
    setError(null); setProgress(0);
    try {
      const staged = await uploadFile(f, setProgress);
      await refresh(); setSel(staged.file_id);
    } catch (e: any) { setError(e.message); } finally { setProgress(null); }
  };

  const start = async () => {
    if (!picked || problems.length) return;
    setBusy(true); setError(null);
    try {
      const r = await api.start({ file_id: picked.file_id, host: host.trim(), port: portNum, connections: conns, chunk_size: chunk, restart });
      nav.go("live", r.job_id);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head">
        <div><h1>New transfer</h1>
          <p>Pick a file, choose how many TCP sockets to use, and send it to the receiver. Each socket carries different chunks at the same time.</p></div>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="grid2">
        <section className="panel">
          <h2>File</h2>
          <div className={`drop ${over ? "over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) upload(f); }}>
            <p>Drop a file here, or</p>
            <button className="btn ghost" onClick={() => input.current?.click()} disabled={progress !== null}>Choose file</button>
            <input ref={input} type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
            {progress !== null && <div className="bars" aria-label="Upload progress"><div style={{ width: `${progress * 100}%` }} /></div>}
          </div>
          <p className="hint" style={{ color: "var(--muted)", fontSize: "0.82rem", marginTop: "0.6rem" }}>
            Browsers cannot reveal a file's path, so the file is first copied to this machine's <code>data/outbox</code> folder for the sender.
            This copy is local staging only. The transfer itself runs over raw TCP sockets.
          </p>
          <h3 style={{ margin: "1rem 0 0.4rem" }}>Staged files</h3>
          {files.length === 0 ? <p style={{ color: "var(--muted)" }}>Nothing staged yet. Add a file above.</p> : (
            <div className="scroll"><table>
              <thead><tr><th /><th>Name</th><th>Size</th><th>Added</th><th /></tr></thead>
              <tbody>{files.map((f) => (
                <tr key={f.file_id} className="click" onClick={() => setSel(f.file_id)}>
                  <td><input type="radio" name="file" checked={sel === f.file_id} onChange={() => setSel(f.file_id)} aria-label={`Select ${f.name}`} /></td>
                  <td>{f.name}</td><td>{bytes(f.size)}</td><td>{dateTime(f.created_at)}</td>
                  <td><button className="btn ghost small" onClick={(e) => { e.stopPropagation(); api.deleteFile(f.file_id).then(() => { if (sel === f.file_id) setSel(null); refresh(); }); }}>Remove</button></td>
                </tr>))}</tbody>
            </table></div>
          )}
          {picked && (
            <div style={{ marginTop: "1rem" }}>
              <h3 style={{ marginBottom: "0.4rem" }}>Selected file</h3>
              <dl className="dl">
                <dt>Name</dt><dd>{picked.name}</dd>
                <dt>Size</dt><dd>{picked.size.toLocaleString()} bytes ({bytes(picked.size)})</dd>
                <dt>SHA-256</dt><dd className="mono">{picked.sha256}</dd>
                <dt>Chunks</dt><dd>{chunkCount === 0 ? "0 (empty file; only the final check runs)" : `${chunkCount} of ${bytes(Math.min(chunk, picked.size))}${picked.size % chunk ? `, last chunk ${bytes(picked.size % chunk)}` : ""}`}</dd>
              </dl>
            </div>
          )}
        </section>

        <section className="panel">
          <h2>Receiver and settings</h2>
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div className="field" style={{ flex: "2 1 160px" }}><label htmlFor="host">Server address</label>
              <input id="host" type="text" value={host} onChange={(e) => setHost(e.target.value)} />
              {cfg && !cfg.remote_targets_allowed && <div className="hint">Only localhost is allowed unless the API starts with MPFT_ALLOW_REMOTE_TARGETS=1.</div>}</div>
            <div className="field" style={{ flex: "1 1 90px" }}><label htmlFor="port">Port</label>
              <input id="port" type="number" value={port} onChange={(e) => setPort(e.target.value)} min={1} max={65535} /></div>
          </div>
          <div className="field"><label id="cl">Parallel TCP connections</label>
            <div className="seg" role="group" aria-labelledby="cl">{CONNS.map((n) => <button key={n} aria-pressed={conns === n} onClick={() => setConns(n)}>{n}</button>)}</div>
            <div className="hint">Never more sockets than chunks. More is not always faster; see the Benchmark page.</div></div>
          <div className="field"><label htmlFor="cs">Chunk size</label>
            <select id="cs" value={chunk} onChange={(e) => setChunk(Number(e.target.value))}>{CHUNKS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}</select></div>
          <div className="field"><label style={{ display: "flex", gap: "0.5rem", fontWeight: 400 }}>
            <input type="checkbox" checked={restart} onChange={(e) => setRestart(e.target.checked)} />
            Start over: discard any partial copy the receiver holds and re-send everything</label>
            <div className="hint">Leave unchecked to resume. If the receiver already holds valid chunks of this exact file, only the missing ones are sent.</div></div>
          <Notice tone="warn">Plain TCP is not encrypted. Anyone on the path could read the file. Use this on localhost or a trusted network.</Notice>
          {problems.length > 0 && picked !== null && <Notice tone="bad">{problems.join(" ")}</Notice>}
          <button className="btn" onClick={start} disabled={busy || problems.length > 0}>{busy ? "Starting…" : "Start transfer"}</button>
        </section>
      </div>
    </>
  );
}
