import { useCallback, useEffect, useRef, useState } from "react";
import { TERMINAL, type Snapshot, type TEvent } from "../types";
import { api } from "./api";
import { wsUrl } from "./wsUrl";

/** One chunk on one socket, from the moment it was sent until it was acknowledged (or failed). */
export interface Span {
  chunk: number; conn: number; t0: number; t1: number | null;
  outcome: "open" | "ok" | "rejected" | "dropped";
}

interface Acc {
  lastSeq: number;
  chunkConn: Record<number, number>;          // chunk id -> connection that delivered it
  spans: Span[];                              // built from real chunk_started / chunk_acked / nack / conn_error events
  open: Record<number, Span>;                 // the chunk each connection is sending right now
  events: TEvent[];
  filling: boolean;
}

const MAX_SPANS = 4000;
const fresh = (): Acc => ({ lastSeq: 0, chunkConn: {}, spans: [], open: {}, events: [], filling: false });

/** Subscribes to a job (WebSocket, REST fallback) and accumulates events, chunk spans and speed samples. */
export function useJob(jobId: string | null) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, bump] = useState(0);
  const acc = useRef<Acc>(fresh());
  const lastMsg = useRef(0);
  const snapRef = useRef<Snapshot | null>(null);
  snapRef.current = snap;

  const processEvents = useCallback((list: TEvent[]) => {
    const a = acc.current;
    const close = (conn: number, t: number, outcome: Span["outcome"]) => {
      const sp = a.open[conn];
      if (sp) { sp.t1 = t; sp.outcome = outcome; delete a.open[conn]; }
      return sp;
    };
    for (const e of [...list].sort((x, y) => x.seq - y.seq)) {
      if (e.seq <= a.lastSeq) continue;
      a.lastSeq = e.seq;
      if (e.conn_id != null && e.chunk_id != null) {
        if (e.type === "chunk_started") {
          const sp: Span = { chunk: e.chunk_id, conn: e.conn_id, t0: e.ts, t1: null, outcome: "open" };
          a.open[e.conn_id] = sp;
          a.spans.push(sp);
        } else if (e.type === "chunk_acked") {
          a.chunkConn[e.chunk_id] = e.conn_id;
          const sp = a.open[e.conn_id];
          if (sp && sp.chunk === e.chunk_id) close(e.conn_id, e.ts, "ok");
          else a.spans.push({ chunk: e.chunk_id, conn: e.conn_id, t0: e.ts, t1: e.ts, outcome: "ok" });
        } else if (e.type === "chunk_nack") {
          close(e.conn_id, e.ts, "rejected");
        } else if (e.type === "conn_error") {
          close(e.conn_id, e.ts, "dropped");
        }
      }
      a.events.push(e);
    }
    if (a.events.length > 600) a.events.splice(0, a.events.length - 600);
    if (a.spans.length > MAX_SPANS) a.spans.splice(0, a.spans.length - MAX_SPANS);
  }, []);

  const ingest = useCallback((id: string, s: Snapshot) => {
    const a = acc.current;
    lastMsg.current = Date.now();
    if (!a.filling) {
      const first = s.events[0];
      if (first && a.lastSeq > 0 && first.seq > a.lastSeq + 1) {
        a.filling = true;                       // missed events between snapshots: fetch them
        api.events(id, a.lastSeq).then(processEvents).catch(() => {}).finally(() => { a.filling = false; bump((n) => n + 1); });
      } else {
        processEvents(s.events);
      }
    }
    setSnap(s);
    setError(null);
  }, [processEvents]);

  useEffect(() => {
    acc.current = fresh();
    setSnap(null);
    if (!jobId) return;
    let closed = false;
    let ws: WebSocket | null = null;
    let finalFetched = false;

    const load = () => api.transfer(jobId).then((s) => !closed && ingest(jobId, s)).catch((e) => !closed && setError(e.message));
    load();
    const connect = () => {
      try {
        ws = new WebSocket(wsUrl(jobId, location));
      } catch {
        return;                                   // no WebSocket available: the REST poll below keeps the page live
      }
      ws.onmessage = (m) => {
        try { const msg = JSON.parse(m.data); if (msg.type === "snapshot" && !closed) ingest(jobId, msg.data); } catch { /* ignore bad frame */ }
      };
      ws.onclose = () => { if (!closed) setTimeout(connect, 1000); };
    };
    connect();
    const timer = setInterval(() => {
      if (Date.now() - lastMsg.current > 2500) load();          // WebSocket quiet: poll
      const s = acc.current;
      const terminal = TERMINAL.includes((snapRef.current?.status ?? "created") as never);
      if (terminal && !finalFetched && !s.filling) {                // one full event fetch for finished jobs
        finalFetched = true;
        api.events(jobId, 0).then((list) => {
          // finished job: replace the partial tail we hold with the complete history
          Object.assign(s, { lastSeq: 0, chunkConn: {}, spans: [], open: {}, events: [] });
          processEvents(list);
          bump((n) => n + 1);
        }).catch(() => {});
      }
    }, 1000);
    return () => { closed = true; clearInterval(timer); ws?.close(); };
  }, [jobId, ingest, processEvents]);

  return { snap, error, acc: acc.current };
}
