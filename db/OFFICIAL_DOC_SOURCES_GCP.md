# Comprehensive Official GCP Sources for MigrAI Graph

## Source of truth tiers

1. Product catalog and launch posture
- https://cloud.google.com/products
- https://docs.cloud.google.com/release-notes
- https://cloud.google.com/support-policy

2. Terraform resource-level definitions
- https://github.com/hashicorp/terraform-provider-google/tree/main/website/docs/r
- https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources
- https://registry.terraform.io/providers/hashicorp/google/latest/docs/data-sources

3. Service reference docs (API-level details)
- https://cloud.google.com/apis/docs/overview
- https://cloud.google.com/docs/reference
- Service-specific pages under https://cloud.google.com/docs

4. SKU and pricing metadata (for cost-aware ranking)
- https://cloud.google.com/skus
- https://cloud.google.com/billing/docs/reference/rest
- https://cloud.google.com/pricing

5. Architecture patterns and recommended usage
- https://cloud.google.com/architecture
- https://cloud.google.com/solutions

## Required graph provenance fields

Each recommendation should trace back to:
- source_id
- source_authority
- doc_url
- doc_title
- extracted_at
- parser_version

## Coverage objective

For each GCP service in graph:
- At least one product-level official page
- At least one terraform provider resource doc (if IaC supported)
- At least one API/reference page
- Optional: pricing/SKU and release-note enrichment

## Priority objective

Priority must be inferred from measurable evidence:
- documentation breadth (resource family depth)
- cross-source coverage score
- lifecycle confidence (GA vs preview/deprecated where available)
- pricing/cost posture signals when added

No manually hardcoded per-service or per-variant business priority values in production scoring.
