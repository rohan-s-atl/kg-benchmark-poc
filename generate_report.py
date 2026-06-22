"""
generate_report.py
Reads results/neo4j_results.json and results/neptune_results.json
and produces results/knowledge_graph_benchmark_report.html
"""

import json
import os
from datetime import datetime

RESULTS_DIR = "results"
OUTPUT_FILE = os.path.join(RESULTS_DIR, "knowledge_graph_benchmark_report.html")

TOOL_FILES = {
    "Neo4j AuraDB": "neo4j_results.json",
    "AWS Neptune":  "neptune_results.json",
}

TOOL_COLORS = {
    "Neo4j AuraDB": {"primary": "#4CAF96", "light": "#1a2e28", "bar": "#2E9B7A"},
    "AWS Neptune":  {"primary": "#FF9A3C", "light": "#2e2010", "bar": "#E07820"},
}

QUERY_LABELS = {
    "Q1_simple_lookup":  "Q1: Simple Lookup",
    "Q2_one_hop":        "Q2: 1-Hop Traversal",
    "Q3_two_hop":        "Q3: 2-Hop Traversal",
    "Q4_keyword_search": "Q4: Keyword Search",
    "Q5_aggregation":    "Q5: Aggregation",
}


def load_results() -> dict:
    results = {}
    for tool, fname in TOOL_FILES.items():
        path = os.path.join(RESULTS_DIR, fname)
        if os.path.exists(path):
            with open(path) as f:
                results[tool] = json.load(f)
            print(f"Loaded: {path}")
        else:
            print(f"WARNING: {path} not found")
    return results


def build_ingestion_chart_data(results: dict):
    labels = []
    times  = []
    tps    = []
    colors = []
    for tool, data in results.items():
        if "ingestion" in data:
            labels.append(tool)
            times.append(data["ingestion"]["elapsed_seconds"])
            tps.append(data["ingestion"]["triples_per_second"])
            colors.append(TOOL_COLORS.get(tool, {}).get("bar", "#888"))
    return json.dumps(labels), json.dumps(times), json.dumps(tps), json.dumps(colors)


def build_query_chart_data(results: dict):
    all_qids = set()
    for data in results.values():
        if "queries" in data:
            all_qids.update(data["queries"].keys())
    qids = sorted(all_qids)

    datasets = []
    for tool, data in results.items():
        c = TOOL_COLORS.get(tool, {}).get("bar", "#888")
        vals = []
        for qid in qids:
            v = data.get("queries", {}).get(qid, {}).get("avg_ms", 0)
            vals.append(v)
        datasets.append({
            "label":           tool,
            "data":            vals,
            "backgroundColor": c,
            "borderRadius":    4,
        })

    labels = [QUERY_LABELS.get(q, q) for q in qids]
    return json.dumps(labels), json.dumps(datasets)


def render_cost_card(tool: str, cost: dict) -> str:
    c = TOOL_COLORS.get(tool, {})
    free_badge = (
        '<span class="badge badge-free">Free Tier</span>'
        if cost.get("free_tier_eligible") else
        '<span class="badge badge-paid">No Free Tier</span>'
    )
    rows = []
    skip = {"free_tier_eligible", "note"}
    for k, v in cost.items():
        if k in skip:
            continue
        label = k.replace("_", " ").title()
        rows.append(f'<tr><td class="cost-key">{label}</td><td class="cost-val">{v}</td></tr>')
    note = cost.get("note", "")
    return f"""
    <div class="cost-card" style="border-top: 4px solid {c.get('primary','#888')}; background:{c.get('light','#1a1d27')}">
      <div class="cost-header">
        <span class="cost-tool-name">{tool}</span>
        {free_badge}
      </div>
      <table class="cost-table">{''.join(rows)}</table>
      <p class="cost-note">{note}</p>
    </div>
    """


def render_dev_notes(tool: str, notes: list) -> str:
    c = TOOL_COLORS.get(tool, {})
    items = "".join(f"<li>{n}</li>" for n in notes)
    return f"""
    <div class="dev-card" style="border-left: 4px solid {c.get('primary','#888')}">
      <div class="dev-tool-name" style="color:{c.get('primary','#888')}">{tool}</div>
      <ul>{items}</ul>
    </div>
    """


def render_summary_table(results: dict) -> str:
    rows = ""
    for tool, data in results.items():
        c    = TOOL_COLORS.get(tool, {})
        ing  = data.get("ingestion", {})
        qs   = data.get("queries", {})
        cost = data.get("cost", {})
        avg_q = (
            round(sum(q["avg_ms"] for q in qs.values() if "avg_ms" in q) / len(qs), 1)
            if qs else "N/A"
        )
        free = "Yes" if cost.get("free_tier_eligible") else "No"
        price = (
            cost.get("estimated_monthly_1gib_usd") or
            cost.get("estimated_monthly_serverless_1ncu_usd") or
            cost.get("starter_monthly_usd") or "N/A"
        )
        price_str = f"${price}/mo" if isinstance(price, (int, float)) else str(price)
        rows += f"""
        <tr>
          <td><span class="tool-dot" style="background:{c.get('primary','#888')}"></span>{tool}</td>
          <td>{ing.get('elapsed_seconds', 'N/A')}s</td>
          <td>{ing.get('triples_per_second', 'N/A'):,} t/s</td>
          <td>{avg_q} ms</td>
          <td>{free}</td>
          <td>{price_str}</td>
        </tr>
        """
    return f"""
    <table class="summary-table">
      <thead>
        <tr>
          <th>Tool</th><th>Ingestion Time</th><th>Throughput</th>
          <th>Avg Query Latency</th><th>Free Tier</th><th>Starting Price</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """


def generate_html(results: dict) -> str:
    labels_ing, times_ing, tps_ing, colors_ing = build_ingestion_chart_data(results)
    labels_q, datasets_q = build_query_chart_data(results)
    cost_cards  = "".join(render_cost_card(t, d.get("cost", {})) for t, d in results.items())
    dev_cards   = "".join(render_dev_notes(t, d.get("dev_notes", [])) for t, d in results.items())
    summary_tbl = render_summary_table(results)
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Knowledge Graph Benchmark — Neo4j vs Neptune</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg:       #0f1117;
      --surface:  #1a1d27;
      --surface2: #22263a;
      --border:   #2e3347;
      --text:     #e8eaf0;
      --muted:    #8b91a8;
      --neo4j:    #4CAF96;
      --neptune:  #FF9A3C;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}

    .header {{ background: linear-gradient(135deg, #12151f 0%, #1e2235 100%); border-bottom: 1px solid var(--border); padding: 48px 40px 36px; text-align: center; }}
    .header h1 {{ font-size: 2rem; font-weight: 700; }}
    .header .subtitle {{ color: var(--muted); margin-top: 8px; font-size: 0.95rem; }}
    .tag-row {{ display: flex; justify-content: center; gap: 12px; margin-top: 20px; flex-wrap: wrap; }}
    .tag {{ padding: 4px 14px; border-radius: 20px; font-size: 0.78rem; font-weight: 600; }}
    .tag-neo4j   {{ background: #1a3d33; color: var(--neo4j);   border: 1px solid var(--neo4j); }}
    .tag-neptune {{ background: #3d2e14; color: var(--neptune); border: 1px solid var(--neptune); }}
    .tag-grey    {{ background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }}

    .container {{ max-width: 1200px; margin: 0 auto; padding: 40px 24px; }}
    .section {{ margin-bottom: 56px; }}
    .section-title {{ font-size: 1.25rem; font-weight: 700; margin-bottom: 24px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }}
    .section-title span {{ display: inline-block; width: 4px; height: 18px; background: linear-gradient(180deg, var(--neo4j), var(--neptune)); border-radius: 2px; margin-right: 10px; vertical-align: middle; }}

    .summary-table {{ width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 10px; overflow: hidden; }}
    .summary-table th {{ background: var(--surface2); padding: 12px 16px; text-align: left; font-size: 0.8rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
    .summary-table td {{ padding: 14px 16px; border-top: 1px solid var(--border); font-size: 0.9rem; }}
    .summary-table tr:hover td {{ background: var(--surface2); }}
    .tool-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; vertical-align: middle; }}

    .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
    @media (max-width: 768px) {{ .chart-grid {{ grid-template-columns: 1fr; }} }}
    .chart-box {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 24px; }}
    .chart-box h3 {{ font-size: 0.9rem; font-weight: 600; color: var(--muted); margin-bottom: 20px; text-transform: uppercase; letter-spacing: 0.4px; }}
    .chart-full {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 24px; margin-bottom: 24px; }}
    .chart-full h3 {{ font-size: 0.9rem; font-weight: 600; color: var(--muted); margin-bottom: 20px; text-transform: uppercase; letter-spacing: 0.4px; }}

    .cost-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; }}
    @media (max-width: 768px) {{ .cost-grid {{ grid-template-columns: 1fr; }} }}
    .cost-card {{ border-radius: 10px; padding: 24px; border: 1px solid var(--border); }}
    .cost-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
    .cost-tool-name {{ font-weight: 700; font-size: 1rem; color: #ffffff; }}
    .badge {{ font-size: 0.7rem; font-weight: 700; padding: 3px 10px; border-radius: 12px; }}
    .badge-free {{ background: #1a3d33; color: #4CAF96; }}
    .badge-paid {{ background: #3d2214; color: #FF6B6B; }}
    .cost-table {{ width: 100%; border-collapse: collapse; }}
    .cost-key {{ font-size: 0.78rem; color: #c8cfe0; padding: 5px 0; text-transform: capitalize; width: 55%; }}
    .cost-val {{ font-size: 0.82rem; font-weight: 600; color: #ffffff; }}
    .cost-note {{ margin-top: 14px; font-size: 0.78rem; color: #a0a8c0; font-style: italic; line-height: 1.5; }}

    .dev-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; }}
    @media (max-width: 768px) {{ .dev-grid {{ grid-template-columns: 1fr; }} }}
    .dev-card {{ background: var(--surface); border-radius: 10px; padding: 20px 20px 20px 16px; border: 1px solid var(--border); }}
    .dev-tool-name {{ font-weight: 700; font-size: 0.95rem; margin-bottom: 12px; }}
    .dev-card ul {{ padding-left: 18px; }}
    .dev-card li {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 7px; line-height: 1.5; }}

    .footer {{ text-align: center; padding: 32px; color: var(--muted); font-size: 0.78rem; border-top: 1px solid var(--border); }}
  </style>
</head>
<body>
<div class="header">
  <h1>Knowledge Graph Cost &amp; Performance Benchmark</h1>
  <p class="subtitle">Enterprise Agentic AI Context Layer POC &mdash; 100K Triple Workload</p>
  <div class="tag-row">
    <span class="tag tag-neo4j">Neo4j AuraDB</span>
    <span class="tag tag-neptune">AWS Neptune</span>
    <span class="tag tag-grey">100K Triples</span>
    <span class="tag tag-grey">5 Query Types</span>
    <span class="tag tag-grey">Generated {generated_at}</span>
  </div>
</div>

<div class="container">
  <div class="section">
    <div class="section-title"><span></span>Executive Summary</div>
    {summary_tbl}
  </div>

  <div class="section">
    <div class="section-title"><span></span>Ingestion Performance (100K Triples)</div>
    <div class="chart-grid">
      <div class="chart-box">
        <h3>Total Ingestion Time (seconds, lower is better)</h3>
        <canvas id="ingestionTimeChart" height="220"></canvas>
      </div>
      <div class="chart-box">
        <h3>Ingestion Throughput (triples/sec, higher is better)</h3>
        <canvas id="ingestionTpsChart" height="220"></canvas>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title"><span></span>Query Latency Benchmark (avg ms, lower is better)</div>
    <div class="chart-full">
      <h3>Average Query Latency by Type &mdash; Simulating Agentic Context Retrieval</h3>
      <canvas id="queryChart" height="120"></canvas>
    </div>
  </div>

  <div class="section">
    <div class="section-title"><span></span>Cost Analysis</div>
    <div class="cost-grid">{cost_cards}</div>
  </div>

  <div class="section">
    <div class="section-title"><span></span>Developer Experience &amp; Fit Assessment</div>
    <div class="dev-grid">{dev_cards}</div>
  </div>
</div>

<div class="footer">
  Xebia Agentic AI Research POC &bull; Synthetic enterprise dataset (Wikidata-style) &bull;
  Queries simulate agent context retrieval workloads &bull; {generated_at}
</div>

<script>
  const LABELS_ING = {labels_ing};
  const TIMES_ING  = {times_ing};
  const TPS_ING    = {tps_ing};
  const COLORS_ING = {colors_ing};
  const LABELS_Q   = {labels_q};
  const DATASETS_Q = {datasets_q};

  const chartDefaults = {{
    responsive: true,
    plugins: {{
      legend: {{ labels: {{ color: '#8b91a8', font: {{ size: 11 }} }} }},
      tooltip: {{ backgroundColor: '#1a1d27', borderColor: '#2e3347', borderWidth: 1 }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#8b91a8', font: {{ size: 11 }} }}, grid: {{ color: '#2e3347' }} }},
      y: {{ ticks: {{ color: '#8b91a8', font: {{ size: 11 }} }}, grid: {{ color: '#2e3347' }} }}
    }}
  }};

  new Chart(document.getElementById('ingestionTimeChart'), {{
    type: 'bar',
    data: {{ labels: LABELS_ING, datasets: [{{ label: 'Seconds', data: TIMES_ING, backgroundColor: COLORS_ING, borderRadius: 5 }}] }},
    options: {{ ...chartDefaults, plugins: {{ ...chartDefaults.plugins, legend: {{ display: false }} }} }}
  }});

  new Chart(document.getElementById('ingestionTpsChart'), {{
    type: 'bar',
    data: {{ labels: LABELS_ING, datasets: [{{ label: 'Triples/sec', data: TPS_ING, backgroundColor: COLORS_ING, borderRadius: 5 }}] }},
    options: {{ ...chartDefaults, plugins: {{ ...chartDefaults.plugins, legend: {{ display: false }} }} }}
  }});

  new Chart(document.getElementById('queryChart'), {{
    type: 'bar',
    data: {{ labels: LABELS_Q, datasets: DATASETS_Q }},
    options: chartDefaults
  }});
</script>
</body>
</html>
"""


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    results = load_results()
    if not results:
        raise SystemExit("No result files found.")
    html = generate_html(results)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nReport generated: {OUTPUT_FILE}")
    print("Open in a browser to view the full benchmark report.")


if __name__ == "__main__":
    main()