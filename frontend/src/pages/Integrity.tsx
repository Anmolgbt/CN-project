import type { Nav } from "../App";
import { Empty, Note, Section } from "../components/ui";
import { api } from "../services/api";
import { bytes, duration, mbps } from "../services/format";
import { useJob } from "../services/useJob";
import { TERMINAL } from "../types";

const VERDICT: Record<string, { tone: string; title: string; sub: string }> = {
  completed: { tone: "ok", title: "The received file is identical to the original.", sub: "Every chunk passed its own check, and the receiver's SHA-256 of the finished file equals the sender's." },
  integrity_failed: { tone: "bad", title: "The finished file does not match the original.", sub: "All chunks arrived, but their combined SHA-256 differs. Nothing was saved to the output folder. Start the transfer again." },
  incomplete: { tone: "warn", title: "The transfer stopped before every chunk arrived.", sub: "The receiver kept the chunks it had validated. Resume the transfer to send only the rest." },
  cancelled: { tone: "warn", title: "The transfer was cancelled before it was checked.", sub: "The chunks already validated are still on the receiver, so it can be resumed." },
  failed: { tone: "bad", title: "The transfer could not run.", sub: "It failed before or during sending. The message below says why." },
};

/** Shows `b`, marking every character that differs from `a`. */
function HashDiff({ a, b }: { a: string; b: string }) {
  return <span>{[...b].map((ch, i) => (ch === a[i] ? ch : <span key={i} className="diff">{ch}</span>))}</span>;
}

export default function Integrity({ nav }: { nav: Nav }) {
  const { snap } = useJob(nav.jobId);
  if (!nav.jobId) return <><h1>Integrity check</h1><Empty>Run a transfer first, or open one from <button className="textbtn" onClick={() => nav.go("history")}>History</button>.</Empty></>;
  if (!snap) return <><h1>Integrity check</h1><Empty>Loading…</Empty></>;
  const r = snap.result;
  if (!r || !TERMINAL.includes(snap.status))
    return <><h1>Integrity check</h1><Empty>This transfer is still running. The check happens after the last chunk arrives. <button className="textbtn" onClick={() => nav.go("live")}>Watch it live</button></Empty></>;

  const v = VERDICT[r.status] ?? VERDICT.failed;
  const match = r.checksum_match;
  return (
    <>
      <h1>Integrity check</h1>
      <p className="lede">{r.file_name}</p>
      <p className={`verdict ${v.tone}`}>{v.title}</p>
      <p className="verdict-sub">{v.sub}</p>
      {r.error && r.status !== "completed" && <Note tone="warn">{r.error}</Note>}

      <Section title="SHA-256 of the file" first={false}>
        <p className="hash-label">Original, computed by the sender before sending</p>
        <p className="hash">{r.original_sha256}</p>
        <p className="hash-label">Received, computed by the receiver after rebuilding the file</p>
        <p className="hash">{r.received_sha256 && r.original_sha256 ? <HashDiff a={r.original_sha256} b={r.received_sha256} /> : "Not computed. The receiver only hashes the file once every chunk is present."}</p>
        <p style={{ marginTop: 14, fontWeight: 650 }} className={match === true ? "ok" : match === false ? "bad" : "muted"}>
          {match === true ? "The two values are identical." : match === false ? "The two values differ; the differing characters are highlighted." : "There is nothing to compare yet."}
        </p>
      </Section>

      <Section title="What arrived">
        <dl className="facts" style={{ maxWidth: 620 }}>
          <dt>Chunks expected</dt><dd><span className="n">{r.expected_chunks}</span></dd>
          <dt>Chunks validated</dt><dd><span className="n">{r.validated_chunks}</span></dd>
          <dt>Missing or invalid</dt>
          <dd><span className="n">{r.missing_count}</span>{r.missing_chunks.length > 0 && <span className="muted"> (chunk {r.missing_chunks.slice(0, 20).join(", ")}{r.missing_chunks.length > 20 ? ", and more" : ""})</span>}</dd>
          <dt>Gave up after retries</dt><dd><span className="n">{r.failed_chunks}</span></dd>
          <dt>Final file size</dt><dd>{r.final_size != null ? <><span className="n">{r.final_size.toLocaleString()}</span> bytes ({bytes(r.final_size)})</> : "Not saved"}</dd>
          <dt>Already on the receiver</dt><dd>{r.resumed_chunks} chunks ({bytes(r.resumed_bytes)}) from an earlier attempt</dd>
          <dt>Time, average, best</dt><dd>{duration(r.elapsed)}, {mbps(r.avg_mb_s)}, {mbps(r.peak_mb_s)}</dd>
          <dt>Chunks sent again</dt><dd>{r.retries}</dd>
        </dl>
      </Section>

      <Section title="Where the file is">
        {r.status === "completed" ? (
          <>
            <p>The receiver saved it as <b>{r.saved_as}</b>{nav.config?.received_dir ? <> in <code>{nav.config.received_dir}</code></> : ""}.</p>
            {nav.config?.embedded_receiver
              ? <p style={{ marginTop: 14 }}><a className="btn" href={api.downloadUrl(r.job_id)} download>Download verified file</a></p>
              : <p className="muted" style={{ marginTop: 8 }}>The receiver runs as a separate program, so this page cannot serve the file. Open the receiver's output folder instead.</p>}
          </>
        ) : <p className="muted">Nothing was saved. The receiver only moves a file into its output folder after the final SHA-256 matches.</p>}
      </Section>
    </>
  );
}
