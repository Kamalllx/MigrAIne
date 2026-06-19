import json
from typing import Any, Dict, List


def detect_input_format(source_text: str) -> str:
    text = source_text.strip()
    if not text:
        return "unknown"

    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return "text"
        if isinstance(parsed, dict) and isinstance(parsed.get("resources"), list):
            return "cloud_project_json"
        return "json"

    if "resource \"google_" in text:
        return "terraform"

    return "text"


def _summarize_cloud_project(project: Dict[str, Any]) -> str:
    resources: List[Dict[str, Any]] = project.get("resources", [])
    lines: List[str] = []

    lines.append("CLOUD PROJECT JSON INPUT (normalized summary):")
    project_id = project.get("project_id") or (project.get("source") or {}).get("project_id")
    source_provider = project.get("provider") or (project.get("source") or {}).get("provider")
    target_provider = (project.get("target") or {}).get("provider")
    migration_id = project.get("migration_id")

    if migration_id:
        lines.append(f"migration_id={migration_id}")
    lines.append(f"project_id={project_id}")
    lines.append(f"source_provider={source_provider}")
    lines.append(f"target_provider={target_provider}")

    source_primary_region = (project.get("source") or {}).get("primary_region")
    target_primary_region = (project.get("target") or {}).get("primary_region")
    if source_primary_region or target_primary_region:
        lines.append(f"region_map={source_primary_region}->{target_primary_region}")

    lines.append(f"resource_count={len(resources)}")
    lines.append("resources:")

    for r in resources:
        spec = r.get("spec") or {}
        spec_bits = []
        for key in [
            "instance_type",
            "engine",
            "engine_version",
            "instance_class",
            "storage_class",
            "cidr_block",
            "runtime",
            "orchestrator",
            "queue_type",
            "topic_type",
        ]:
            value = spec.get(key)
            if value is not None:
                spec_bits.append(f"{key}={value}")

        resource_line = (
            f"- id={r.get('id')} name={r.get('name')} "
            f"source_type={r.get('source_type') or r.get('resource_type')} "
            f"target_type={r.get('target_resource_type')} "
            f"target_service={r.get('target_service_code')} "
            f"category={r.get('category')} "
            f"source_region={r.get('source_region') or r.get('region')} "
            f"target_region={r.get('target_region')} "
            f"exposure={r.get('private_or_public')}"
        )

        dep_count = len(r.get("dependencies") or [])
        if dep_count:
            resource_line += f" deps={dep_count}"

        if spec_bits:
            resource_line += " spec{" + ", ".join(spec_bits) + "}"
        lines.append(resource_line)

    constraints = project.get("migration_constraints") or project.get("global_constraints")
    if isinstance(constraints, dict) and constraints:
        lines.append("constraints:")
        for k in [
            "max_downtime_minutes",
            "budget_ceiling_usd",
            "cutover_strategy",
            "cutover_window",
        ]:
            if k in constraints:
                lines.append(f"- {k}={constraints.get(k)}")

    return "\n".join(lines)


def normalize_source_for_planner(source_text: str) -> str:
    fmt = detect_input_format(source_text)
    if fmt != "cloud_project_json":
        return source_text

    parsed = json.loads(source_text)
    return _summarize_cloud_project(parsed)
