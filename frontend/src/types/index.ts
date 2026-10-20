export type JobStatus =
  | "created" | "scanning" | "connecting" | "transferring" | "paused" | "verifying"
  | "completed" | "incomplete" | "failed" | "integrity_failed" | "cancelled";

export const TERMINAL: JobStatus[] = ["completed", "incomplete", "failed", "integrity_failed", "cancelled"];

export interface TEvent {
  seq: number; ts: number; type: string; message: string; conn_id?: number; chunk_id?: number;
}

export interface Conn {
  conn_id: number; status: string; bytes_acked: number; chunks_acked: number; retries: number;
  reconnects: number; current_chunk: number | null; current_rate: number; avg_rate: number; last_error: string | null;
}

export interface Result {
  status: JobStatus; error: string | null; job_id: string; transfer_id: string | null;
  file_name: string | null; file_size: number | null; original_sha256: string | null;
  received_sha256: string | null; checksum_match: boolean | null; expected_chunks: number;
  validated_chunks: number; missing_count: number; missing_chunks: number[]; failed_chunks: number;
  final_size: number | null; saved_as: string | null; elapsed: number; data_seconds: number;
  avg_mb_s: number; peak_mb_s: number; bytes_acked: number; bytes_sent: number; resumed_chunks: number;
  resumed_bytes: number; retries: number; connections_requested: number; connections_used: number; chunk_size: number;
}

export interface Snapshot {
  job_id: string; status: JobStatus; error: string | null; transfer_id: string | null;
  file: { name: string; size: number; sha256: string; chunks: number } | null;
  config: { host: string; port: number; connections: number; chunk_size: number };
  scan_progress: number; started_at: number | null; finished_at: number | null; elapsed: number;
  eta_seconds: number | null;
  bytes: { acked: number; resumed: number; done: number; total: number; sent: number };
  throughput: { current: number; average: number; peak: number };
  chunks: { total: number; completed: number; pending: number; in_progress: number; retrying: number; failed: number };
  chunk_states: string; chunk_owner: Record<string, number>; chunk_attempts: Record<string, number>;
  connections: Conn[]; retries: number; resumed_chunks: number; events: TEvent[]; last_seq: number;
  result: Result | null;
  now: number;                 // server clock when the snapshot was taken (epoch seconds)
}

export interface Summary {
  job_id: string; kind: string; status: JobStatus; file_name: string | null; file_size: number | null;
  connections: number; connections_used: number | null; chunk_size: number; elapsed: number | null;
  avg_mb_s: number | null; peak_mb_s: number | null; retries: number | null; failed_chunks: number | null;
  checksum_match: boolean | null; started_at: number | null; finished_at: number | null; error: string | null;
  chunks_completed: number; chunks_total: number; transfer_id: string | null; resumable: boolean;
}

export interface StagedFile { file_id: string; name: string; size: number; sha256: string; created_at: number }

export interface AppConfig {
  default_host: string; default_port: number; embedded_receiver: boolean; connection_options: number[];
  chunk_size_options: number[]; max_connections: number; max_chunks: number; max_upload_bytes: number;
  received_dir: string | null; remote_targets_allowed: boolean; encrypted: boolean;
}

export interface BenchRun {
  job_id: string; connections: number; connections_used: number; repeat: number; status: string; elapsed: number;
  avg_mb_s: number; peak_mb_s: number; bytes_acked: number; retries: number; completed_chunks: number;
  expected_chunks: number; integrity: boolean | null;
}

export interface BenchRow {
  connections: number; runs: number; elapsed: number; avg_mb_s: number; peak_mb_s: number; bytes_acked: number;
  retries: number; completed_chunks: number; expected_chunks: number; integrity: boolean; speedup_vs_1: number | null;
}

export interface Benchmark {
  id: string; status: string; created_at: number; file_name: string; file_size: number; chunk_size: number;
  connections: number[]; repeats: number; runs: BenchRun[]; summary: BenchRow[]; current_job_id: string | null;
  error: string | null;
}

export type Page = "overview" | "new" | "live" | "integrity" | "history" | "benchmark";
