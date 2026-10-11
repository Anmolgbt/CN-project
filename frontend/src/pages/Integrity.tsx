import type { Nav } from "../App";
import { Empty, Notice } from "../components/ui";
import { api } from "../services/api";
import { bytes, duration, mbps } from "../services/format";
import { useJob } from "../services/useJob";
import { TERMINAL } from "../types";

const VERDICT: Record<string, { cls: string; title: string; sub: string }> = {
  completed: { cls: "ok", title: "Transfer complete and integrity verified", sub: "Every chunk validated, and the receiver's final SHA-256 equals the sender's." },
  integrity_failed: { cls: "bad", title: "Integrity verification failed", sub: "All chunks arrived, but the reassembled file's SHA-256 differs from the original. Nothing was published; restart the transfer." },
  incomplete: { cls: "warn", title: "Transfer incomplete", sub: "Some chunks never arrived. The receiver kept the valid ones; resume to send the rest." },
  cancelled: { cls: "warn", title: "Transfer cancelled", sub: "Stopped before verification. Valid chunks are kept on the receiver, so it can be resumed." },
  failed: { cls: "bad", title: "Transfer failed", sub: "The transfer could not start or continue." },
};

export default function Integrity({ nav }: { nav: Nav }) {
  const { snap } = useJob(nav.jobId);
  if (!nav.jobId) return <Empty>Run or select a transfer first. <button className="btn small" onClick={() => nav.go("history")}>Open history</button></Empty>;
  if (!snap) return <Empty>Loading…</Empty>;
  const r = snap.result;
  if (!r || !TERMINAL.includes(snap.status))
    return <Empty>This transfer has not finished yet. <button className="btn small" onClick={() => nav.go("live")}>Watch it live</button></Empty>;
  const v = VERDICT[r.status] ?? VERDICT.failed;
  const match = r.checksum_match;
  return (
    <>
      <div className="page-head"><div><h1>Integrity verification</h1><p>{r.file_name}</p></div></div>
      <div className={`verdict ${v.cls}`} role="status"><div>{v.title}<small>{v.sub}</small></div></div>
      {r.error && r.status !== "completed" && <Notice tone="warn">{r.error}</Notice>}
      <div className="grid2">
        <section className="panel">
          <h2>SHA-256 comparison</h2>
          <h3 style={{ margin: "0.4rem 0 0.25rem" }}>Original (computed by the sender)</h3>
          <div className="hash">{r.original_sha256}</div>
          <h3 style={{ margin: "0.8rem 0 0.25rem" }}>Received (computed by the receiver after reassembly)</h3>
          <div className="hash">{r.received_sha256 ?? "Not computed: the receiver only hashes when every chunk is present."}</div>
          <p style={{ marginTop: "0.8rem" }}><span className={`chip ${match === true ? "ok" : match === false ? "bad" : "warn"}`}><i />{match === true ? "Checksums match" : match === false ? "Checksums differ" : "No comparison possible"}</span></p>
        </section>
        <section className="panel">
          <h2>Chunk accounting</h2>
          <dl className="dl">
            <dt>Expected chunks</dt><dd>{r.expected_chunks}</dd>
            <dt>Validated chunks</dt><dd>{r.validated_chunks}</dd>
            <dt>Missing or invalid</dt><dd>{r.missing_count}{r.missing_chunks.length > 0 && <> <span className="mono">(ids: {r.missing_chunks.slice(0, 20).join(", ")}{r.missing_chunks.length > 20 ? ", …" : ""})</span></>}</dd>
            <dt>Failed after retries</dt><dd>{r.failed_chunks}</dd>
            <dt>Final file size</dt><dd>{r.final_size != null ? `${r.final_size.toLocaleString()} bytes (${bytes(r.final_size)})` : "Not published"}</dd>
            <dt>Resumed from receiver</dt><dd>{r.resumed_chunks} chunks ({bytes(r.resumed_bytes)})</dd>
            <dt>Time / average / peak</dt><dd>{duration(r.elapsed)} / {mbps(r.avg_mb_s)} / {mbps(r.peak_mb_s)}</dd>
            <dt>Retries</dt><dd>{r.retries}</dd>
          </dl>
        </section>
      </div>
      <section className="panel">
        <h2>Where the file is</h2>
        {r.status === "completed" ? (
          <>
            <p>Saved by the receiver as <code>{r.saved_as}</code> in its output directory{nav.config?.received_dir ? <> (<code>{nav.config.received_dir}</code>)</> : ""}.</p>
            {nav.config?.embedded_receiver
              ? <a className="btn" href={api.downloadUrl(r.job_id)} download>Download verified file</a>
              : <p style={{ color: "var(--muted)" }}>The receiver runs separately, so the dashboard cannot serve the file. Open its output directory instead.</p>}
          </>
        ) : <p style={{ color: "var(--muted)" }}>Nothing was published, because a file is only moved into the output directory after the final SHA-256 matches.</p>}
      </section>
    </>
  );
}
