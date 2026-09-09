"""Isolated authenticated API mixed workload and abrupt process recovery.

Run from backend: python -m scripts.meeting_soak --seconds 60 --concurrency 2
Add --models for synthetic upload, indexing and RAG calls through configured providers.
No existing application data is opened. Results describe this workload, not an SLA.
"""

import argparse
import concurrent.futures
import json
import math
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def summarize(samples):
    ordered = sorted(sample["seconds"] for sample in samples)
    return {
        "evaluated": len(samples),
        "errors": sum(sample["error"] is not None for sample in samples),
        **{
            f"p{percent}_seconds": ordered[math.ceil(len(ordered) * percent / 100) - 1]
            if ordered
            else None
            for percent in (50, 95, 99)
        },
    }


def run(seconds: float, concurrency: int, models: bool, model_interval: float = 300):
    with tempfile.TemporaryDirectory(prefix="meeting-api-soak-") as directory:
        root = Path(directory)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        base = f"http://127.0.0.1:{port}/api/v1"
        env = {
            **os.environ,
            "MEETING_AGENT_DATA_DIR": directory,
            "DATA_DIR": directory,
            "DB_PATH": str(root / "meetings.db"),
            "UPLOAD_DIR": str(root / "uploads"),
            "VECTOR_DB_DIR": str(root / "vectordb"),
            "LOG_DIR": str(root / "logs"),
            "ENVIRONMENT": "production",
            "API_KEY": "isolated-soak-key",
            "PRINCIPAL_PEPPER": "isolated-soak-pepper-not-a-production-secret",
            "PRINCIPAL_ID": "isolated_soak",
            "TRUSTED_HOSTS": "localhost,127.0.0.1",
            "CORS_ORIGINS": f"http://127.0.0.1:{port}",
            "RAGANYTHING_ENABLED": "false",
            "MEETING_AUTO_SUMMARIZE_FILES": "false",
            "COMBINED_EXTRACTION_ENABLED": "false",
            "SESSION_SUMMARY_ENABLED": "false",
        }
        headers = {"X-API-Key": env["API_KEY"]}
        process = None
        log = (root / "server.log").open("w")

        def start():
            nonlocal process
            started = time.monotonic()
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "src.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                stdout=log,
                stderr=log,
            )
            with httpx.Client(timeout=5, headers=headers) as client:
                while time.monotonic() - started < 180:
                    if process.poll() is not None:
                        raise RuntimeError(
                            "Isolated backend exited during startup: "
                            + (root / "server.log").read_text()[-4000:]
                        )
                    try:
                        if client.get(f"{base}/health/ready").status_code == 200:
                            return time.monotonic() - started
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
            raise TimeoutError("Isolated backend readiness timed out")

        resource_samples = []

        def worker(worker_id):
            samples = []
            with httpx.Client(base_url=base, headers=headers, timeout=120) as client:

                def record(name, action):
                    before, error = time.monotonic(), None
                    try:
                        action()
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {str(exc)[:250]}"
                    samples.append(
                        {"operation": name, "seconds": time.monotonic() - before, "error": error}
                    )

                def upload_and_chat():
                    response = client.post(
                        "/meetings/upload",
                        data={"title": f"Soak synthetic {worker_id}"},
                        files={
                            "file": (
                                "synthetic.txt",
                                (
                                    b"# Delivery\nAlice: Ship Friday.\n"
                                    b"Bob: Friday is impossible because QA needs two days.\n"
                                    b"Alice: Agreed, ship Monday. Bob owns QA."
                                ),
                                "text/plain",
                            )
                        },
                    )
                    response.raise_for_status()
                    meeting_id = response.json()["meeting_id"]
                    wait_started = time.monotonic()
                    while time.monotonic() - wait_started < 120:
                        detail = client.get(f"/meetings/{meeting_id}")
                        detail.raise_for_status()
                        if detail.json().get("status") == "ready":
                            break
                        time.sleep(0.5)
                    else:
                        raise TimeoutError("Synthetic upload did not become ready")
                    chat_started = time.monotonic()
                    chat = client.post(
                        "/chat",
                        json={
                            "question": "Why did the delivery date change?",
                            "meeting_ids": [meeting_id],
                        },
                    )
                    chat.raise_for_status()
                    if not chat.json().get("answer"):
                        raise AssertionError("RAG response has no answer")
                    samples.append(
                        {
                            "operation": "chat",
                            "seconds": time.monotonic() - chat_started,
                            "error": None,
                        }
                    )

                iteration = 0
                next_model = 0.0
                next_progress = 0.0
                while time.monotonic() < deadline:
                    key = f"soak.{worker_id}.{iteration}"

                    def write(key=key):
                        response = client.post(
                            "/memory",
                            json={
                                "key": key,
                                "value": "Prepare QA report",
                                "fact_type": "action_item",
                                "action_status": "open",
                                "assignee": "Alice",
                                "assertion_status": "confirmed" if models else "pending",
                            },
                        )
                        response.raise_for_status()

                    record("memory_write", write)

                    def query():
                        response = client.post(
                            "/memory/facts/query",
                            json={"fact_types": ["action_item"], "assignee": "Alice"},
                        )
                        response.raise_for_status()
                        if response.json()["extraction_complete"]:
                            raise AssertionError(
                                "Recorded set must not certify extraction completeness"
                            )

                    record("facts_query", query)
                    if models and time.monotonic() >= next_model:
                        record("upload_index_rag", upload_and_chat)
                        next_model = time.monotonic() + model_interval
                    if worker_id == 0 and time.monotonic() >= next_progress:
                        try:
                            rss = subprocess.run(
                                ["ps", "-p", str(process.pid), "-o", "rss="],
                                capture_output=True,
                                text=True,
                                check=True,
                            )
                            rss_kib = int(rss.stdout.strip())
                        except (OSError, subprocess.SubprocessError, ValueError):
                            # OS data protection can also deny process introspection.
                            # Keep the business/recovery checks; never invent RSS.
                            rss_kib = None
                        resource_samples.append(
                            {
                                "elapsed_seconds": time.monotonic() - began,
                                "rss_kib": rss_kib,
                            }
                        )
                        print(
                            json.dumps(
                                {
                                    "phase": "mixed_workload",
                                    "elapsed_seconds": round(time.monotonic() - began),
                                    "worker_iterations": iteration + 1,
                                    "rss_kib": rss_kib,
                                }
                            ),
                            flush=True,
                        )
                        next_progress = time.monotonic() + 60
                    iteration += 1
                    time.sleep(0.1)
            return samples

        try:
            initial_start_seconds = start()
            began = time.monotonic()
            deadline = began + seconds
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                samples = [
                    sample for result in pool.map(worker, range(concurrency)) for sample in result
                ]
            duration = time.monotonic() - began
            process.kill()  # Owned disposable server; test ungraceful SQLite/WAL recovery.
            process.wait(timeout=15)
            recovery_seconds = start()
            with httpx.Client(base_url=base, headers=headers, timeout=30) as client:
                response = client.get("/memory", params={"q": "soak.", "limit": 100})
                response.raise_for_status()
                persisted = response.json()["total"]
                drain_started = time.monotonic()
                while True:
                    jobs = client.get("/health/jobs")
                    jobs.raise_for_status()
                    job_counts = jobs.json()["counts"]
                    settled = not any(
                        job_counts.get(k, 0) for k in ("pending", "running", "expired_running")
                    )
                    if settled or time.monotonic() - drain_started > 360:
                        break
                    time.sleep(2.5)  # Stay below the job health endpoint limit (30/min).
                jobs_recovered = settled and job_counts.get("dead_letter", 0) == 0
            expected = sum(s["operation"] == "memory_write" and s["error"] is None for s in samples)
            if persisted != expected:
                raise AssertionError(f"Lost writes after crash: {persisted}/{expected}")
            process.terminate()
            process.wait(timeout=30)
            with sqlite3.connect(root / "meetings.db") as conn:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                with sqlite3.connect(root / "verified-backup.db") as backup:
                    conn.backup(backup)
                    backup_integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
            return {
                "workload": "synthetic_single_instance_api",
                "concurrency": concurrency,
                "requested_seconds": seconds,
                "workload_seconds": duration,
                "initial_start_seconds": initial_start_seconds,
                "crash_recovery_seconds": recovery_seconds,
                "operations": {
                    name: summarize([s for s in samples if s["operation"] == name])
                    for name in ("memory_write", "facts_query", "upload_index_rag", "chat")
                },
                "models_enabled": models,
                "model_interval_seconds": model_interval,
                "resource_samples": resource_samples,
                "rss_evaluated_samples": sum(
                    sample["rss_kib"] is not None for sample in resource_samples
                ),
                "rss_skipped_samples": sum(
                    sample["rss_kib"] is None for sample in resource_samples
                ),
                "duration_gate_passed": duration >= seconds,
                "chat_p95_target_seconds": 3.0,
                "chat_p95_gate_passed": (
                    summarize([s for s in samples if s["operation"] == "chat"])["p95_seconds"] < 3.0
                )
                if any(s["operation"] == "chat" for s in samples)
                else None,
                "model_quality_score": None,
                "errors": [s for s in samples if s["error"]],
                "persisted_writes": persisted,
                "job_counts_after_recovery": job_counts,
                "jobs_recovered": jobs_recovered,
                "job_drain_seconds": time.monotonic() - drain_started,
                "integrity": integrity,
                "backup_integrity": backup_integrity,
                "release_ready": False,
                "limitations": [
                    "synthetic data",
                    "bounded run; does not establish rolling 30-day SLO",
                    "no human quality labels",
                    "no multi-instance evaluation",
                ],
            }
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--model-interval", type=float, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 86400 or not 1 <= args.concurrency <= 10:
        parser.error("seconds must be 1..86400; concurrency must be 1..10")
    if args.model_interval < 30:
        parser.error("model-interval must be at least 30 seconds")
    result = run(args.seconds, args.concurrency, args.models, args.model_interval)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)
    raise SystemExit(
        1 if result["errors"] or result["integrity"] != "ok" or not result["jobs_recovered"] else 0
    )
