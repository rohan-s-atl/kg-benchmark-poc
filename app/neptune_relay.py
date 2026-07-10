"""
neptune_relay.py
In-memory job queue that bridges Neptune SPARQL queries to a worker script
running inside the Neptune VPC (e.g. a Neptune Notebook), since Neptune is not
reachable directly from outside the VPC. The local app enqueues a job when it
generates a SPARQL query; a notebook-side poller (notebook/neptune_relay_worker.py)
claims it, runs it against the live cluster, and POSTs the result back.
"""

import asyncio
import time
import uuid

pending_queue: list[dict] = []
completed_jobs: dict[str, dict] = {}


def enqueue(sparql: str, question: str) -> str:
    job_id = str(uuid.uuid4())
    pending_queue.append({"id": job_id, "sparql": sparql, "question": question, "created_at": time.time()})
    return job_id


def claim_next():
    if pending_queue:
        return pending_queue.pop(0)
    return None


def submit_result(job_id: str, rows=None, error=None, latency_ms=None):
    completed_jobs[job_id] = {"rows": rows, "error": error, "latency_ms": latency_ms}


async def wait_for_result(job_id: str, timeout_s: float, ping_every_s: float = 3.0):
    """
    Async generator. Yields {"type": "waiting", "elapsed_s": ...} periodically
    while polling, then a final {"type": "result", "rows":..., "error":...,
    "elapsed_ms":...} once the worker posts a result or the timeout is hit.
    """
    t0 = time.perf_counter()
    deadline = time.monotonic() + timeout_s
    last_ping = time.monotonic()

    while time.monotonic() < deadline:
        if job_id in completed_jobs:
            result = completed_jobs.pop(job_id)
            elapsed_ms = result.get("latency_ms") or (time.perf_counter() - t0) * 1000
            yield {
                "type": "result",
                "rows": result.get("rows"),
                "error": result.get("error"),
                "elapsed_ms": elapsed_ms,
            }
            return
        if time.monotonic() - last_ping >= ping_every_s:
            yield {"type": "waiting", "elapsed_s": round(time.perf_counter() - t0, 1)}
            last_ping = time.monotonic()
        await asyncio.sleep(0.5)

    # Timed out — drop the job if no worker ever claimed/answered it.
    pending_queue[:] = [j for j in pending_queue if j["id"] != job_id]
    elapsed_ms = (time.perf_counter() - t0) * 1000
    yield {
        "type": "result",
        "rows": None,
        "error": f"Neptune relay timed out after {timeout_s:.0f}s — is the notebook relay worker running?",
        "elapsed_ms": elapsed_ms,
    }
