// Verify provenance: every service should have at least one authoritative doc
MATCH (s:GCPService)
OPTIONAL MATCH (s)-[:EVIDENCED_BY]->(d:DocPage)<-[:PUBLISHES]-(src:DocSource)
RETURN s.key AS service_key,
       s.name AS service_name,
       collect(DISTINCT src.id) AS source_ids,
       count(DISTINCT d) AS evidence_docs
ORDER BY evidence_docs ASC, service_key;

// Top inferred services by family
MATCH (f:GCPServiceFamily)-[:HAS_SERVICE]->(s:GCPService)
RETURN f.name AS family, s.key AS service_key, s.priority AS inferred_priority
ORDER BY family, inferred_priority DESC;

// Top inferred variants for a service
MATCH (s:GCPService {key: $service_key})-[:HAS_VARIANT]->(v:GCPServiceVariant)
RETURN s.key AS service_key,
       v.key AS variant_key,
       v.name AS variant_name,
       v.tier AS tier,
       v.priority AS inferred_priority
ORDER BY inferred_priority DESC;

// Identify services with low evidence (candidates for enrichment)
MATCH (s:GCPService)
OPTIONAL MATCH (s)-[:EVIDENCED_BY]->(d:DocPage)
WITH s, count(DISTINCT d) AS evidence_count
WHERE evidence_count < 1
RETURN s.key AS service_key, s.name AS service_name, evidence_count
ORDER BY service_key;
