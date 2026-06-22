"""
benchmark_neptune.py
POC: Knowledge Graph Cost & Performance Benchmark
Tool: AWS Neptune (SPARQL endpoint) — no IAM auth
Workload: 100K-triple enterprise org/company dataset (Wikidata-style)
Output: results/neptune_results.json

Prerequisites:
  - Run from a Neptune Notebook (inside the same VPC)
  - IAM authentication must be OFF on the Neptune cluster
  - pip install SPARQLWrapper requests
"""

import time
import json
import os
import random
from datetime import datetime

try:
    import requests
    from SPARQLWrapper import SPARQLWrapper, JSON as SPARQL_JSON, POST, GET
except ImportError:
    raise SystemExit("Install dependencies: pip install SPARQLWrapper requests")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
NEPTUNE_ENDPOINT = "https://kg-bench.cluster-c6vu88s8s7lr.us-east-1.neptune.amazonaws.com:8182"
NEPTUNE_REGION   = "us-east-1"

TRIPLE_COUNT  = 100_000
QUERY_RUNS    = 5
RESULTS_DIR   = "results"
BASE_URI      = "http://enterprise.poc/entity/"
GRAPH         = "http://enterprise.poc/graph"
# ══════════════════════════════════════════════════════════════════════════════


# ── Synthetic enterprise dataset generator ────────────────────────────────────
def generate_ntriples(n_triples: int) -> list:
    random.seed(42)
    companies  = [f"Company_{i}"   for i in range(500)]
    employees  = [f"Employee_{i}"  for i in range(2000)]
    products   = [f"Product_{i}"   for i in range(1000)]
    locations  = [f"Location_{i}"  for i in range(200)]
    industries = ["Finance", "Healthcare", "Technology", "Manufacturing",
                  "Retail", "Energy", "Logistics", "Media"]

    predicates = {
        "employs":       (companies, employees),
        "produces":      (companies, products),
        "located_in":    (companies, locations),
        "partners_with": (companies, companies),
        "operates_in":   (companies, [f"Industry_{x}" for x in industries]),
        "reports_to":    (employees, employees),
        "works_on":      (employees, products),
        "supplied_by":   (products,  companies),
        "sold_in":       (products,  locations),
    }

    pred_list = list(predicates.keys())
    seen      = set()
    ntrips    = []

    while len(ntrips) < n_triples:
        pred = random.choice(pred_list)
        subj_pool, obj_pool = predicates[pred]
        s = random.choice(subj_pool)
        o = random.choice(obj_pool)
        key = (s, pred, o)
        if s != o and key not in seen:
            seen.add(key)
            s_uri = f"<{BASE_URI}{s.replace(' ', '_')}>"
            p_uri = f"<{BASE_URI}{pred}>"
            o_uri = f"<{BASE_URI}{o.replace(' ', '_')}>"
            ntrips.append(f"{s_uri} {p_uri} {o_uri} .")

    return ntrips[:n_triples]


# ── Ingestion ─────────────────────────────────────────────────────────────────
def ingest(ntriples: list) -> dict:
    print(f"[Neptune] Ingesting {len(ntriples):,} triples via SPARQL UPDATE...")
    url        = f"{NEPTUNE_ENDPOINT}/sparql"
    batch_size = 500
    batches    = [ntriples[i:i+batch_size] for i in range(0, len(ntriples), batch_size)]

    t_start = time.perf_counter()
    for idx, batch in enumerate(batches):
        triples_block = "\n".join(batch)
        update_query  = f"""
            INSERT DATA {{
                GRAPH <{GRAPH}> {{
                    {triples_block}
                }}
            }}
        """
        r = requests.post(url, data={"update": update_query})
        r.raise_for_status()

        if (idx + 1) % 20 == 0:
            pct = (idx + 1) / len(batches) * 100
            print(f"  {pct:.0f}% ({(idx+1)*batch_size:,} triples)", end="\r")

    elapsed = time.perf_counter() - t_start
    print(f"\n[Neptune] Ingestion complete in {elapsed:.2f}s")
    return {
        "total_triples":      len(ntriples),
        "elapsed_seconds":    round(elapsed, 3),
        "triples_per_second": round(len(ntriples) / elapsed, 1),
        "method":             "SPARQL UPDATE (batches of 500)",
        "note":               "Production: use Neptune Bulk Loader from S3 for 10x+ speed"
    }


# ── Query benchmark suite ─────────────────────────────────────────────────────
QUERIES = {
    "Q1_simple_lookup": {
        "description": "Fetch a single entity by URI",
        "sparql": f"""
            SELECT ?s ?p ?o
            FROM <{GRAPH}>
            WHERE {{
                BIND(<{BASE_URI}Company_42> AS ?s)
                ?s ?p ?o .
            }} LIMIT 1
        """
    },
    "Q2_one_hop": {
        "description": "1-hop: all direct relationships of an entity",
        "sparql": f"""
            SELECT ?p ?o
            FROM <{GRAPH}>
            WHERE {{
                <{BASE_URI}Company_42> ?p ?o .
            }} LIMIT 50
        """
    },
    "Q3_two_hop": {
        "description": "2-hop: entity + connections of connections",
        "sparql": f"""
            SELECT ?mid ?p1 ?p2 ?end
            FROM <{GRAPH}>
            WHERE {{
                <{BASE_URI}Company_42> ?p1 ?mid .
                ?mid ?p2 ?end .
            }} LIMIT 100
        """
    },
    "Q4_keyword_search": {
        "description": "Filter entities by URI substring (prefix search)",
        "sparql": f"""
            SELECT DISTINCT ?s
            FROM <{GRAPH}>
            WHERE {{
                ?s ?p ?o .
                FILTER(CONTAINS(STR(?s), "Employee_1"))
            }} LIMIT 50
        """
    },
    "Q5_aggregation": {
        "description": "Count relationships per predicate (subgraph summary)",
        "sparql": f"""
            SELECT ?p (COUNT(*) AS ?cnt)
            FROM <{GRAPH}>
            WHERE {{
                ?s ?p ?o .
            }}
            GROUP BY ?p
            ORDER BY DESC(?cnt)
            LIMIT 20
        """
    },
}


def run_queries(n_runs: int) -> dict:
    url     = f"{NEPTUNE_ENDPOINT}/sparql"
    results = {}

    for qid, q in QUERIES.items():
        print(f"[Neptune] Running {qid} ({n_runs}x)...")
        latencies = []
        for _ in range(n_runs):
            t0 = time.perf_counter()
            r  = requests.get(url, params={"query": q["sparql"]},
                              headers={"Accept": "application/sparql-results+json"})
            r.raise_for_status()
            latencies.append((time.perf_counter() - t0) * 1000)

        results[qid] = {
            "description": q["description"],
            "avg_ms": round(sum(latencies) / len(latencies), 2),
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
        }
        print(f"  avg {results[qid]['avg_ms']} ms")

    return results


# ── Cleanup ───────────────────────────────────────────────────────────────────
def cleanup():
    print("[Neptune] Cleaning up test graph...")
    url = f"{NEPTUNE_ENDPOINT}/sparql"
    r   = requests.post(url, data={"update": f"DROP GRAPH <{GRAPH}>"})
    r.raise_for_status()
    print("[Neptune] Done.")


# ── Cost estimation ───────────────────────────────────────────────────────────
def estimate_cost() -> dict:
    return {
        "free_tier_eligible": False,
        "free_trial": "30-day trial on new AWS account (~$200 credits)",
        "serverless_price_per_ncu_hr": 0.10,
        "estimated_monthly_serverless_1ncu_usd": 72,
        "provisioned_r6g_large_per_hr": 0.348,
        "estimated_monthly_provisioned_usd": 252,
        "storage_per_gb_month_usd": 0.10,
        "io_per_million_requests_usd": 0.20,
        "note": "Neptune has no free tier. Serverless is best for variable/POC workloads. "
                "Significant cost advantage only at very high scale with reserved pricing."
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 60)
    print("  AWS Neptune — Enterprise Knowledge Graph Benchmark")
    print("=" * 60)

    try:
        r = requests.get(f"{NEPTUNE_ENDPOINT}/status", timeout=10)
        r.raise_for_status()
        print(f"[Neptune] Connected. Status: {r.json()}\n")
    except Exception as e:
        print(f"[Neptune] Connection failed: {e}")
        raise SystemExit(1)

    ntriples = generate_ntriples(TRIPLE_COUNT)

    ingestion_stats = ingest(ntriples)
    query_stats     = run_queries(QUERY_RUNS)
    cost_info       = estimate_cost()

    cleanup()

    output = {
        "tool":      "AWS Neptune",
        "timestamp": datetime.utcnow().isoformat(),
        "dataset":   {"triples": TRIPLE_COUNT, "source": "Synthetic enterprise (Wikidata-style)"},
        "ingestion": ingestion_stats,
        "queries":   query_stats,
        "cost":      cost_info,
        "dev_notes": [
            "SPARQL 1.1 + Gremlin dual support — flexible for RDF and property graph workloads",
            "No free tier; requires VPC — highest setup friction of the three tools",
            "Neptune Serverless removes capacity planning burden but adds cold-start latency",
            "Bulk Loader from S3 is 10-50x faster than SPARQL UPDATE for large ingestion",
            "Best fit for organizations already deep in the AWS ecosystem",
        ]
    }

    out_path = os.path.join(RESULTS_DIR, "neptune_results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n[Neptune] Results saved to {out_path}")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()