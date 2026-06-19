# MigrAIne: GCP Discovery for AWS Migration

MigrAIne discovers GCP resources using Cloud Asset Inventory, normalizes them into migration-focused records, and maps resources to likely AWS target services.

The project provides:

- A CLI discovery/export flow via `test.py`
- A Flask API server and dashboard via `main.py`
- Resource normalization and migration hints in `gcp_discovery/client.py`

## Features

- Multi-content asset discovery (`RESOURCE`, `IAM_POLICY`, `ORG_POLICY`, `ACCESS_POLICY`)
- Search metadata enrichment from `search_all_resources`
- Type-aware parsing for common GCP services (Compute, Storage, IAM, Artifact Registry, Billing, Cloud Run, Cloud Functions)
- Aggregated migration snapshot:
	- counts by GCP type/category
	- counts by mapped AWS target
	- enabled API inventory
- JSON-safe export for downstream tools

## Requirements

- Python 3.10+
- Authenticated Google Cloud credentials (ADC)
- Packages:
	- `google-cloud-asset`
	- `google-auth`
	- `flask`

Install dependencies:

```bash
pip install google-cloud-asset google-auth flask
```

## Authentication

Use one of the following:

1. Application Default Credentials:

```bash
gcloud auth application-default login
```

2. Service account key:

```bash
set GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\service-account.json
```

Set project if needed:

```bash
set GCP_PROJECT_ID=your-project-id
```

## Run CLI Inventory Export

```bash
python test.py
```

This prints a migration summary and writes:

- `migration_blueprint.json`
- `gcp_architecture.tf`

To generate only Terraform:

```bash
python export_terraform.py
```

## Run Flask Dashboard

```bash
python main.py
```

Then open:

- `http://localhost:5000`

Key API endpoints:

- `GET /api/health`
- `GET /api/inventory`
- `GET /api/inventory?refresh=1`
- `GET /api/resources`
- `GET /api/resources?category=compute`
- `GET /api/resources?project_name=<project-name>`
- `GET /api/resources?project_id=<project-id>`
- `GET /api/resources?asset_type=compute.googleapis.com/Instance`
- `GET /api/resources?q=keyword`
- `GET /api/resources/<resource_id>`

## Project Structure

```text
app.py                      # Legacy script
main.py                     # Flask app + API
test.py                     # CLI snapshot + export
export_terraform.py         # Terraform export helper
gcp_discovery/
	__init__.py
	client.py                 # Discovery + normalization
	models.py                 # GCPResource model
	parsers.py                # Additional parser helpers (if used)
templates/
	index.html                # Dashboard UI
```

## Notes

- First run can take time depending on project size.
- Some asset content types can vary by API/library version; warnings are included in snapshot output.
- Not every GCP resource has a direct AWS 1:1 mapping. Unmapped resources are tagged as `Review Required`.
