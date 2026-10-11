import { useCallback, useEffect, useRef, useState } from "react";
import { TERMINAL, type Snapshot, type TEvent } from "../types";
import { api } from "./api";
import { wsUrl } from "./wsUrl";

export interface Sample { t: number; current: number; average: number }

interface Acc {
  lastSeq: number;
  chunkConn: Record<number, number>;          // chunk id -> connection that delivered it
  lanes: Record<number, number[]>;            // connection id -> recently acknowledged chunk ids
  events: TEvent[];
  samples: Sample[];
  filling: boolean;
}

const fresh = (): Acc => ({ lastSeq: 0, chunkConn: {}, lanes: {}, events: [], samples: [], filling: false });

/** Subscribes to a job (WebSocket, REST fallback) and accumulates events, lanes and throughput samples. */
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
    for (const e of [...list].sort((x, y) => x.seq - y.seq)) {
      if (e.seq <= a.lastSeq) continue;
      a.lastSeq = e.seq;
      if (e.type === "chunk_acked" && e.conn_id != null && e.chunk_id != null) {
        a.chunkConn[e.chunk_id] = e.conn_id;
        const lane = (a.lanes[e.conn_id] ??= []);
        lane.push(e.chunk_id);
        if (lane.length > 14) lane.shift();
      }
      a.events.push(e);
    }
    if (a.events.length > 600) a.events.splice(0, a.events.length - 600);
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
    const last = a.samples[a.samples.length - 1];
    if ((s.status === "transferring" || s.status === "verifying") && s.elapsed > (last?.t ?? -1)) {
      a.samples.push({ t: s.elapsed, current: s.throughput.current / 1e6, average: s.throughput.average / 1e6 });
      if (a.samples.length > 900) a.samples.shift();
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
        api.events(jobId, 0).then((list) => { processEvents(list); bump((n) => n + 1); }).catch(() => {});
      }
    }, 1000);
    return () => { closed = true; clearInterval(timer); ws?.close(); };
  }, [jobId, ingest, processEvents]);

  return { snap, error, acc: acc.current };
}
