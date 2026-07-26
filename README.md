# kg-benchmark-poc

Knowledge graph benchmark and live agent demo comparing **Neo4j AuraDB** and
**AWS Neptune** as context layers for enterprise AI agents.

The project has two related workflows:

- A repeatable benchmark that ingests a 100K-triple synthetic enterprise graph,
  measures ingestion/query latency, writes JSON results, and generates an HTML
  report.
- A FastAPI demo app where Claude turns a natural-language question into both
  Cypher and SPARQL, runs the queries against Neo4j and Neptune, compares
  latency, and streams an answer grounded in the returned graph rows.

## Current Captured Results

The checked-in screenshots and `results/*.json` are from the captured benchmark
run on June 22, 2026.

| Metric | Neo4j AuraDB | AWS Neptune |
| --- | ---: | ---: |
| Ingestion time | **9.502 s** | 15.010 s |
| Ingestion throughput | **10,523.6 triples/s** | 6,662.0 triples/s |
| Q1 simple lookup avg | 38.43 ms | **17.19 ms** |
| Q2 one-hop traversal avg | 52.65 ms | **17.23 ms** |
| Q3 two-hop traversal avg | 45.89 ms | **20.90 ms** |
| Q4 keyword search avg | **40.16 ms** | 270.53 ms |
| Q5 aggregation avg | 108.12 ms | **104.92 ms** |
| Small starting estimate | **$65/mo** | $72/mo |
| Free tier for this POC | **Yes** | No |

High-level takeaways from this run:

- Neo4j was faster for ingestion and prefix/keyword-style entity search.
- Neptune was faster for direct URI lookups and one/two-hop SPARQL traversals.
- Neo4j had lower setup friction for a local POC because AuraDB is reachable
  directly and has a free tier.
- Neptune is a strong fit for RDF/SPARQL workloads, but the normal VPC-only
  access pattern adds demo and local-development complexity.

## Screenshots

### Executive Summary

![Executive Summary](screenshots/exec-summary.png)

### Ingestion Performance

![Ingestion Performance](screenshots/ingest-perf.png)

### Query Latency Benchmark

![Query Latency](screenshots/query-latency.png)

### Cost Analysis

![Cost Analysis](screenshots/cost-analysis.png)

## Repository Layout

```text
kg-benchmark-poc/
|-- app/
|   |-- main.py                 # FastAPI app and streaming API endpoints
|   |-- agent.py                # Claude query generation and answer streaming
|   |-- graph_clients.py        # Thin Neo4j and Neptune query clients
|   |-- neptune_relay.py        # In-memory relay queue for VPC-only Neptune
|   |-- benchmark_replay.py     # Timed replay from captured results JSON
|   `-- static/                 # Browser UI for chat, comparison, and report
|-- benchmarks/
|   |-- benchmark_neo4j.py      # Neo4j ingest/query benchmark
|   `-- benchmark_neptune.py    # Neptune ingest/query benchmark
|-- notebook/
|   |-- seed_neptune_data.py    # Neptune seed script for notebook/VPC use
|   `-- neptune_relay_worker.py # Notebook-side relay worker for live demo
|-- generate_report.py          # Builds results/knowledge_graph_benchmark_report.html
|-- run_all.py                  # Benchmark orchestrator
|-- seed_demo_data.py           # Seeds Neo4j for the live demo without cleanup
|-- requirements.txt
|-- screenshots/
`-- results/                    # Generated benchmark JSON/HTML outputs
```

## Prerequisites

- Python 3.10 or newer
- A Neo4j AuraDB instance
- An Anthropic API key for the live demo
- Optional: an AWS Neptune cluster for Neptune benchmarks and live comparison

Install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS/Linux, activate with `source .venv/bin/activate` instead.

## Environment

Create a local `.env` file in the repo root:

```env
NEO4J_URI=neo4j+s://<your-instance>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=<your-password>

ANTHROPIC_API_KEY=<your-anthropic-api-key>

# Only needed when querying Neptune directly from the app.
NEPTUNE_ENDPOINT=https://<your-cluster>.cluster.<region>.neptune.amazonaws.com:8182

# Optional relay mode for VPC-only Neptune access.
NEPTUNE_RELAY=false
NEPTUNE_RELAY_TOKEN=<shared-secret>
NEPTUNE_RELAY_TIMEOUT_S=45
```

`.env` is ignored by git. Do not commit real database URLs, relay tokens, or API
keys.

## Run the Live Demo App

Seed Neo4j with the benchmark-shaped graph and leave it populated:

```bash
python seed_demo_data.py
```

If the graph already contains demo data and you want to replace it:

```bash
python seed_demo_data.py --force
```

Start the app:

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

The UI has two tabs:

- **Live Demo**: ask a natural-language question. The backend generates Cypher
  and SPARQL, runs both graph queries, displays latency, and streams the final
  Claude answer.
- **Benchmark Report**: embeds the generated HTML report and includes a
  "Run Benchmark" replay button. The replay uses the captured `results/*.json`
  numbers; it does not re-run destructive benchmark scripts.

## Neptune Relay Mode

AWS Neptune is usually reachable only from inside its VPC. For a local live demo,
the repo includes a polling relay:

1. Expose the local FastAPI app with a tunnel such as `ngrok http 8000`.
2. Set `NEPTUNE_RELAY=true` and `NEPTUNE_RELAY_TOKEN=<shared-secret>` in local
   `.env`, then restart `uvicorn`.
3. Edit `notebook/neptune_relay_worker.py` inside a Neptune Notebook or other
   VPC-connected environment:
   - Set `LOCAL_APP_URL` to the public tunnel URL.
   - Set `NEPTUNE_ENDPOINT` to the Neptune writer endpoint.
   - Set `RELAY_TOKEN` to the same value as `NEPTUNE_RELAY_TOKEN`.
4. Run the worker inside the VPC:

```bash
python3 neptune_relay_worker.py
```

When the app receives a question, it queues the generated SPARQL query. The
worker polls `/api/neptune-pending`, runs SPARQL inside the VPC, and posts rows
back to `/api/neptune-result`.

To keep Neptune populated for the live relay demo, upload and run:

```bash
python3 seed_neptune_data.py
```

Use `python3 seed_neptune_data.py --force` to drop and re-seed the named graph.

## Run Benchmarks

Run Neo4j only from your local machine:

```bash
python run_all.py --skip-neptune
```

Run both benchmarks:

```bash
python run_all.py
```

Run Neptune only:

```bash
python run_all.py --skip-neo4j
```

Important benchmark behavior:

- `benchmarks/benchmark_neo4j.py` ingests data, measures queries, writes
  `results/neo4j_results.json`, and then deletes the benchmark graph.
- `benchmarks/benchmark_neptune.py` ingests data, measures queries, writes
  `results/neptune_results.json`, and then drops the named graph.
- Use `seed_demo_data.py` and `notebook/seed_neptune_data.py` when you want data
  to remain available for the live app.

Neptune benchmark notes:

- Run Neptune code from inside the Neptune VPC, typically a Neptune Notebook.
- The Neptune scripts use SPARQL over HTTP and assume IAM authentication is not
  required for the endpoint they call.
- `benchmarks/benchmark_neptune.py` and `notebook/seed_neptune_data.py` contain
  a `NEPTUNE_ENDPOINT` constant that must be changed for your cluster before
  running in a notebook.

## Generate the Report

If result JSON files already exist:

```bash
python generate_report.py
```

The report is written to:

```text
results/knowledge_graph_benchmark_report.html
```

When the FastAPI app is running, the same report is also available at:

```text
http://127.0.0.1:8000/report
```

## Benchmark Methodology

Both databases use the same synthetic enterprise graph shape:

- 500 companies
- 2,000 employees
- 1,000 products
- 200 locations
- 8 industries
- 100,000 generated relationships/triples

Relationship types:

```text
employs, produces, located_in, partners_with, operates_in,
reports_to, works_on, supplied_by, sold_in
```

The five query workloads model common context-retrieval patterns for AI agents:

| Query | Purpose |
| --- | --- |
| Q1 simple lookup | Fetch one entity by name/URI |
| Q2 one-hop traversal | Retrieve direct relationships for an entity |
| Q3 two-hop traversal | Expand from an entity to neighbors-of-neighbors |
| Q4 keyword search | Find entities by name/prefix |
| Q5 aggregation | Summarize relationship counts by type |

For Q1-Q4, each query is run across five parameter values with warmup runs before
timing. Q5 is a whole-graph aggregation.

## Data Models

Neo4j stores the graph as a property graph:

```cypher
(:Entity {name: "Company_42"})-[:RELATION {type: "employs"}]->(:Entity {name: "Employee_7"})
```

Neptune stores the graph as RDF triples in a named graph:

```text
<http://enterprise.poc/entity/Company_42>
<http://enterprise.poc/entity/employs>
<http://enterprise.poc/entity/Employee_7>
```

Named graph:

```text
http://enterprise.poc/graph
```

## API Endpoints

| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/ask` | POST | Streams generated queries, query latency, and answer tokens as NDJSON |
| `/api/benchmark-replay` | POST | Streams a timed replay from saved benchmark results |
| `/api/neptune-pending` | GET | Relay worker polls for queued SPARQL jobs |
| `/api/neptune-result` | POST | Relay worker submits Neptune rows/errors |
| `/report` | GET | Renders the generated benchmark report HTML |
| `/` | GET | Serves the static demo UI |

## Tech Stack

- FastAPI and Uvicorn for the local web app
- Anthropic Python SDK for query generation and answer streaming
- Neo4j Python driver for AuraDB
- Requests / SPARQL HTTP for Neptune
- Chart.js in a generated self-contained HTML report
