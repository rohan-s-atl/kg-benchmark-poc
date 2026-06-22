"""
benchmark_neo4j.py
POC: Knowledge Graph Cost & Performance Benchmark
Tool: Neo4j AuraDB
Workload: 100K-triple enterprise org/company dataset (Wikidata-style)
Output: results/neo4j_results.json
"""

from dotenv import load_dotenv
load_dotenv()

import time
import json
import os
import random
from datetime import datetime

try:
    from neo4j import GraphDatabase
except ImportError:
    raise SystemExit("Install dependency: pip install neo4j")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════
NEO4J_URI      = os.getenv("NEO4J_URI",      "neo4j+s://<your-aura-instance>.databases.neo4j.io")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "<your-password>")

TRIPLE_COUNT   = 100_000
QUERY_RUNS     = 5
RESULTS_DIR    = "results"
# ══════════════════════════════════════════════════════════════════════════════


def generate_dataset(n_triples: int):
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
        "operates_in":   (companies, [f"Industry_{i}" for i in industries]),
        "reports_to":    (employees, employees),
        "works_on":      (employees, products),
        "supplied_by":   (products,  companies),
        "sold_in":       (products,  locations),
    }

    triples   = []
    pred_list = list(predicates.keys())
    while len(triples) < n_triples:
        pred = random.choice(pred_list)
        subj_pool, obj_pool = predicates[pred]
        s = random.choice(subj_pool)
        o = random.choice(obj_pool)
        if s != o:
            triples.append((s, pred, o))

    return triples[:n_triples]


def ingest(driver, triples: list) -> dict:
    """
    Two-phase ingestion:
    1. Bulk CREATE all unique nodes first (fast, no duplicate checking)
    2. Bulk CREATE all relationships
    This matches Neptune's approach of no duplicate checking during ingestion.
    """
    print(f"[Neo4j] Ingesting {len(triples):,} triples...")

    # Collect unique nodes and edges
    nodes = set()
    edges = []
    for s, p, o in triples:
        nodes.add(s)
        nodes.add(o)
        edges.append({"s": s, "p": p, "o": o})

    node_list  = [{"name": n} for n in nodes]
    batch_size = 2000

    t_start = time.perf_counter()

    with driver.session() as session:
        # Phase 1: create all nodes in bulk
        print(f"  Creating {len(node_list):,} unique nodes...")
        node_batches = [node_list[i:i+batch_size] for i in range(0, len(node_list), batch_size)]
        for batch in node_batches:
            session.run(
                "UNWIND $rows AS row CREATE (:Entity {name: row.name})",
                rows=batch
            )

        # Create index for fast lookup during edge creation
        session.run("CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)")

        # Phase 2: create all relationships in bulk
        print(f"  Creating {len(edges):,} relationships...")
        edge_batches = [edges[i:i+batch_size] for i in range(0, len(edges), batch_size)]
        for idx, batch in enumerate(edge_batches):
            session.run(
                """
                UNWIND $rows AS row
                MATCH (a:Entity {name: row.s})
                MATCH (b:Entity {name: row.o})
                CREATE (a)-[:RELATION {type: row.p}]->(b)
                """,
                rows=batch
            )
            if (idx + 1) % 5 == 0:
                pct = (idx + 1) / len(edge_batches) * 100
                print(f"  {pct:.0f}% relationships", end="\r")

    elapsed = time.perf_counter() - t_start
    print(f"\n[Neo4j] Ingestion complete in {elapsed:.2f}s")
    return {
        "total_triples":      len(triples),
        "elapsed_seconds":    round(elapsed, 3),
        "triples_per_second": round(len(triples) / elapsed, 1),
        "method":             "Bulk CREATE nodes then relationships (no duplicate checking)",
    }


QUERIES = {
    "Q1_simple_lookup": {
        "description": "Fetch a single entity by name",
        "cypher": "MATCH (e:Entity {name: $name}) RETURN e LIMIT 1",
        "params": {"name": "Company_42"},
    },
    "Q2_one_hop": {
        "description": "1-hop: all direct relationships of an entity",
        "cypher": """
            MATCH (a:Entity {name: $name})-[r:RELATION]->(b:Entity)
            RETURN a.name, r.type, b.name LIMIT 50
        """,
        "params": {"name": "Company_42"},
    },
    "Q3_two_hop": {
        "description": "2-hop: entity + connections of connections",
        "cypher": """
            MATCH (a:Entity {name: $name})-[r1:RELATION]->(b:Entity)-[r2:RELATION]->(c:Entity)
            RETURN a.name, r1.type, b.name, r2.type, c.name LIMIT 100
        """,
        "params": {"name": "Company_42"},
    },
    "Q4_keyword_search": {
        "description": "Prefix search across all entity names",
        "cypher": """
            MATCH (e:Entity)
            WHERE e.name STARTS WITH $prefix
            RETURN e.name LIMIT 50
        """,
        "params": {"prefix": "Employee_1"},
    },
    "Q5_aggregation": {
        "description": "Count relationships per type (subgraph summary)",
        "cypher": """
            MATCH (a:Entity)-[r:RELATION]->(b:Entity)
            RETURN r.type, count(*) AS cnt
            ORDER BY cnt DESC LIMIT 20
        """,
        "params": {},
    },
}


def run_queries(driver, n_runs: int) -> dict:
    results = {}
    with driver.session() as session:
        for qid, q in QUERIES.items():
            print(f"[Neo4j] Running {qid} ({n_runs}x)...")
            latencies = []
            for _ in range(n_runs):
                t0 = time.perf_counter()
                list(session.run(q["cypher"], **q["params"]))
                latencies.append((time.perf_counter() - t0) * 1000)

            results[qid] = {
                "description": q["description"],
                "avg_ms": round(sum(latencies) / len(latencies), 2),
                "min_ms": round(min(latencies), 2),
                "max_ms": round(max(latencies), 2),
            }
            print(f"  avg {results[qid]['avg_ms']} ms")
    return results


def cleanup(driver):
    print("[Neo4j] Cleaning up test data...")
    with driver.session() as session:
        # Drop index first
        session.run("DROP INDEX entity_name IF EXISTS")
        # Delete in batches to avoid memory issues
        while True:
            result = session.run(
                "MATCH (n:Entity) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS cnt"
            )
            if result.single()["cnt"] == 0:
                break
    print("[Neo4j] Done.")


def estimate_cost(ingestion: dict, queries: dict) -> dict:
    return {
        "free_tier_eligible": True,
        "free_tier_limits": "200K nodes, 400K relationships",
        "professional_price_per_gib_hr": 0.09,
        "estimated_monthly_1gib_usd": 65,
        "estimated_monthly_4gib_usd": 260,
        "note": "100K triples fits comfortably in AuraDB Free tier. "
                "Production enterprise scale (10M+ triples) requires Professional."
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 60)
    print("  Neo4j AuraDB — Enterprise Knowledge Graph Benchmark")
    print("=" * 60)

    print(f"[Neo4j] Connecting to {NEO4J_URI}...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("[Neo4j] Connected.\n")

    triples = generate_dataset(TRIPLE_COUNT)

    ingestion_stats = ingest(driver, triples)
    query_stats     = run_queries(driver, QUERY_RUNS)
    cost_info       = estimate_cost(ingestion_stats, query_stats)

    cleanup(driver)
    driver.close()

    output = {
        "tool":      "Neo4j AuraDB",
        "timestamp": datetime.utcnow().isoformat(),
        "dataset":   {"triples": TRIPLE_COUNT, "source": "Synthetic enterprise (Wikidata-style)"},
        "ingestion": ingestion_stats,
        "queries":   query_stats,
        "cost":      cost_info,
        "dev_notes": [
            "Cypher query language — easy learning curve",
            "AuraDB Free tier adequate for POC and small enterprise workloads",
            "Native graph storage (index-free adjacency) — fast traversal",
            "No SPARQL support — not ideal for RDF/OWL ontology-heavy workloads",
            "Excellent Python driver with async support",
        ]
    }

    out_path = os.path.join(RESULTS_DIR, "neo4j_results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n[Neo4j] Results saved to {out_path}")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()