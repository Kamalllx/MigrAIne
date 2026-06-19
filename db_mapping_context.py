import os
import re
from typing import Callable

from dotenv import load_dotenv
from neo4j import GraphDatabase


def _sanitize(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    while cleaned.startswith("="):
        cleaned = cleaned[1:].strip()
    return cleaned or None


def _pick(prefix: str, key: str, fallback_prefix: str | None = None) -> str | None:
    value = _sanitize(os.getenv(f"{prefix}_{key}"))
    if value:
        return value
    if fallback_prefix:
        return _sanitize(os.getenv(f"{fallback_prefix}_{key}"))
    return None


def _extract_gcp_resource_types(tf_content: str) -> list[str]:
    found = re.findall(r'resource\s+"(google_[a-z_]+)"\s+"[^"]+"', tf_content)
    seen = set()
    ordered = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _intent_category(service_key: str, family: str | None) -> str:
    key = service_key.lower()
    fam = (family or "").lower()
    if "disk" in key or "bucket" in key or "storage" in key:
        return "Storage"
    if "sql" in key or "spanner" in key or "firestore" in key or "bigtable" in key:
        return "Databases"
    if "vpn" in key or "dns" in key or "network" in key or "subnetwork" in key or "firewall" in key:
        return "Networking"
    if "pubsub" in key or "scheduler" in key or "eventarc" in key or "tasks" in key:
        return "Integration"
    if "secret" in key or "kms" in key or "service_account" in key:
        return "Security"
    if "logging" in key or "monitoring" in key:
        return "Observability"
    if "container" in key or "cloud_run" in key or "function" in key or "compute" in key:
        return "Compute"

    if "storage" in fam:
        return "Storage"
    if "database" in fam:
        return "Databases"
    if "network" in fam:
        return "Networking"
    if "integration" in fam:
        return "Integration"
    if "security" in fam:
        return "Security"
    if "observability" in fam:
        return "Observability"
    return "Compute"


def build_db_mapping_context(gcp_terraform: str, log: Callable[[str], None] | None = None) -> str:
    load_dotenv()
    logger = log or (lambda _: None)

    gcp_uri = _pick("GCP_GRAPH_NEO4J", "URI", fallback_prefix="NEO4J")
    gcp_user = _pick("GCP_GRAPH_NEO4J", "USER", fallback_prefix="NEO4J")
    gcp_password = _pick("GCP_GRAPH_NEO4J", "PASSWORD", fallback_prefix="NEO4J")
    gcp_db = _pick("GCP_GRAPH_NEO4J", "DATABASE")

    aws_uri = _pick("AWS_GRAPH_NEO4J", "URI", fallback_prefix="NEO4J")
    aws_user = _pick("AWS_GRAPH_NEO4J", "USER", fallback_prefix="NEO4J")
    aws_password = _pick("AWS_GRAPH_NEO4J", "PASSWORD", fallback_prefix="NEO4J")
    aws_db = _pick("AWS_GRAPH_NEO4J", "DATABASE")

    if not (aws_uri and aws_user and aws_password):
        logger("      ⚠️  DB mapping context unavailable (missing AWS Neo4j envs).")
        return ""

    gcp_enabled = bool(gcp_uri and gcp_user and gcp_password)
    if not gcp_enabled:
        logger("      ℹ️  GCP graph envs missing; using AWS-only mapping hints.")

    service_keys = _extract_gcp_resource_types(gcp_terraform)
    if not service_keys:
        return ""

    gcp_driver = GraphDatabase.driver(gcp_uri, auth=(gcp_user, gcp_password)) if gcp_enabled else None
    aws_driver = GraphDatabase.driver(aws_uri, auth=(aws_user, aws_password))

    lines = [
        "DB-BACKED GCP->AWS MAPPING HINTS (prioritize these over free-form guesses):",
    ]

    try:
        aws_session = aws_driver.session(database=aws_db) if aws_db else aws_driver.session()

        if gcp_enabled:
            gcp_session = gcp_driver.session(database=gcp_db) if gcp_db else gcp_driver.session()
            gcp_ctx = gcp_session
        else:
            gcp_ctx = None

        with aws_session as aws:
            for key in service_keys:
                gcp_row = None
                if gcp_ctx is not None:
                    gcp_row = gcp_ctx.run(
                        """
                        MATCH (s:GCPService {key: $key})
                        OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:GCPServiceVariant)
                        WITH s, v ORDER BY v.priority DESC
                        RETURN s.key AS key,
                               s.name AS name,
                               s.family AS family,
                               s.priority AS priority,
                               collect(v.name)[0] AS top_variant,
                               collect(v.tier)[0] AS top_variant_tier
                        """,
                        key=key,
                    ).single()

                category = _intent_category(key, gcp_row.get("family") if gcp_row else None)
                aws_candidates = list(
                    aws.run(
                        """
                        MATCH (c:ServiceCategory {name: $category})<-[:IN_CATEGORY]-(s:Service)
                        OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:ServiceVariant)
                        RETURN s.id AS id,
                               s.name AS name,
                               coalesce(s.basePriority, 0.5) AS base,
                               max(v.priority) AS top_variant_priority
                        ORDER BY base DESC, top_variant_priority DESC
                        LIMIT 3
                        """,
                        category=category,
                    )
                )

                candidate_text = "none"
                if aws_candidates:
                    rendered = []
                    for item in aws_candidates:
                        rendered.append(
                            f"{item['name']} ({item['id']}, base={item['base']})"
                        )
                    candidate_text = "; ".join(rendered)

                gcp_variant = gcp_row.get("top_variant") if gcp_row else "unknown"
                gcp_tier = gcp_row.get("top_variant_tier") if gcp_row else "unknown"

                lines.append(
                    f"- {key}: intent={category}; gcp_top_variant={gcp_variant} "
                    f"(tier={gcp_tier}); aws_candidates={candidate_text}"
                )

        if gcp_ctx is not None:
            gcp_ctx.close()

    finally:
        if gcp_driver is not None:
            gcp_driver.close()
        aws_driver.close()

    logger(f"      ✅ DB mapping hints prepared for {len(service_keys)} resource type(s).")
    return "\n".join(lines)
