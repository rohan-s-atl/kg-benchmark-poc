"""
main.py
FastAPI backend for the live agent demo. A user types a natural-language
question; the backend asks Claude to translate it into Cypher (Neo4j) and
SPARQL (Neptune), runs both queries in parallel against the live databases,
then streams a conversational answer grounded in both result sets back to
the UI alongside the queries and their latencies.

Run from repo root: uvicorn app.main:app --reload
Requires: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEPTUNE_ENDPOINT, ANTHROPIC_API_KEY in .env
"""

from dotenv import load_dotenv
load_dotenv()

import asyncio
import importlib.util
import json
import os

import anthropic
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import neptune_relay
from app.agent import generate_queries, stream_answer
from app.benchmark_replay import build_replay_script
from app.graph_clients import Neo4jClient, NeptuneClient

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEPTUNE_ENDPOINT = os.getenv("NEPTUNE_ENDPOINT")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# When the Neptune cluster isn't reachable directly (e.g. VPC-only access),
# set NEPTUNE_RELAY=true and run notebook/neptune_relay_worker.py inside the
# VPC — it polls this app for pending SPARQL jobs and posts results back.
NEPTUNE_RELAY_ENABLED = os.getenv("NEPTUNE_RELAY", "false").strip().lower() == "true"
NEPTUNE_RELAY_TOKEN = os.getenv("NEPTUNE_RELAY_TOKEN")
NEPTUNE_RELAY_TIMEOUT_S = float(os.getenv("NEPTUNE_RELAY_TIMEOUT_S", "45"))

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _load_generate_report():
    """Loads generate_report.py from the repo root as a module (same pattern run_all.py uses)."""
    path = os.path.join(REPO_ROOT, "generate_report.py")
    spec = importlib.util.spec_from_file_location("generate_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_report_module = _load_generate_report()

app = FastAPI(title="Knowledge Graph Agent Demo")

anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
neo4j_client = Neo4jClient(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD) if NEO4J_URI and NEO4J_PASSWORD else None
neptune_client = NeptuneClient(NEPTUNE_ENDPOINT) if NEPTUNE_ENDPOINT else None


class AskRequest(BaseModel):
    question: str


def _run_neo4j(cypher: str):
    if neo4j_client is None:
        return {"error": "Neo4j is not configured (missing NEO4J_URI/NEO4J_PASSWORD)."}, 0.0
    try:
        rows, ms = neo4j_client.run(cypher)
        return {"rows": rows}, ms
    except Exception as e:
        return {"error": str(e)}, 0.0


def _run_neptune(sparql: str):
    if neptune_client is None:
        return {"error": "Neptune is not configured (missing NEPTUNE_ENDPOINT)."}, 0.0
    try:
        rows, ms = neptune_client.run(sparql)
        return {"rows": rows}, ms
    except Exception as e:
        return {"error": str(e)}, 0.0


def _check_relay_token(x_relay_token: str | None):
    if NEPTUNE_RELAY_TOKEN and x_relay_token != NEPTUNE_RELAY_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Relay-Token")


@app.get("/api/neptune-pending")
async def neptune_pending(x_relay_token: str | None = Header(default=None)):
    """Polled by notebook/neptune_relay_worker.py running inside the Neptune VPC."""
    _check_relay_token(x_relay_token)
    return {"job": neptune_relay.claim_next()}


class NeptuneResultRequest(BaseModel):
    id: str
    rows: list | None = None
    error: str | None = None
    latency_ms: float | None = None


@app.post("/api/neptune-result")
async def neptune_result(req: NeptuneResultRequest, x_relay_token: str | None = Header(default=None)):
    """Posted by notebook/neptune_relay_worker.py once it has run the query."""
    _check_relay_token(x_relay_token)
    neptune_relay.submit_result(req.id, rows=req.rows, error=req.error, latency_ms=req.latency_ms)
    return {"status": "ok"}


@app.post("/api/ask")
async def ask(req: AskRequest):
    async def event_stream():
        try:
            queries = await generate_queries(anthropic_client, req.question)
        except Exception as e:
            yield json.dumps({"type": "error", "message": f"Query generation failed: {e}"}) + "\n"
            return

        cypher, sparql = queries["cypher"], queries["sparql"]
        yield json.dumps({"type": "queries", "cypher": cypher, "sparql": sparql}) + "\n"

        neo4j_task = asyncio.create_task(asyncio.to_thread(_run_neo4j, cypher))

        if NEPTUNE_RELAY_ENABLED:
            job_id = neptune_relay.enqueue(sparql, req.question)
            neptune_result, neptune_ms = None, 0.0
            async for event in neptune_relay.wait_for_result(job_id, NEPTUNE_RELAY_TIMEOUT_S):
                if event["type"] == "waiting":
                    yield json.dumps({"type": "waiting_neptune", "elapsed_s": event["elapsed_s"]}) + "\n"
                else:
                    neptune_ms = event["elapsed_ms"]
                    neptune_result = {"error": event["error"]} if event.get("error") else {"rows": event.get("rows") or []}
            neo4j_result, neo4j_ms = await neo4j_task
        else:
            (neo4j_result, neo4j_ms), (neptune_result, neptune_ms) = await asyncio.gather(
                neo4j_task,
                asyncio.to_thread(_run_neptune, sparql),
            )

        yield json.dumps({
            "type": "latency",
            "neo4j_ms": round(neo4j_ms, 2),
            "neptune_ms": round(neptune_ms, 2),
            "neo4j_error": neo4j_result.get("error"),
            "neptune_error": neptune_result.get("error"),
        }) + "\n"

        try:
            async for chunk in stream_answer(anthropic_client, req.question, neo4j_result, neptune_result):
                yield json.dumps({"type": "token", "text": chunk}) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": f"Answer generation failed: {e}"}) + "\n"
            return

        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.post("/api/benchmark-replay")
async def benchmark_replay():
    """
    Replays the actual captured benchmark console output (results/*.json) as
    timed streaming lines — for the "Run Benchmark" button. Does NOT re-run
    the real benchmark scripts, since those wipe the graph when they finish,
    which would break the live chat demo.
    """
    async def event_stream():
        results = _report_module.load_results()
        script = build_replay_script(results)
        for delay, tool_key, text in script:
            await asyncio.sleep(delay)
            yield json.dumps({"type": "line", "tool": tool_key, "text": text}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.get("/report")
async def report():
    results = _report_module.load_results()
    if not results:
        return HTMLResponse(
            "<body style='background:#0f1117;color:#e8eaf0;font-family:sans-serif;padding:40px'>"
            "<h2>No benchmark results found</h2>"
            "<p>Run <code>python3 run_all.py</code> (or "
            "<code>python3 run_all.py --skip-neptune</code>) from the repo root first.</p>"
            "</body>",
            status_code=404,
        )
    html = _report_module.generate_html(results)
    return HTMLResponse(html)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
