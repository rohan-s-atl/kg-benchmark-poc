"""
agent.py
Translates a natural-language question into a Cypher query (Neo4j) and a SPARQL
query (Neptune) via the Anthropic API, and turns retrieved graph context into a
conversational streamed answer.
"""

import json

import anthropic

MODEL = "claude-opus-4-8"

SCHEMA_DESCRIPTION = """
The knowledge graph models an enterprise: companies, employees, products, locations, and industries.

Neo4j (property graph):
- Every entity is a node with label `Entity` and a single property `name`.
- Every relationship has type `RELATION` and carries a `type` property naming the semantic relationship.
- Pattern for a typed relationship: (a:Entity)-[r:RELATION {type: '<relation_type>'}]->(b:Entity)

AWS Neptune (RDF triple store, SPARQL 1.1):
- Every entity is a URI: http://enterprise.poc/entity/<Name>
- Every relationship is a predicate URI: http://enterprise.poc/entity/<relation_type>
- All triples live in the named graph <http://enterprise.poc/graph>
- Pattern: SELECT ... FROM <http://enterprise.poc/graph> WHERE { <http://enterprise.poc/entity/Name> <http://enterprise.poc/entity/relation_type> ?o . }

Relationship types (identical as the Neo4j `type` property and the Neptune predicate name):
employs, produces, located_in, partners_with, operates_in, reports_to, works_on, supplied_by, sold_in

Entity naming convention: Company_N, Employee_N, Product_N, Location_N, Industry_<Name>
(e.g. Industry_Finance, Industry_Healthcare, Industry_Technology, Industry_Manufacturing,
Industry_Retail, Industry_Energy, Industry_Logistics, Industry_Media). N is an integer.
"""

QUERY_TOOL = {
    "name": "generate_graph_queries",
    "description": (
        "Generate the Cypher and SPARQL queries needed to answer the user's "
        "question against the knowledge graph."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "cypher": {
                "type": "string",
                "description": "A single valid read-only Cypher query for Neo4j.",
            },
            "sparql": {
                "type": "string",
                "description": "A single valid read-only SPARQL 1.1 query for the Neptune graph.",
            },
        },
        "required": ["cypher", "sparql"],
    },
}

DANGEROUS_KEYWORDS = [
    "CREATE", "DELETE", "REMOVE", "MERGE", "DROP", "SET ", "DETACH",
    "LOAD CSV", "INSERT", "CLEAR", "COPY",
]


def _reject_if_unsafe(query: str):
    upper = query.upper()
    for kw in DANGEROUS_KEYWORDS:
        if kw in upper:
            raise ValueError(f"Generated query contains a disallowed write keyword: {kw.strip()}")


async def generate_queries(client: anthropic.AsyncAnthropic, question: str) -> dict:
    message = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=(
            "You are a graph query generation engine for a knowledge-graph demo. "
            "Given a natural-language question, produce ONE read-only Cypher query "
            "for Neo4j and ONE read-only SPARQL query for AWS Neptune that answer it, "
            "using the schema below. Only ever generate read queries (MATCH/SELECT) "
            "— never write or delete data.\n\n" + SCHEMA_DESCRIPTION
        ),
        tools=[QUERY_TOOL],
        tool_choice={"type": "tool", "name": "generate_graph_queries"},
        messages=[{"role": "user", "content": question}],
    )

    for block in message.content:
        if block.type == "tool_use":
            cypher = block.input["cypher"].strip()
            sparql = block.input["sparql"].strip()
            _reject_if_unsafe(cypher)
            _reject_if_unsafe(sparql)
            return {"cypher": cypher, "sparql": sparql}

    raise RuntimeError("Model did not return a tool call with generated queries.")


async def stream_answer(client: anthropic.AsyncAnthropic, question: str, neo4j_result, neptune_result):
    context = (
        f"Question: {question}\n\n"
        f"Neo4j result:\n{json.dumps(neo4j_result, indent=2, default=str)}\n\n"
        f"Neptune result:\n{json.dumps(neptune_result, indent=2, default=str)}\n"
    )

    async with client.messages.stream(
        model=MODEL,
        max_tokens=1024,
        system=(
            "You are an assistant answering questions about an enterprise knowledge graph. "
            "Answer the user's question conversationally and concisely, using ONLY the graph "
            "query results provided below as context. Do not use general knowledge, and do "
            "not make anything up. If the two result sets disagree or one is empty, mention "
            "it briefly. If neither result set answers the question, say so plainly."
        ),
        messages=[{"role": "user", "content": context}],
    ) as stream:
        async for text in stream.text_stream:
            yield text
