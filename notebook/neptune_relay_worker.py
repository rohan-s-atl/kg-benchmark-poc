"""
neptune_relay_worker.py
Run this INSIDE the Neptune VPC (e.g. a Neptune Notebook terminal) to bridge
live Neptune queries into the local agent demo app, which can't reach Neptune
directly from outside the VPC.

How it works:
  1. Polls {LOCAL_APP_URL}/api/neptune-pending for a job the local app queued.
  2. Runs the SPARQL query against the live Neptune cluster.
  3. POSTs the result back to {LOCAL_APP_URL}/api/neptune-result.

Setup:
  1. Expose your local app to the internet, e.g.: ngrok http 8000
  2. Set LOCAL_APP_URL below to that public URL (e.g. the ngrok https URL).
  3. Set RELAY_TOKEN below to match NEPTUNE_RELAY_TOKEN in your local .env.
  4. Set NEPTUNE_RELAY=true in your local .env and restart the app.
  5. Upload this file to the Neptune Notebook and run it from a terminal:
       python3 neptune_relay_worker.py
     Leave it running for the duration of the demo.
"""

import time

import requests

# ---- Configure these ----
LOCAL_APP_URL = "https://cope-unluckily-flattery.ngrok-free.dev"
NEPTUNE_ENDPOINT = "https://kg-bench.cluster-c6vu88s8s7lr.us-east-1.neptune.amazonaws.com:8182"
RELAY_TOKEN = "6h5vGNiJ5S9oqxDqLsaZumPpRJVzCSq1"
POLL_INTERVAL_S = 2
# --------------------------

HEADERS = {"X-Relay-Token": RELAY_TOKEN} if RELAY_TOKEN else {}


def run_sparql(sparql: str):
    url = f"{NEPTUNE_ENDPOINT.rstrip('/')}/sparql"
    t0 = time.perf_counter()
    r = requests.get(
        url,
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


def main():
    print(f"[Relay] Polling {LOCAL_APP_URL} every {POLL_INTERVAL_S}s. Ctrl+C to stop.")
    while True:
        try:
            r = requests.get(f"{LOCAL_APP_URL}/api/neptune-pending", headers=HEADERS, timeout=10)
            r.raise_for_status()
            job = r.json().get("job")
        except Exception as e:
            print(f"[Relay] Poll failed: {e}")
            time.sleep(POLL_INTERVAL_S)
            continue

        if not job:
            time.sleep(POLL_INTERVAL_S)
            continue

        print(f"[Relay] Got job {job['id']} for question: {job['question']!r}")
        try:
            rows, latency_ms = run_sparql(job["sparql"])
            print(f"[Relay]   -> {len(rows)} rows in {latency_ms:.1f}ms")
            requests.post(
                f"{LOCAL_APP_URL}/api/neptune-result",
                json={"id": job["id"], "rows": rows, "latency_ms": latency_ms},
                headers=HEADERS,
                timeout=10,
            )
        except Exception as e:
            print(f"[Relay]   -> error: {e}")
            requests.post(
                f"{LOCAL_APP_URL}/api/neptune-result",
                json={"id": job["id"], "error": str(e)},
                headers=HEADERS,
                timeout=10,
            )


if __name__ == "__main__":
    main()
