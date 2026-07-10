"""
benchmark_replay.py
Builds a scripted, timed replay of the actual captured benchmark console
output (results/neo4j_results.json, results/neptune_results.json) for the
"Run Benchmark" button in the demo app. This does NOT re-run the real
benchmark scripts — those ingest, benchmark, then wipe the graph, which
would break the live chat demo. It replays the real numbers from the last
actual run, line by line with realistic pacing, so it looks and feels live.
"""

TOOLS = [
    ("Neo4j AuraDB", "neo4j", "Neo4j"),
    ("AWS Neptune", "neptune", "Neptune"),
]


def build_replay_script(results: dict):
    """Returns a list of (delay_seconds_before_line, tool_key, text) tuples."""
    lines = []

    def add(delay, tool_key, text):
        lines.append((delay, tool_key, text))

    for label, tool_key, prefix in TOOLS:
        data = results.get(label)
        if not data:
            add(0.2, tool_key, f"[{prefix}] No results found — run the benchmark script first.")
            continue

        ing = data.get("ingestion", {})
        add(0.3, tool_key, "=" * 60)
        add(0.05, tool_key, f"  {label} — Knowledge Graph Benchmark")
        add(0.05, tool_key, "=" * 60)
        add(0.3, tool_key, f"[{prefix}] Connecting...")
        add(0.4, tool_key, f"[{prefix}] Connected.")
        add(0.3, tool_key, f"[{prefix}] Ingesting {ing.get('total_triples', 0):,} triples...")
        add(0.9, tool_key, f"[{prefix}] Done in {ing.get('elapsed_seconds', 0)}s "
                           f"({ing.get('triples_per_second', 0):,.1f} triples/sec)")

        query_runs = data.get("methodology", {}).get("query_runs", 20)
        for qid, q in data.get("queries", {}).items():
            # Aggregation queries run whole-graph, no per-entity variation —
            # match the real scripts' console output (see benchmark_neo4j.py).
            entity_count = 1 if "aggregation" in qid else data.get("methodology", {}).get("entities_per_query", 5)
            add(0.35, tool_key, f"[{prefix}] {qid} ({query_runs} runs x {entity_count} entities)...")
            add(0.45, tool_key, f"  avg {q['avg_ms']} ms  p95 {q['p95_ms']} ms")

        add(0.3, tool_key, f"[{prefix}] Cleaning up...")
        add(0.25, tool_key, f"[{prefix}] Done.")
        add(0.4, tool_key, "")

    return lines
