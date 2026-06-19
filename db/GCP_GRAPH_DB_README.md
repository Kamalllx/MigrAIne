# GCP Graph DB (Neo4j) for MigrAI

This pipeline builds the GCP graph from official sources and stores evidence URLs in Neo4j.
Priorities are inferred from graph/document signals, not manually assigned constants.

## Official sources used

- Google Cloud Product Catalog: https://cloud.google.com/products
- Terraform Google Provider docs repository: https://github.com/hashicorp/terraform-provider-google/tree/main/website/docs/r
- Google Cloud Release Notes (for enrichment): https://docs.cloud.google.com/release-notes

Source config is in `db/gcp_official_sources.py`.

## Graph model

- `(:GCPCloud {key, name, priority_mode})`
- `(:DocSource {id, name, authority, url})`
- `(:DocPage {url, title, doc_type})`
- `(:GCPServiceFamily {name})`
- `(:GCPService {key, name, family, priority, priority_mode, priority_formula})`
- `(:GCPServiceVariant {key, name, tier, priority, priority_mode, priority_formula})`

Relations:
- `(DocSource)-[:PUBLISHES]->(DocPage)`
- `(GCPService)-[:EVIDENCED_BY]->(DocPage)`
- `(GCPCloud)-[:HAS_FAMILY]->(GCPServiceFamily)`
- `(GCPServiceFamily)-[:HAS_SERVICE]->(GCPService)`
- `(GCPService)-[:HAS_VARIANT]->(GCPServiceVariant)`
- `(GCPService)-[:SIMILAR_TO]->(GCPService)`

## Inference strategy

Service priority is inferred from documentation graph features only:

`service_priority = 30 + 70 * family_breadth_signal`

- `family_breadth_signal` = normalized count of Terraform resources in a service family.
- No hardcoded per-service score list is used in the docs pipeline.

Variant priority is inferred from extracted official-doc enum options:

`variant_priority = 20 + 50 * global_value_frequency + 30 * (1 / enum_position)`

- `global_value_frequency`: normalized frequency of a value across resource docs.
- `enum_position`: earlier value in provider docs gets higher signal.

## Seed steps

1. Install dependencies:

```powershell
pip install -r requirements.txt
```

2. Set Neo4j env vars:

```powershell
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD="your-password"
$env:NEO4J_DATABASE="neo4j"
```

3. Build graph from official docs:

```powershell
python db/build_gcp_docs_graph.py
```

## Query helpers

- `db/gcp_docs_graph_queries.cypher`: provenance, coverage, top services, top variants.

## Note on previous bootstrap catalog

`gcp_graph_catalog.py` and `seed_gcp_graph.py` are bootstrap files and can be kept for fallback/dev.
For production scoring and provenance, use `db/build_gcp_docs_graph.py`.
