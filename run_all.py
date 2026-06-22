"""
run_all.py
Runs the Neo4j and Neptune benchmarks sequentially, then generates the HTML report.

Usage:
  python run_all.py                  # run both
  python run_all.py --skip-neptune   # Neo4j only (runs locally)
  python run_all.py --skip-neo4j     # Neptune only
"""

from dotenv import load_dotenv
load_dotenv()

import argparse
import importlib.util
import os
import time
from datetime import datetime

RESULTS_DIR = "results"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_benchmark(script_path, tool_name):
    print(f"\n{'='*62}")
    print(f"  {tool_name}")
    print(f"{'='*62}")
    t0 = time.perf_counter()
    try:
        mod = load_module(script_path, tool_name.lower().replace(" ", "_"))
        mod.main()
        elapsed = time.perf_counter() - t0
        print(f"\n✓ {tool_name} finished in {elapsed:.1f}s")
        return {"tool": tool_name, "status": "success", "elapsed": round(elapsed, 1)}
    except SystemExit as e:
        print(f"\n✗ {tool_name} exited early: {e}")
        return {"tool": tool_name, "status": "failed", "error": str(e)}
    except Exception as e:
        print(f"\n✗ {tool_name} error: {e}")
        return {"tool": tool_name, "status": "failed", "error": str(e)}


def check_credentials():
    checks = {
        "Neo4j":   ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"],
        "Neptune": ["NEPTUNE_ENDPOINT"],
    }
    for tool, keys in checks.items():
        missing = [k for k in keys if not os.getenv(k)]
        if missing:
            print(f"  Warning: {tool} missing env vars: {', '.join(missing)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-neo4j",   action="store_true")
    parser.add_argument("--skip-neptune", action="store_true")
    args = parser.parse_args()

    print(f"\n  Knowledge Graph Benchmark — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Neo4j AuraDB vs AWS Neptune\n")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    check_credentials()

    benchmarks = [
        ("benchmarks/benchmark_neo4j.py",   "Neo4j AuraDB", args.skip_neo4j),
        ("benchmarks/benchmark_neptune.py",  "AWS Neptune",  args.skip_neptune),
    ]

    run_log = []
    for script, name, skip in benchmarks:
        if skip:
            print(f"\n  Skipping {name}")
            continue
        if not os.path.exists(script):
            print(f"\n  {script} not found, skipping")
            continue
        result = run_benchmark(script, name)
        if result:
            run_log.append(result)

    result_files = [f for f in os.listdir(RESULTS_DIR) if f.endswith("_results.json")]
    if result_files:
        print(f"\n{'='*62}\n  Generating Report\n{'='*62}")
        try:
            mod = load_module("generate_report.py", "generate_report")
            mod.main()
        except Exception as e:
            print(f"  Report generation failed: {e}")

    print(f"\n{'='*62}\n  Summary\n{'='*62}")
    for entry in run_log:
        status = f"✓ {entry['elapsed']}s" if entry["status"] == "success" else f"✗ {entry.get('error','')}"
        print(f"  {entry['tool']:<20} {status}")

    print(f"\n  Report: results/knowledge_graph_benchmark_report.html\n")


if __name__ == "__main__":
    main()