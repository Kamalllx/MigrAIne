import json
from pathlib import Path

from gcp_discovery import GCPDiscoveryClient


def main() -> None:
    client = GCPDiscoveryClient()
    blueprint = client.build_migration_blueprint()

    project = blueprint.get("project", {})
    summary = blueprint.get("summary", {})
    planes = blueprint.get("planes", {})

    print(f"Schema version: {blueprint.get('schema_version')}")
    print(f"Project ID: {project.get('id')}")
    print(f"Project Name: {project.get('name')}")
    print(f"Source region: {project.get('regions', {}).get('source_primary')}")
    print(f"Target region: {project.get('regions', {}).get('target_primary')}")
    print(f"Total raw assets: {summary.get('total_raw_assets', 0)}")
    print(f"Total resources: {summary.get('total_resources', 0)}")
    print(f"Total dependencies: {summary.get('total_dependencies', 0)}")

    warnings = blueprint.get("discovery_warnings", [])
    print(f"Discovery warnings: {len(warnings)}")
    for warning in warnings[:10]:
        print(f"  - {warning}")

    print("\nBy plane:")
    for plane, count in (summary.get("by_plane") or {}).items():
        print(f"  {plane}: {count}")

    print("\nBy source type:")
    for source_type, count in (summary.get("by_source_type") or {}).items():
        print(f"  {source_type}: {count}")

    enabled_services = planes.get("control_plane", {}).get("api_enablement", {}).get("enabled_services", [])
    print(f"\nEnabled services (control plane): {len(enabled_services)}")
    for service_name in enabled_services[:40]:
        print(f"  - {service_name}")

    resources = blueprint.get("resource_catalog", [])
    print(f"\nResource samples ({min(8, len(resources))} of {len(resources)}):")
    for resource in resources[:8]:
        print(f"\n  {resource.get('name')}")
        print(f"    ID: {resource.get('id')}")
        print(f"    Plane: {resource.get('plane')}")
        print(f"    Source Type: {resource.get('source_type')}")
        print(f"    Intent: {resource.get('what_it_does', {}).get('workload_intent')}")
        print(f"    Region: {resource.get('where_it_runs', {}).get('region')}")
        print(
            "    AWS Family: "
            + ", ".join(resource.get("target_preferences", {}).get("aws_service_family", []))
        )

    output_path = Path("migration_blueprint.json")
    output_path.write_text(json.dumps(blueprint, indent=2), encoding="utf-8")
    print(f"\nSaved migration blueprint to: {output_path.resolve()}")

    terraform_path = Path("gcp_architecture.tf")
    terraform_path.write_text(client.build_terraform_file(), encoding="utf-8")
    print(f"Saved Terraform architecture to: {terraform_path.resolve()}")


if __name__ == "__main__":
    main()