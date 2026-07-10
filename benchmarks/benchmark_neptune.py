"""
benchmark_neptune.py
Benchmarks AWS Neptune on a 100K-triple enterprise knowledge graph workload.
Uses SPARQL 1.1 over HTTP — no IAM auth required (must be disabled on cluster).

Must be run from inside the Neptune VPC. The easiest way is a Neptune Notebook
(AWS Console > Neptune > Notebooks) — upload this file and run it there.

Output: results/neptune_results.json
"""

import time
import json
import os
import random
from datetime import datetime

try:
    import requests
except ImportError:
    raise SystemExit("Missing dependency: pip install requests")

# Update this to your cluster's writer endpoint
NEPTUNE_ENDPOINT = "https://kg-bench.cluster-c6vu88s8s7lr.us-east-1.neptune.amazonaws.com:8182"

TRIPLE_COUNT = 100_000
QUERY_RUNS   = 20
RESULTS_DIR  = "results"
BASE_URI     = "http://enterprise.poc/entity/"
GRAPH        = "http://enterprise.poc/graph"

# Entities used to vary query parameters so results aren't skewed by one
# entity's particular position/degree in the graph.
LOOKUP_ENTITIES  = ["Company_10", "Company_42", "Company_100", "Company_200", "Company_350"]
KEYWORD_PREFIXES = ["Employee_1", "Employee_5", "Employee_10", "Employee_20", "Employee_50"]
WARMUP_RUNS      = 1


def percentile(values, pct):
    """Linear-interpolation percentile (matches numpy's default 'linear' method)."""
    values = sorted(values)
    k = (len(values) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def generate_ntriples(n_triples):
    """
    Same entity/relationship structure as the Neo4j benchmark —
    companies, employees, products, locations — formatted as N-Triples
    for SPARQL ingestion.
    """
    random.seed(42)
    companies  = [f"Company_{i}"  for i in range(500)]
    employees  = [f"Employee_{i}" for i in range(2000)]
    products   = [f"Product_{i}"  for i in range(1000)]
    locations  = [f"Location_{i}" for i in range(200)]
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
            ntrips.append(
                f"<{BASE_URI}{s}> <{BASE_URI}{pred}> <{BASE_URI}{o}> ."
            )

    return ntrips[:n_triples]


def ingest(ntriples):
    print(f"[Neptune] Ingesting {len(ntriples):,} triples via SPARQL UPDATE...")
    url        = f"{NEPTUNE_ENDPOINT}/sparql"
    batch_size = 500
    batches    = [ntriples[i:i+batch_size] for i in range(0, len(ntriples), batch_size)]
    t_start    = time.perf_counter()

    for idx, batch in enumerate(batches):
        query = f"""
            INSERT DATA {{
                GRAPH <{GRAPH}> {{
                    {chr(10).join(batch)}
                }}
            }}
        """
        r = requests.post(url, data={"update": query})
        r.raise_for_status()

        if (idx + 1) % 20 == 0:
            print(f"  {((idx+1)/len(batches)*100):.0f}%", end="\r")

    elapsed = time.perf_counter() - t_start
    print(f"\n[Neptune] Done in {elapsed:.2f}s")
    return {
        "total_triples":      len(ntriples),
        "elapsed_seconds":    round(elapsed, 3),
        "triples_per_second": round(len(ntriples) / elapsed, 1),
        "method":             "SPARQL UPDATE in batches of 500",
        "note":               "Neptune Bulk Loader from S3 is significantly faster for large datasets"
    }


QUERIES = {
    "Q1_simple_lookup": {
        "description": "Fetch a single entity by URI",
        "sparql_template": lambda entity: f"""
            SELECT ?s ?p ?o FROM <{GRAPH}>
            WHERE {{
                BIND(<{BASE_URI}{entity}> AS ?s)
                ?s ?p ?o .
            }} LIMIT 1
        """,
        "param_values": LOOKUP_ENTITIES,
    },
    "Q2_one_hop": {
        "description": "1-hop: all direct relationships of an entity",
        "sparql_template": lambda entity: f"""
            SELECT ?p ?o FROM <{GRAPH}>
            WHERE {{ <{BASE_URI}{entity}> ?p ?o . }}
            LIMIT 50
        """,
        "param_values": LOOKUP_ENTITIES,
    },
    "Q3_two_hop": {
        "description": "2-hop: entity + connections of connections",
        "sparql_template": lambda entity: f"""
            SELECT ?mid ?p1 ?p2 ?end FROM <{GRAPH}>
            WHERE {{
                <{BASE_URI}{entity}> ?p1 ?mid .
                ?mid ?p2 ?end .
            }} LIMIT 100
        """,
        "param_values": LOOKUP_ENTITIES,
    },
    "Q4_keyword_search": {
        "description": "Filter entities by URI substring",
        "sparql_template": lambda prefix: f"""
            SELECT DISTINCT ?s FROM <{GRAPH}>
            WHERE {{
                ?s ?p ?o .
                FILTER(CONTAINS(STR(?s), "{prefix}"))
            }} LIMIT 50
        """,
        "param_values": KEYWORD_PREFIXES,
    },
    "Q5_aggregation": {
        "description": "Count relationships per predicate",
        "sparql_template": lambda _: f"""
            SELECT ?p (COUNT(*) AS ?cnt) FROM <{GRAPH}>
            WHERE {{ ?s ?p ?o . }}
            GROUP BY ?p ORDER BY DESC(?cnt) LIMIT 20
        """,
        "param_values": [None],  # whole-graph aggregation, no entity variation
    },
}


def run_queries(n_runs):
    url     = f"{NEPTUNE_ENDPOINT}/sparql"
    results = {}

    for qid, q in QUERIES.items():
        param_values = q["param_values"]
        n_entities   = len(param_values)
        print(f"[Neptune] {qid} ({n_runs} runs x {n_entities} entities)...")

        # Warmup: run each entity once, untimed, to prime the connection
        # before measurements start.
        for val in param_values:
            sparql = q["sparql_template"](val)
            r = requests.get(url, params={"query": sparql},
                              headers={"Accept": "application/sparql-results+json"})
            r.raise_for_status()

        latencies = []
        for val in param_values:
            sparql = q["sparql_template"](val)
            for _ in range(n_runs):
                t0 = time.perf_counter()
                r  = requests.get(url, params={"query": sparql},
                                  headers={"Accept": "application/sparql-results+json"})
                r.raise_for_status()
                latencies.append((time.perf_counter() - t0) * 1000)

        results[qid] = {
            "description": q["description"],
            "avg_ms": round(sum(latencies) / len(latencies), 2),
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
            "p95_ms": round(percentile(latencies, 95), 2),
        }
        print(f"  avg {results[qid]['avg_ms']} ms  p95 {results[qid]['p95_ms']} ms")

    return results


def cleanup():
    print("[Neptune] Dropping test graph...")
    r = requests.post(f"{NEPTUNE_ENDPOINT}/sparql",
                      data={"update": f"DROP GRAPH <{GRAPH}>"})
    r.raise_for_status()
    print("[Neptune] Done.")


def estimate_cost():
    return {
        "free_tier_eligible":                    False,
        "free_trial":                            "30-day trial on new AWS accounts (~$200 credits)",
        "serverless_price_per_ncu_hr":           0.10,
        "estimated_monthly_serverless_1ncu_usd": 72,
        "provisioned_r6g_large_per_hr":          0.348,
        "estimated_monthly_provisioned_usd":     252,
        "storage_per_gb_month_usd":              0.10,
        "io_per_million_requests_usd":           0.20,
        "note": "No free tier. Serverless is the right choice for variable workloads "
                "like this POC. Reserved pricing becomes cost-effective at high scale."
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 60)
    print("  AWS Neptune — Knowledge Graph Benchmark")
    print("=" * 60)

    try:
        r = requests.get(f"{NEPTUNE_ENDPOINT}/status", timeout=10)
        r.raise_for_status()
        print(f"[Neptune] Connected.\n")
    except Exception as e:
        print(f"[Neptune] Connection failed: {e}")
        print("  Make sure you're running from inside the Neptune VPC.")
        raise SystemExit(1)

    ntriples        = generate_ntriples(TRIPLE_COUNT)
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
        "methodology": {
            "query_runs":               QUERY_RUNS,
            "entities_per_query":       len(LOOKUP_ENTITIES),
            "warmup_runs":              WARMUP_RUNS,
            "total_samples_per_query":  QUERY_RUNS * len(LOOKUP_ENTITIES),
            "note": "Q1-Q4 vary parameters across 5 entities (100 samples/query). "
                    "Q5 is a whole-graph aggregation with no entity variation "
                    "(20 samples/query).",
        },
        "cost":      cost_info,
        "dev_notes": [
            "Supports both SPARQL 1.1 and Gremlin — good flexibility for mixed workloads",
            "No free tier and VPC-only access make initial setup the most friction of the two tools",
            "Serverless mode works well for variable query loads but adds cold-start latency",
            "Bulk Loader from S3 is the right path for production-scale ingestion",
            "Best fit for teams already running workloads on AWS",
        ]
    }

    out_path = os.path.join(RESULTS_DIR, "neptune_results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n[Neptune] Results written to {out_path}")


if __name__ == "__main__":
    main()