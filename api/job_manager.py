"""Runs transfers and benchmarks in background threads; persists history; manages uploads."""
from __future__ import annotations

import json
import logging
import random
import statistics
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.schemas import ApiError, StartBenchmark, StartTransfer
from client.config import ClientConfig
from client.socket_client import TERMINAL, TransferJob
from common.integrity import total_chunks
from server.chunk_storage import sanitize_filename
from server.config import ServerConfig, is_loopback

log = logging.getLogger("mpft.api")
MAX_ACTIVE_JOBS = 4
MAX_CHUNKS = 65_536


class JobManager:
    """Owns TransferJob threads. The data plane stays in client/ + server/; this is control plane only."""

    def __init__(self, data_dir: Path, embedded: Optional[ServerConfig] = None, default_port: int = 9000,
                 allow_remote_targets: bool = False, max_upload: int = 4 * 1024 ** 3) -> None:
        self.data_dir = Path(data_dir)
        self.outbox = self.data_dir / "outbox"
        self.meta_dir = self.outbox / ".meta"
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.data_dir / "history.jsonl"
        self.bench_path = self.data_dir / "benchmarks.jsonl"
        self.embedded = embedded
        self.default_port = default_port
        self.allow_remote_targets = allow_remote_targets
        self.max_upload = max_upload
        self.jobs: Dict[str, TransferJob] = {}
        self.params: Dict[str, Dict[str, Any]] = {}
        self.bench: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ files
    def new_upload(self, name: str) -> tuple[str, Path]:
        clean = sanitize_filename(name)
        file_id = f"{uuid.uuid4().hex[:8]}_{clean}"
        return file_id, self.outbox / file_id

    def register_upload(self, file_id: str, name: str, size: int, sha256: str) -> Dict[str, Any]:
        meta = {"file_id": file_id, "name": sanitize_filename(name), "size": size, "sha256": sha256,
                "created_at": time.time()}
        (self.meta_dir / f"{file_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def _file_meta(self, file_id: str) -> Dict[str, Any]:
        if Path(file_id).name != file_id or file_id.startswith("."):
            raise ApiError(400, "bad_file_id", "invalid file id")
        mp, fp = self.meta_dir / f"{file_id}.json", self.outbox / file_id
        if not mp.exists() or not fp.is_file():
            raise ApiError(404, "file_not_found", "unknown file; upload it first")
        return json.loads(mp.read_text(encoding="utf-8"))

    def get_file(self, file_id: str) -> tuple[Path, Dict[str, Any]]:
        meta = self._file_meta(file_id)
        return self.outbox / file_id, meta

    def list_files(self) -> List[Dict[str, Any]]:
        out = []
        for mp in self.meta_dir.glob("*.json"):
            try:
                m = json.loads(mp.read_text(encoding="utf-8"))
                if (self.outbox / m["file_id"]).is_file():
                    out.append(m)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(out, key=lambda m: m["created_at"], reverse=True)

    def delete_file(self, file_id: str) -> None:
        self._file_meta(file_id)
        (self.outbox / file_id).unlink(missing_ok=True)
        (self.meta_dir / f"{file_id}.json").unlink(missing_ok=True)

    # ------------------------------------------------------------- validation
    def _check_target(self, host: str) -> None:
        if not self.allow_remote_targets and not is_loopback(host):
            raise ApiError(400, "remote_target_disabled",
                           "Only localhost receivers are allowed. Set MPFT_ALLOW_REMOTE_TARGETS=1 to send elsewhere "
                           "(the TCP stream is not encrypted).")

    def _active(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status not in TERMINAL)

    # --------------------------------------------------------------- transfers
    def start_transfer(self, req: StartTransfer, kind: str = "transfer") -> TransferJob:
        self._check_target(req.host)
        path, meta = self.get_file(req.file_id)
        if total_chunks(meta["size"], req.chunk_size) > MAX_CHUNKS:
            raise ApiError(422, "too_many_chunks", f"{meta['size']} bytes need more than {MAX_CHUNKS} chunks; "
                                                   "choose a larger chunk size")
        with self._lock:
            if self._active() >= MAX_ACTIVE_JOBS:
                raise ApiError(429, "too_many_jobs", f"at most {MAX_ACTIVE_JOBS} transfers may run at once")
            job = TransferJob(path, ClientConfig(host=req.host, port=req.port, connections=req.connections,
                                                 chunk_size=req.chunk_size),
                              display_name=meta["name"], restart=req.restart)
            self.jobs[job.job_id] = job
            self.params[job.job_id] = {**req.model_dump(), "file_name": meta["name"], "kind": kind}
        threading.Thread(target=self._run_and_record, args=(job, kind), daemon=True,
                         name=f"mpft-job-{job.job_id}").start()
        return job

    def _run_and_record(self, job: TransferJob, kind: str) -> Dict[str, Any]:
        result = job.run()
        self._append_history(job, kind)
        return result

    def get_job(self, job_id: str) -> TransferJob:
        j = self.jobs.get(job_id)
        if j is None:
            raise ApiError(404, "job_not_found", "no such live transfer")
        return j

    def resume(self, job_id: str) -> TransferJob:
        """Start a new job for the same file/settings; the server tells it which chunks it already holds."""
        params = self.params.get(job_id)
        status = self.jobs[job_id].status if job_id in self.jobs else None
        if params is None:
            rec = self.history_record(job_id)
            params = rec["params"]
            status = rec["snapshot"]["status"]
        if status not in ("incomplete", "failed", "cancelled"):
            raise ApiError(409, "not_resumable", f"a transfer in state {status!r} cannot be resumed")
        req = StartTransfer(**{k: params[k] for k in ("file_id", "host", "port", "connections", "chunk_size")},
                            restart=False)
        return self.start_transfer(req, kind=params.get("kind", "transfer"))

    # ----------------------------------------------------------------- history
    def _append_history(self, job: TransferJob, kind: str) -> None:
        snap = job.snapshot(events_tail=300)
        snap["chunk_states"] = snap["chunk_states"][:5000]
        rec = {"job_id": job.job_id, "kind": kind, "params": self.params.get(job.job_id, {}),
               "finished_at": job.finished_at, "snapshot": snap}
        with self._lock, open(self.history_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        out = []
        with self._lock, open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    @staticmethod
    def summarize(snap: Dict[str, Any], params: Dict[str, Any], kind: str, job_id: str) -> Dict[str, Any]:
        r = snap.get("result") or {}
        f = snap.get("file") or {}
        return {
            "job_id": job_id, "kind": kind, "status": snap["status"], "file_name": f.get("name"),
            "file_size": f.get("size"), "connections": snap["config"]["connections"],
            "connections_used": r.get("connections_used"), "chunk_size": snap["config"]["chunk_size"],
            "elapsed": r.get("elapsed", snap.get("elapsed")), "avg_mb_s": r.get("avg_mb_s"),
            "peak_mb_s": r.get("peak_mb_s"), "retries": snap.get("retries"),
            "failed_chunks": (r.get("failed_chunks") if r else snap["chunks"]["failed"]),
            "checksum_match": r.get("checksum_match"), "started_at": snap.get("started_at"),
            "finished_at": snap.get("finished_at"), "error": snap.get("error"),
            "chunks_completed": snap["chunks"]["completed"], "chunks_total": snap["chunks"]["total"],
            "transfer_id": snap.get("transfer_id"), "resumable": snap["status"] in ("incomplete", "failed", "cancelled")
            and bool(params.get("file_id")),
        }

    def history(self, limit: int = 100) -> List[Dict[str, Any]]:
        recs = self._read_jsonl(self.history_path)
        recs.sort(key=lambda r: r.get("finished_at") or 0, reverse=True)
        return [self.summarize(r["snapshot"], r.get("params", {}), r.get("kind", "transfer"), r["job_id"])
                for r in recs[:limit]]

    def history_record(self, job_id: str) -> Dict[str, Any]:
        for r in reversed(self._read_jsonl(self.history_path)):
            if r["job_id"] == job_id:
                return r
        raise ApiError(404, "job_not_found", "no such transfer")

    def snapshot_any(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Live snapshot if the job is in memory, otherwise the persisted final snapshot."""
        j = self.jobs.get(job_id)
        if j is not None:
            return j.snapshot()
        try:
            return self.history_record(job_id)["snapshot"]
        except ApiError:
            return None

    def live_summaries(self) -> List[Dict[str, Any]]:
        return [self.summarize(j.snapshot(events_tail=0), self.params.get(i, {}), self.params.get(i, {}).get("kind", "transfer"), i)
                for i, j in self.jobs.items()]

    # -------------------------------------------------------------- downloads
    def completed_output(self, job_id: str) -> tuple[Path, str]:
        snap = self.snapshot_any(job_id)
        if snap is None:
            raise ApiError(404, "job_not_found", "no such transfer")
        res = snap.get("result") or {}
        if res.get("status") != "completed" or not res.get("saved_as"):
            raise ApiError(409, "not_complete", "only verified transfers can be downloaded")
        if self.embedded is None:
            raise ApiError(409, "no_local_receiver", "the receiver is not running inside this API process")
        path = self.embedded.received_dir / Path(res["saved_as"]).name      # basename only: no traversal
        if not path.is_file():
            raise ApiError(404, "output_missing", "the received file no longer exists")
        return path, res["saved_as"]

    # -------------------------------------------------------------- benchmarks
    def _bench_source(self, req: StartBenchmark) -> tuple[Path, str]:
        if req.file_id:
            path, meta = self.get_file(req.file_id)
            return path, meta["name"]
        name = f"bench_{req.size_mb}MB.bin"
        path = self.outbox / name
        size = req.size_mb * 1024 * 1024
        if not path.exists() or path.stat().st_size != size:
            rnd = random.Random(20240601)                       # fixed seed => identical bytes every run
            with open(path, "wb") as fh:
                left = size
                while left:
                    n = min(left, 1024 * 1024)
                    fh.write(rnd.randbytes(n))
                    left -= n
        return path, name

    def start_benchmark(self, req: StartBenchmark) -> Dict[str, Any]:
        self._check_target(req.host)
        path, name = self._bench_source(req)
        size = path.stat().st_size
        if total_chunks(size, req.chunk_size) > MAX_CHUNKS:
            raise ApiError(422, "too_many_chunks", "choose a larger chunk size")
        with self._lock:
            if any(b["status"] == "running" for b in self.bench.values()):
                raise ApiError(409, "benchmark_running", "a benchmark is already running")
            bid = uuid.uuid4().hex[:10]
            b = {"id": bid, "status": "running", "created_at": time.time(), "file_name": name, "file_size": size,
                 "chunk_size": req.chunk_size, "host": req.host, "port": req.port, "connections": req.connections,
                 "repeats": req.repeats, "runs": [], "summary": [], "current_job_id": None, "error": None,
                 "cancel": False}
            self.bench[bid] = b
        threading.Thread(target=self._bench_thread, args=(b, path, req), daemon=True, name=f"mpft-bench-{bid}").start()
        return b

    def _cleanup_received(self, result: Dict[str, Any], req: StartBenchmark) -> None:
        """Benchmark copies are throw-away; delete them only when we own the receiver directory."""
        e = self.embedded
        if e and result.get("saved_as") and is_loopback(req.host) and req.port == self.default_port:
            (e.received_dir / Path(result["saved_as"]).name).unlink(missing_ok=True)

    def _bench_thread(self, b: Dict[str, Any], path: Path, req: StartBenchmark) -> None:
        try:
            for rep in range(req.repeats):
                for n in req.connections:
                    if b["cancel"]:
                        raise RuntimeError("cancelled")
                    run_name = f"bench-{b['id']}-{n}c-r{rep + 1}-{b['file_name']}"
                    job = TransferJob(path, ClientConfig(host=req.host, port=req.port, connections=n,
                                                         chunk_size=req.chunk_size),
                                      display_name=run_name, restart=True)
                    with self._lock:
                        self.jobs[job.job_id] = job
                        self.params[job.job_id] = {"host": req.host, "port": req.port, "connections": n,
                                                   "chunk_size": req.chunk_size, "kind": "benchmark",
                                                   "file_name": run_name}
                        b["current_job_id"] = job.job_id
                    res = self._run_and_record(job, "benchmark")
                    self._cleanup_received(res, req)
                    b["runs"].append({
                        "job_id": job.job_id, "connections": n, "connections_used": res["connections_used"],
                        "repeat": rep + 1, "status": res["status"], "elapsed": res["elapsed"],
                        "avg_mb_s": res["avg_mb_s"], "peak_mb_s": res["peak_mb_s"], "bytes_acked": res["bytes_acked"],
                        "retries": res["retries"], "completed_chunks": res["validated_chunks"],
                        "expected_chunks": res["expected_chunks"], "integrity": res["checksum_match"]})
                    if res["status"] != "completed":
                        raise RuntimeError(f"run with {n} connection(s) ended {res['status']}: {res['error']}")
            b["status"] = "completed"
        except RuntimeError as exc:
            b["status"] = "cancelled" if b["cancel"] else "failed"
            b["error"] = str(exc)
        except Exception as exc:  # pragma: no cover
            log.exception("benchmark crashed")
            b["status"], b["error"] = "failed", str(exc)
        finally:
            b["current_job_id"] = None
            b["summary"] = self._summarize_bench(b)
            with self._lock, open(self.bench_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: v for k, v in b.items() if k != "cancel"}) + "\n")

    @staticmethod
    def _summarize_bench(b: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        for n in b["connections"]:
            runs = [r for r in b["runs"] if r["connections"] == n and r["status"] == "completed"]
            if not runs:
                continue
            mean = lambda k: statistics.fmean(r[k] for r in runs)
            rows.append({"connections": n, "runs": len(runs), "elapsed": mean("elapsed"), "avg_mb_s": mean("avg_mb_s"),
                         "peak_mb_s": max(r["peak_mb_s"] for r in runs), "bytes_acked": runs[0]["bytes_acked"],
                         "retries": sum(r["retries"] for r in runs), "completed_chunks": runs[0]["completed_chunks"],
                         "expected_chunks": runs[0]["expected_chunks"], "integrity": all(r["integrity"] for r in runs)})
        base = next((r for r in rows if r["connections"] == 1), None)
        for r in rows:
            r["speedup_vs_1"] = (base["elapsed"] / r["elapsed"]) if base and r["elapsed"] > 0 else None
        return rows

    def bench_public(self, b: Dict[str, Any]) -> Dict[str, Any]:
        if b["status"] == "running":
            b["summary"] = self._summarize_bench(b)
        return {k: v for k, v in b.items() if k != "cancel"}

    def get_bench(self, bid: str) -> Dict[str, Any]:
        b = self.bench.get(bid)
        if b is not None:
            return self.bench_public(b)
        for r in self._read_jsonl(self.bench_path):
            if r["id"] == bid:
                return r
        raise ApiError(404, "benchmark_not_found", "no such benchmark")

    def list_benchmarks(self) -> List[Dict[str, Any]]:
        persisted = {r["id"]: r for r in self._read_jsonl(self.bench_path)}
        for bid, b in self.bench.items():
            persisted[bid] = self.bench_public(b)
        return sorted(persisted.values(), key=lambda r: r["created_at"], reverse=True)

    def cancel_benchmark(self, bid: str) -> None:
        b = self.bench.get(bid)
        if b is None or b["status"] != "running":
            raise ApiError(409, "not_running", "benchmark is not running")
        b["cancel"] = True
        jid = b.get("current_job_id")
        if jid and jid in self.jobs:
            self.jobs[jid].cancel()
