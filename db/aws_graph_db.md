# AWS Neo4j Graph DB Import

This graph is generated dynamically using HTTP GET requests against official AWS Pricing API endpoints.

This model supports recommendations at two levels:

- Service level: `Service.basePriority` for overall likelihood.
- Customization level: `ServiceVariant.priority` for service + tier/config specificity.

## Graph Model

- `(:CloudProvider {name: 'AWS'})`
- `(:ServiceCategory {name})`
- `(:Service {id, name, basePriority, rankInCategory, notes})`
- `(:ServiceVariant {id, name, priority, dimension, configuration})`

Relationships:

- `(CloudProvider)-[:HAS_CATEGORY]->(ServiceCategory)`
- `(CloudProvider)-[:HAS_SERVICE]->(Service)`
- `(Service)-[:IN_CATEGORY]->(ServiceCategory)`
- `(Service)-[:HAS_VARIANT]->(ServiceVariant)`
- `(Service)-[:ALTERNATIVE_TO {context}]->(Service)`

## Data Sources (Official AWS)

- `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json`
- Per-service `currentVersionUrl`
- Per-service `currentRegionIndexUrl`

## Setup

1. Add these to `.env`:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password

# Optional import tuning
AWS_IMPORT_MAX_SERVICES=0
AWS_IMPORT_MAX_PRODUCTS_PER_SERVICE=2500
AWS_IMPORT_MAX_VARIANTS_PER_DIMENSION=6

`AWS_IMPORT_MAX_SERVICES=0` means import all services discovered from AWS Pricing index (default behavior).
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Import AWS graph from official AWS endpoints:

```bash
python db/load_aws_graph.py
```

## Example Queries

Top services by category:

```cypher
MATCH (c:ServiceCategory)<-[:IN_CATEGORY]-(s:Service)
RETURN c.name AS category, s.name AS service, s.basePriority AS priority, s.rankInCategory AS rank
ORDER BY category, rank ASC;
```

Top service variants (service + customization):

```cypher
MATCH (s:Service)-[:HAS_VARIANT]->(v:ServiceVariant)
RETURN s.name AS service, v.name AS variant, v.dimension AS dimension, v.priority AS priority
ORDER BY v.priority DESC
LIMIT 20;
```

Best recommendation inside one category (example: Relational Database):

```cypher
MATCH (c:ServiceCategory {name: 'Relational Database'})<-[:IN_CATEGORY]-(s:Service)
OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:ServiceVariant)
WITH c, s, v
ORDER BY s.basePriority DESC, v.priority DESC
RETURN c.name AS category, s.name AS service, s.basePriority AS servicePriority,
       collect({variant: v.name, variantPriority: v.priority})[0..3] AS topVariants;
```
