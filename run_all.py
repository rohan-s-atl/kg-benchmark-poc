"""
run_all.py
Master orchestrator for the Knowledge Graph Benchmark POC.
Runs all benchmark scripts sequentially, then generates the HTML report.

Usage:
  python run_all.py

Optional flags:
  --skip-neo4j       Skip Neo4j benchmark
  --skip-neptune     Skip Neptune benchmark
  --no-cleanup       Keep test data in each database after benchmarking
"""

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime

RESULTS_DIR = "results"


def load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def section(title: str):
    width = 62
    print("\n" + "═" * width)
    print(f"  {title}")
    print("═" * width)


def check_env():
    """Warn about missing credentials before starting."""
    checks = {
        "Neo4j":   ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"],
        "Neptune": ["NEPTUNE_ENDPOINT"],
    }
    warnings = []
    for tool, keys in checks.items():
        missing = [k for k in keys if not os.getenv(k)]
        if missing:
            warnings.append(f"  {tool}: missing {', '.join(missing)}")
    if warnings:
        print("\n⚠  Missing environment variables (will use placeholder defaults):")
        for w in warnings:
            print(w)
        print("  Edit the CONFIG block at the top of each benchmark script,")
        print("  or export the variables before running.\n")


def run_benchmark(script_path: str, tool_name: str) -> dict | None:
    section(f"Running: {tool_name}")
    t0 = time.perf_counter()
    try:
        mod = load_module(script_path, tool_name.lower().replace(" ", "_"))
        mod.main()
        elapsed = time.perf_counter() - t0
        print(f"\n✓ {tool_name} completed in {elapsed:.1f}s")
        return {"tool": tool_name, "status": "success", "elapsed": round(elapsed, 1)}
    except SystemExit as e:
        print(f"\n✗ {tool_name} exited: {e}")
        return {"tool": tool_name, "status": "failed", "error": str(e)}
    except Exception as e:
        print(f"\n✗ {tool_name} error: {e}")
        return {"tool": tool_name, "status": "failed", "error": str(e)}


def run_report():
    section("Generating HTML Report")
    try:
        mod = load_module("generate_report.py", "generate_report")
        mod.main()
        print("✓ Report generated: results/knowledge_graph_benchmark_report.html")
        return True
    except Exception as e:
        print(f"✗ Report generation failed: {e}")
        return False


def print_summary(run_log: list):
    section("Run Summary")
    for entry in run_log:
        icon   = "✓" if entry["status"] == "success" else "✗"
        detail = f"({entry['elapsed']}s)" if entry["status"] == "success" else f"({entry.get('error', '')})"
        print(f"  {icon}  {entry['tool']:<20} {detail}")


def main():
    parser = argparse.ArgumentParser(description="Run all KG benchmark scripts.")
    parser.add_argument("--skip-neo4j",   action="store_true", help="Skip Neo4j")
    parser.add_argument("--skip-neptune", action="store_true", help="Skip Neptune")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║   Knowledge Graph Benchmark POC — Full Suite             ║")
    print("║   AWS Neptune  ·  Neo4j AuraDB                           ║")
    print(f"║   {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'):<54}║")
    print("╚══════════════════════════════════════════════════════════╝")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    check_env()

    run_log = []

    benchmarks = [
        ("benchmarks/benchmark_neo4j.py",    "Neo4j AuraDB",  args.skip_neo4j),
        ("benchmarks/benchmark_neptune.py",  "AWS Neptune",   args.skip_neptune),
    ]

    for script, name, skip in benchmarks:
        if skip:
            print(f"\n  -- Skipping {name} (--skip flag set)")
            continue
        if not os.path.exists(script):
            print(f"\n  -- {script} not found, skipping {name}")
            continue
        result = run_benchmark(script, name)
        if result:
            run_log.append(result)

    # Only generate report if at least one benchmark produced results
    result_files = [f for f in os.listdir(RESULTS_DIR) if f.endswith("_results.json")]
    if result_files:
        run_report()
    else:
        print("\n  No result files found — skipping report generation.")

    print_summary(run_log)
    print(f"\n  Done. Open results/knowledge_graph_benchmark_report.html to view the report.\n")


if __name__ == "__main__":
    main()