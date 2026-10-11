import type { AppConfig, Benchmark, Snapshot, StagedFile, Summary, TEvent } from "../types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network", "Cannot reach the API. Is it running on port 8000?");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(res.status, e?.code ?? "error", e?.message ?? `Request failed (${res.status})`);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });

export interface StartBody {
  file_id: string; host: string; port: number; connections: number; chunk_size: number; restart: boolean;
}
export interface BenchBody {
  file_id?: string; size_mb?: number; host: string; port: number; chunk_size: number; connections: number[]; repeats: number;
}

export const api = {
  health: () => call<{ status: string }>("/api/health"),
  config: () => call<AppConfig>("/api/config"),
  files: () => call<StagedFile[]>("/api/files"),
  deleteFile: (id: string) => call<void>(`/api/files/${encodeURIComponent(id)}`, { method: "DELETE" }),
  start: (b: StartBody) => post<{ job_id: string }>("/api/transfers", b),
  transfer: (id: string) => call<Snapshot>(`/api/transfers/${id}`),
  live: () => call<Summary[]>("/api/transfers"),
  events: (id: string, since = 0) => call<TEvent[]>(`/api/transfers/${id}/events?since=${since}&limit=5000`),
  pause: (id: string) => post(`/api/transfers/${id}/pause`),
  unpause: (id: string) => post(`/api/transfers/${id}/continue`),
  cancel: (id: string) => post(`/api/transfers/${id}/cancel`),
  resume: (id: string) => post<{ job_id: string }>(`/api/transfers/${id}/resume`),
  history: () => call<Summary[]>("/api/history"),
  startBench: (b: BenchBody) => post<Benchmark>("/api/benchmarks", b),
  bench: (id: string) => call<Benchmark>(`/api/benchmarks/${id}`),
  benches: () => call<Benchmark[]>("/api/benchmarks"),
  cancelBench: (id: string) => post(`/api/benchmarks/${id}/cancel`),
  downloadUrl: (id: string) => `/api/transfers/${id}/download`,
};

/** Stage a file on the sender machine. XHR is used because fetch cannot report upload progress. */
export function uploadFile(file: File, onProgress: (frac: number) => void): Promise<StagedFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/files?name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onerror = () => reject(new ApiError(0, "network", "Upload failed: cannot reach the API."));
    xhr.onload = () => {
      let body: any = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* keep null */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as StagedFile);
      else reject(new ApiError(xhr.status, body?.error?.code ?? "error", body?.error?.message ?? "Upload failed"));
    };
    xhr.send(file);
  });
}
