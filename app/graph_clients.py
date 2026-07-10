"""
graph_clients.py
Thin synchronous clients for running a single query against Neo4j and Neptune
and timing it. Reuses the same connection approach as benchmarks/benchmark_neo4j.py
and benchmarks/benchmark_neptune.py.
"""

import time

import requests
from neo4j import GraphDatabase
from neo4j.graph import Node, Relationship


def _serialize_value(value):
    if isinstance(value, (Node, Relationship)):
        return dict(value)
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


class Neo4jClient:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def run(self, cypher: str):
        t0 = time.perf_counter()
        with self.driver.session() as session:
            records = list(session.run(cypher))
            rows = [{k: _serialize_value(v) for k, v in r.items()} for r in records]
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return rows, elapsed_ms


class NeptuneClient:
    def __init__(self, endpoint: str):
        self.url = f"{endpoint.rstrip('/')}/sparql"

    def run(self, sparql: str):
        t0 = time.perf_counter()
        r = requests.get(
            self.url,
            params={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            timeout=30,
        )
        r.raise_for_status()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        data = r.json()
        bindings = data.get("results", {}).get("bindings", [])
        rows = [{k: v.get("value") for k, v in b.items()} for b in bindings]
        return rows, elapsed_ms
