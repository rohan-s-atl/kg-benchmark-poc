"""
seed_demo_data.py
Populates Neo4j AuraDB with the same synthetic enterprise knowledge graph used
by benchmarks/benchmark_neo4j.py, but WITHOUT running the cleanup step at the
end — so the graph stays populated for the live agent demo (app/).

benchmark_neo4j.py's main() always deletes everything it ingests once its
benchmark queries finish, since repeatable benchmarking requires starting
from an empty graph each run. This script reuses its generate_dataset() and
ingest() functions directly so the two stay in sync, but stops right after
ingestion.

Usage:
  python3 seed_demo_data.py            # refuses to run if the graph is non-empty
  python3 seed_demo_data.py --force     # wipes existing Entity nodes first, then re-seeds
"""

from dotenv import load_dotenv
load_dotenv()

import argparse
import importlib.util
import os

from neo4j import GraphDatabase

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")


def load_benchmark_module():
    path = os.path.join(os.path.dirname(__file__), "benchmarks", "benchmark_neo4j.py")
    spec = importlib.util.spec_from_file_location("benchmark_neo4j", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def existing_node_count(driver) -> int:
    with driver.session() as session:
        return session.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]


def wipe(driver):
    print("[Seed] Wiping existing Entity nodes...")
    with driver.session() as session:
        session.run("DROP INDEX entity_name IF EXISTS")
        while True:
            result = session.run(
                "MATCH (n:Entity) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS cnt"
            )
            if result.single()["cnt"] == 0:
                break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true",
        help="Wipe any existing Entity nodes before seeding, instead of refusing to run.",
    )
    args = parser.parse_args()

    if not NEO4J_URI or not NEO4J_PASSWORD:
        raise SystemExit("Missing NEO4J_URI / NEO4J_PASSWORD — check your .env file.")

    bench = load_benchmark_module()

    print(f"[Seed] Connecting to {NEO4J_URI}...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()

    count = existing_node_count(driver)
    if count > 0:
        if not args.force:
            driver.close()
            raise SystemExit(
                f"Graph already has {count:,} Entity nodes. Re-running ingest() without "
                "cleanup would create duplicates. Pass --force to wipe and re-seed."
            )
        wipe(driver)

    triples = bench.generate_dataset(bench.TRIPLE_COUNT)
    bench.ingest(driver, triples)
    driver.close()

    print(f"[Seed] Done. Graph is now populated with {bench.TRIPLE_COUNT:,} triples "
          f"and ready for the demo app (app/main.py).")


if __name__ == "__main__":
    main()
