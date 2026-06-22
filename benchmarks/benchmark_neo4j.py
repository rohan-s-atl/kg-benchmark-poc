"""
benchmark_neo4j.py
Benchmarks Neo4j AuraDB on a 100K-triple enterprise knowledge graph workload.
Measures ingestion speed and query latency across 5 query types that simulate
what an AI agent would run when retrieving context from a knowledge graph.

Output: results/neo4j_results.json
Run from repo root: python run_all.py --skip-neptune
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
    raise SystemExit("Missing dependency: pip install neo4j")

NEO4J_URI      = os.getenv("NEO4J_URI",      "neo4j+s://<your-instance>.databases.neo4j.io")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "<your-password>")

TRIPLE_COUNT = 100_000
QUERY_RUNS   = 5
RESULTS_DIR  = "results"


def generate_dataset(n_triples):
    """
    Generates a synthetic enterprise knowledge graph as (subject, predicate, object) triples.
    Entities represent companies, employees, products, and locations — modeled after
    the structure of a real enterprise knowledge base.
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


def ingest(driver, triples):
    """
    Two-phase bulk ingestion:
    1. CREATE all unique nodes (no duplicate checking for speed)
    2. CREATE all relationships after building an index

    This approach is equivalent to Neptune's SPARQL UPDATE behavior
    and gives a fair apples-to-apples ingestion comparison.
    """
    print(f"[Neo4j] Ingesting {len(triples):,} triples...")

    nodes = set()
    edges = []
    for s, p, o in triples:
        nodes.add(s)
        nodes.add(o)
        edges.append({"s": s, "p": p, "o": o})

    node_list  = [{"name": n} for n in nodes]
    batch_size = 2000
    t_start    = time.perf_counter()

    with driver.session() as session:
        print(f"  Creating {len(node_list):,} nodes...")
        for batch in [node_list[i:i+batch_size] for i in range(0, len(node_list), batch_size)]:
            session.run("UNWIND $rows AS row CREATE (:Entity {name: row.name})", rows=batch)

        session.run("CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)")

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
                print(f"  {((idx+1)/len(edge_batches)*100):.0f}%", end="\r")

    elapsed = time.perf_counter() - t_start
    print(f"\n[Neo4j] Done in {elapsed:.2f}s")
    return {
        "total_triples":      len(triples),
        "elapsed_seconds":    round(elapsed, 3),
        "triples_per_second": round(len(triples) / elapsed, 1),
        "method":             "Bulk CREATE (two-phase: nodes then relationships)",
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
        "description": "Prefix search across entity names",
        "cypher": """
            MATCH (e:Entity)
            WHERE e.name STARTS WITH $prefix
            RETURN e.name LIMIT 50
        """,
        "params": {"prefix": "Employee_1"},
    },
    "Q5_aggregation": {
        "description": "Count relationships per type",
        "cypher": """
            MATCH (a:Entity)-[r:RELATION]->(b:Entity)
            RETURN r.type, count(*) AS cnt
            ORDER BY cnt DESC LIMIT 20
        """,
        "params": {},
    },
}


def run_queries(driver, n_runs):
    results = {}
    with driver.session() as session:
        for qid, q in QUERIES.items():
            print(f"[Neo4j] {qid} ({n_runs} runs)...")
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
    print("[Neo4j] Cleaning up...")
    with driver.session() as session:
        session.run("DROP INDEX entity_name IF EXISTS")
        while True:
            result = session.run(
                "MATCH (n:Entity) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS cnt"
            )
            if result.single()["cnt"] == 0:
                break
    print("[Neo4j] Done.")


def estimate_cost():
    return {
        "free_tier_eligible":         True,
        "free_tier_limits":           "200K nodes, 400K relationships",
        "professional_price_per_gib_hr": 0.09,
        "estimated_monthly_1gib_usd": 65,
        "estimated_monthly_4gib_usd": 260,
        "note": "100K triples fits in the AuraDB free tier. "
                "Production workloads at 10M+ triples require a paid plan."
    }


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 60)
    print("  Neo4j AuraDB — Knowledge Graph Benchmark")
    print("=" * 60)

    print(f"[Neo4j] Connecting to {NEO4J_URI}...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("[Neo4j] Connected.\n")

    triples         = generate_dataset(TRIPLE_COUNT)
    ingestion_stats = ingest(driver, triples)
    query_stats     = run_queries(driver, QUERY_RUNS)
    cost_info       = estimate_cost()

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
            "Cypher is intuitive — low barrier to entry for new developers",
            "Free tier covers this entire 100K triple workload at no cost",
            "Index-free adjacency makes traversal fast without manual index tuning",
            "No SPARQL support — a limitation for RDF/OWL-heavy enterprise ontologies",
            "Python driver is well-maintained with good async support",
        ]
    }

    out_path = os.path.join(RESULTS_DIR, "neo4j_results.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n[Neo4j] Results written to {out_path}")


if __name__ == "__main__":
    main()