"""
seed_neptune_data.py
Populates AWS Neptune with the same synthetic enterprise knowledge graph used
by benchmarks/benchmark_neptune.py, but WITHOUT the cleanup step at the end —
so the graph stays populated for the live agent demo's Neptune relay
(notebook/neptune_relay_worker.py).

benchmark_neptune.py's main() always DROPs the graph once its benchmark
queries finish, since repeatable benchmarking requires starting from an empty
graph each run. This script duplicates its generate_ntriples()/ingest() logic
(self-contained on purpose — Jupyter only has whatever file you upload, not
the rest of the repo) and stops right after ingestion.

Must be run from inside the Neptune VPC — upload to a Neptune Notebook
(AWS Console > Neptune > Notebooks) and run it there, same as
benchmark_neptune.py.

Usage:
  python3 seed_neptune_data.py            # refuses to run if the graph is non-empty
  python3 seed_neptune_data.py --force     # drops existing data first, then re-seeds
"""

import argparse
import random
import time

import requests

# Update this to your cluster's writer endpoint (same as benchmark_neptune.py)
NEPTUNE_ENDPOINT = "https://kg-bench.cluster-c6vu88s8s7lr.us-east-1.neptune.amazonaws.com:8182"

TRIPLE_COUNT = 100_000
BASE_URI     = "http://enterprise.poc/entity/"
GRAPH        = "http://enterprise.poc/graph"


def generate_ntriples(n_triples):
    """Identical dataset shape to benchmark_neptune.py's generate_ntriples()."""
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
            ntrips.append(f"<{BASE_URI}{s}> <{BASE_URI}{pred}> <{BASE_URI}{o}> .")

    return ntrips[:n_triples]


def existing_triple_count():
    r = requests.get(
        f"{NEPTUNE_ENDPOINT}/sparql",
        params={"query": f"SELECT (COUNT(*) AS ?c) FROM <{GRAPH}> WHERE {{ ?s ?p ?o . }}"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=30,
    )
    r.raise_for_status()
    bindings = r.json().get("results", {}).get("bindings", [])
    return int(bindings[0]["c"]["value"]) if bindings else 0


def wipe():
    print("[Seed] Dropping existing graph...")
    r = requests.post(f"{NEPTUNE_ENDPOINT}/sparql", data={"update": f"DROP GRAPH <{GRAPH}>"})
    r.raise_for_status()


def ingest(ntriples):
    print(f"[Seed] Ingesting {len(ntriples):,} triples via SPARQL UPDATE...")
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
    print(f"\n[Seed] Done in {elapsed:.2f}s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true",
        help="Drop any existing graph data before seeding, instead of refusing to run.",
    )
    args = parser.parse_args()

    if "<your-cluster>" in NEPTUNE_ENDPOINT:
        raise SystemExit("Set NEPTUNE_ENDPOINT at the top of this file first.")

    print(f"[Seed] Checking {NEPTUNE_ENDPOINT}...")
    count = existing_triple_count()
    if count > 0:
        if not args.force:
            raise SystemExit(
                f"Graph already has {count:,} triples. Re-ingesting without cleanup would "
                "create duplicates. Pass --force to drop and re-seed."
            )
        wipe()

    ntriples = generate_ntriples(TRIPLE_COUNT)
    ingest(ntriples)
    print(f"[Seed] Done. Graph is now populated with {TRIPLE_COUNT:,} triples "
          f"and ready for the demo relay (neptune_relay_worker.py).")


if __name__ == "__main__":
    main()
