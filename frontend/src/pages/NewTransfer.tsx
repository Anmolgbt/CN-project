import { useEffect, useRef, useState } from "react";
import type { Nav } from "../App";
import { Note } from "../components/ui";
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
  if (!picked) problems.push("Choose a file first.");
  if (!host.trim()) problems.push("Enter the receiver's address.");
  if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) problems.push("The port must be a number from 1 to 65535.");
  if (chunkCount > (cfg?.max_chunks ?? 65536)) problems.push(`This file would need ${chunkCount.toLocaleString()} chunks and the limit is ${cfg?.max_chunks.toLocaleString()}. Pick a larger chunk size.`);

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

  const lastChunk = picked ? picked.size - (chunkCount - 1) * chunk : 0;

  return (
    <>
      <h1>New transfer</h1>
      <p className="lede">Choose a file and how many TCP sockets to send it over. Each socket carries different chunks at the same moment.</p>
      {error && <Note tone="bad">{error}</Note>}

      <div className="cols" style={{ marginTop: 32 }}>
        <section>
          <h2>File</h2>
          <div className={`drop ${over ? "over" : ""}`} style={{ marginTop: 12 }}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) upload(f); }}>
            <button className="btn plain" onClick={() => input.current?.click()} disabled={progress !== null}>Choose a file</button>
            <span className="muted">or drop one here</span>
            <input ref={input} type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          </div>
          {progress !== null && <div className="upbar" aria-label="Copy progress"><div style={{ width: `${progress * 100}%` }} /></div>}
          <p className="small muted" style={{ marginTop: 10, maxWidth: "56ch" }}>
            A browser cannot tell the sender where a file lives, so the file is first copied into this computer's <code>data/outbox</code> folder.
            That copy is only a hand-off. The transfer itself runs over raw TCP sockets.
          </p>

          <h2 style={{ marginTop: 32 }}>Files ready to send</h2>
          {files.length === 0 ? <p className="empty">Nothing yet. Add a file above.</p> : (
            <div className="table" style={{ marginTop: 6 }}>
              <table>
                <tbody>{files.map((f) => (
                  <tr key={f.file_id} className="pick" aria-selected={sel === f.file_id} onClick={() => setSel(f.file_id)}>
                    <td style={{ width: 24 }}><input type="radio" name="file" checked={sel === f.file_id} onChange={() => setSel(f.file_id)} aria-label={`Send ${f.name}`} /></td>
                    <td>{f.name}</td><td>{bytes(f.size)}</td><td className="muted">{dateTime(f.created_at)}</td>
                    <td style={{ textAlign: "right", paddingRight: 0 }}><button className="textbtn" onClick={(e) => { e.stopPropagation(); api.deleteFile(f.file_id).then(() => { if (sel === f.file_id) setSel(null); refresh(); }); }}>Remove</button></td>
                  </tr>))}</tbody>
              </table>
            </div>
          )}

          {picked && (
            <>
              <h2 style={{ marginTop: 32 }}>Selected file</h2>
              <dl className="facts" style={{ marginTop: 10 }}>
                <dt>Name</dt><dd>{picked.name}</dd>
                <dt>Size</dt><dd>{picked.size.toLocaleString()} bytes ({bytes(picked.size)})</dd>
                <dt>SHA-256</dt><dd className="hash">{picked.sha256}</dd>
                <dt>Chunks</dt>
                <dd>{chunkCount === 0 ? "None. The file is empty, so only the final check runs."
                  : chunkCount === 1 ? `1 chunk of ${bytes(picked.size)}`
                  : `${chunkCount.toLocaleString()} chunks of ${bytes(chunk)}${lastChunk !== chunk ? `, the last one ${bytes(lastChunk)}` : ""}`}</dd>
              </dl>
            </>
          )}
        </section>

        <section>
          <h2>Settings</h2>
          <div className="row" style={{ marginTop: 12 }}>
            <div className="field" style={{ flex: "2 1 180px" }}>
              <label htmlFor="host">Receiver address</label>
              <input id="host" type="text" value={host} onChange={(e) => setHost(e.target.value)} />
            </div>
            <div className="field" style={{ flex: "1 1 90px" }}>
              <label htmlFor="port">Port</label>
              <input id="port" type="number" value={port} onChange={(e) => setPort(e.target.value)} min={1} max={65535} />
            </div>
          </div>
          {cfg && !cfg.remote_targets_allowed && <p className="help small muted" style={{ marginTop: -8, marginBottom: 20 }}>Only this computer is allowed as a receiver unless the API is started with <code>MPFT_ALLOW_REMOTE_TARGETS=1</code>.</p>}

          <div className="field">
            <label id="cl">Parallel TCP connections</label>
            <div className="choice" role="group" aria-labelledby="cl">{CONNS.map((n) => <button key={n} aria-pressed={conns === n} onClick={() => setConns(n)}>{n}</button>)}</div>
            <p className="help">A file never uses more sockets than it has chunks. More sockets are not always faster, as the Benchmark page shows.</p>
          </div>
          <div className="field">
            <label htmlFor="cs">Chunk size</label>
            <select id="cs" value={chunk} onChange={(e) => setChunk(Number(e.target.value))}>{CHUNKS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}</select>
          </div>
          <div className="field">
            <label className="check"><input type="checkbox" checked={restart} onChange={(e) => setRestart(e.target.checked)} />Start over and discard what the receiver already holds</label>
            <p className="help">Leave this off to resume. If the receiver already has valid chunks of this exact file, only the missing ones are sent.</p>
          </div>

          <p className="note-block warn small">This connection is plain TCP and is not encrypted. Use it on this computer or a network you trust.</p>
          {problems.length > 0 && picked !== null && <Note tone="bad">{problems.join(" ")}</Note>}
          <button className="btn" onClick={start} disabled={busy || problems.length > 0}>{busy ? "Starting…" : "Start transfer"}</button>
        </section>
      </div>
    </>
  );
}
