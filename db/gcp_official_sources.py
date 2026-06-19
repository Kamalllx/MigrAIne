"""Official source endpoints used to build the GCP knowledge graph.

These are intentionally vendor/provider authoritative sources.
"""

GCP_OFFICIAL_SOURCES = [
    {
        "id": "gcp_products_catalog",
        "name": "Google Cloud Product Catalog",
        "authority": "Google Cloud",
        "url": "https://cloud.google.com/products",
    },
    {
        "id": "terraform_google_provider_repo",
        "name": "Terraform Google Provider Resource Docs (source repository)",
        "authority": "HashiCorp + Google Cloud Terraform Team",
        "url": "https://github.com/hashicorp/terraform-provider-google/tree/main/website/docs/r",
    },
    {
        "id": "google_cloud_release_notes",
        "name": "Google Cloud Release Notes",
        "authority": "Google Cloud",
        "url": "https://docs.cloud.google.com/release-notes",
    },
]
