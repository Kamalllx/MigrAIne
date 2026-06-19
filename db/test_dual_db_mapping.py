import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv
from neo4j import GraphDatabase


@dataclass
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str | None = None


def _cfg_from_env(prefix: str, fallback_prefix: str | None = None) -> Neo4jConfig:
    def sanitize(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        while cleaned.startswith("="):
            cleaned = cleaned[1:].strip()
        return cleaned or None

    def pick(key: str) -> str | None:
        value = sanitize(os.getenv(f"{prefix}_{key}"))
        if value:
            return value
        if fallback_prefix:
            return sanitize(os.getenv(f"{fallback_prefix}_{key}"))
        return None

    uri = pick("URI")
    user = pick("USER")
    password = pick("PASSWORD")
    database = pick("DATABASE")

    if not uri or not user or not password:
        raise RuntimeError(
            f"Missing {prefix}_URI/{prefix}_USER/{prefix}_PASSWORD in .env"
        )

    return Neo4jConfig(uri=uri, user=user, password=password, database=database or None)


def _run_query(driver, database: str | None, query: str, **params):
    session = driver.session(database=database) if database else driver.session()
    with session as s:
        return [record.data() for record in s.run(query, **params)]


def _family_to_aws_category(family: str) -> str:
    mapping = {
        "compute": "Compute",
        "container": "Compute",
        "storage": "Storage",
        "database": "Databases",
        "network": "Networking",
        "security": "Security",
        "integration": "Integration",
        "analytics": "Analytics",
        "operations": "Observability",
        "logging": "Observability",
        "monitoring": "Observability",
        "devtools": "Other",
        "ai": "Analytics",
        "ml": "Analytics",
    }

    family_lower = (family or "").lower()
    for key, value in mapping.items():
        if key in family_lower:
            return value
    return "Other"


def _intent_for_gcp_service(service_key: str, family: str) -> dict:
    rules = [
        (r"compute_disk", {"category": "Storage", "hints": ["ebs", "elastic block"], "required_tokens": ["ebs", "block"], "why": "GCP persistent disk is block storage; EBS is the closest AWS primitive."}),
        (r"external_vpn_gateway|vpn_tunnel", {"category": "Networking", "hints": ["vpn", "site to site"], "required_tokens": ["vpn", "site", "tunnel"], "why": "GCP external VPN maps to AWS VPN connectivity services."}),
        (r"compute_instance", {"category": "Compute", "hints": ["ec2"], "required_tokens": ["ec2", "instance"], "why": "VM-to-VM mapping: Compute Engine instance to EC2."}),
        (r"storage_bucket", {"category": "Storage", "hints": ["s3"], "required_tokens": ["s3", "object"], "why": "Object storage mapping: GCS bucket to S3 bucket."}),
        (r"sql_database|cloudsql|alloydb", {"category": "Databases", "hints": ["rds", "aurora"], "required_tokens": ["rds", "aurora", "database"], "why": "Managed relational database mapping to RDS/Aurora."}),
        (r"pubsub|cloud_tasks|scheduler|eventarc", {"category": "Integration", "hints": ["sqs", "sns", "eventbridge"], "required_tokens": ["sqs", "sns", "eventbridge", "mq"], "why": "Messaging/eventing workloads map to SQS/SNS/EventBridge."}),
        (r"cloud_run|container_cluster|container_node_pool", {"category": "Compute", "hints": ["ecs", "eks", "fargate"], "required_tokens": ["ecs", "eks", "fargate", "container"], "why": "Container runtime/orchestration maps to ECS/EKS/Fargate."}),
        (r"cloud_function|cloudfunctions", {"category": "Compute", "hints": ["lambda"], "required_tokens": ["lambda", "function"], "why": "Function-as-a-service mapping to Lambda."}),
        (r"dns_managed_zone", {"category": "Networking", "hints": ["route53", "route 53"], "required_tokens": ["route53", "dns"], "why": "Managed DNS zone maps to Route 53."}),
        (r"secret_manager|kms", {"category": "Security", "hints": ["secrets manager", "kms"], "required_tokens": ["secret", "kms", "key"], "why": "Secret/key management maps to Secrets Manager/KMS."}),
        (r"monitoring|logging", {"category": "Observability", "hints": ["cloudwatch", "cloudtrail"], "required_tokens": ["cloudwatch", "cloudtrail", "log", "monitor"], "why": "Monitoring/logging workloads map to CloudWatch/CloudTrail."}),
    ]

    for pattern, intent in rules:
        if re.search(pattern, service_key):
            return intent

    return {
        "category": _family_to_aws_category(family),
        "hints": [],
        "required_tokens": [],
        "why": "Fallback to family-level category mapping.",
    }


def _filter_by_intent(rows: list[dict], required_tokens: list[str], hints: list[str]) -> list[dict]:
    if not required_tokens and not hints:
        return rows

    token_pool = [t.lower() for t in (required_tokens + hints)]
    kept = []
    for row in rows:
        text = f"{row['aws_id']} {row['aws_name']}".lower()
        if any(token in text for token in token_pool):
            kept.append(row)
    return kept


def _choose_gcp_variant(gcp_driver, gcp_db: str | None, service_key: str) -> dict:
    # Avoid VM-size leakage for non-instance resources.
    query = """
    MATCH (s:GCPService {key: $key})-[:HAS_VARIANT]->(v:GCPServiceVariant)
    WHERE NOT (
        v.tier = 'machine_type'
        AND NOT (s.key CONTAINS 'instance' OR s.key CONTAINS 'template' OR s.key CONTAINS 'node_pool' OR s.key CONTAINS 'autoscaler')
    )
    RETURN v.name AS variant_name, v.priority AS variant_priority, v.tier AS variant_tier
    ORDER BY v.priority DESC
    LIMIT 1
    """
    rows = _run_query(gcp_driver, gcp_db, query, key=service_key)
    if rows:
        return rows[0]
    return {"variant_name": "n/a", "variant_priority": None, "variant_tier": None}


def _manual_popularity_boost(aws_id: str, aws_name: str) -> float:
    text = f"{aws_id} {aws_name}".lower()
    boosts = {
        "amazonec2": 0.40,
        "ec2": 0.35,
        "s3": 0.25,
        "rds": 0.20,
        "lambda": 0.12,
        "vpc": 0.15,
        "dynamodb": 0.12,
        "cloudwatch": 0.04,
        "sqs": 0.12,
        "sns": 0.10,
        "eventbridge": 0.10,
        "eks": 0.08,
        "ecs": 0.10,
        "route53": 0.10,
    }
    return max((score for token, score in boosts.items() if token in text), default=0.0)


def _find_aws_candidates(aws_driver, aws_db: str | None, category: str, hints: list[str], required_tokens: list[str]) -> tuple[list[dict], str]:
    query_category = """
    MATCH (c:ServiceCategory {name: $category})<-[:IN_CATEGORY]-(s:Service)
    OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:ServiceVariant)
    WITH s, count(v) AS variant_count, coalesce(max(v.priority), 0.0) AS best_variant
    OPTIONAL MATCH (s)-[:AVAILABLE_IN]->(r:AwsRegion)
    WITH s, variant_count, best_variant, count(DISTINCT r) AS region_count
    RETURN s.id AS aws_id,
           s.name AS aws_name,
           coalesce(s.basePriority, 0.5) AS aws_priority,
           variant_count,
           best_variant,
           region_count
    """
    query_all = """
    MATCH (s:Service)
    OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:ServiceVariant)
    WITH s, count(v) AS variant_count, coalesce(max(v.priority), 0.0) AS best_variant
    OPTIONAL MATCH (s)-[:AVAILABLE_IN]->(r:AwsRegion)
    WITH s, variant_count, best_variant, count(DISTINCT r) AS region_count
    RETURN s.id AS aws_id,
           s.name AS aws_name,
           coalesce(s.basePriority, 0.5) AS aws_priority,
           variant_count,
           best_variant,
           region_count
    """

    rows = _run_query(aws_driver, aws_db, query_category, category=category)

    def score_rows(source_rows: list[dict], active_hints: list[str], use_variant_signal: bool) -> list[dict]:
        scored_rows = []
        for row in source_rows:
            text = f"{row['aws_id']} {row['aws_name']}".lower()
            hint_match = 0.0
            if active_hints:
                matched = sum(1 for h in active_hints if h.lower() in text)
                hint_match = matched / len(active_hints)

            variant_signal = (row.get("best_variant") or 0.0) if use_variant_signal else 0.0
            popularity = (
                (row["aws_priority"] or 0.5) * 0.45
                + variant_signal * 0.05
                + (min(row.get("region_count") or 0, 30) / 30.0) * 0.20
                + _manual_popularity_boost(row["aws_id"], row["aws_name"]) * 0.20
                + hint_match * 0.10
            )
            row["match_score"] = round(popularity, 4)
            row["hint_match"] = round(hint_match, 4)
            scored_rows.append(row)

        scored_rows.sort(key=lambda x: x["match_score"], reverse=True)
        return scored_rows

    filtered_rows = _filter_by_intent(rows, required_tokens, hints)
    scored = score_rows(filtered_rows if filtered_rows else rows, hints, use_variant_signal=True)
    if scored and any(item["hint_match"] > 0 for item in scored[:10]):
        return scored[:3], "category+hint"

    # If category search has no intent-compatible service, try global but keep strict intent filter.
    all_rows = _run_query(aws_driver, aws_db, query_all)
    filtered_global = _filter_by_intent(all_rows, required_tokens, hints)
    if filtered_global:
        scored_all = score_rows(filtered_global, hints, use_variant_signal=False)
        return scored_all[:3], "global+intent_filtered"

    if filtered_rows:
        return scored[:3], "category_only"
    return [], "no_intent_match"


def main() -> None:
    load_dotenv()

    # GCP graph can reuse existing NEO4J_* env if GCP_GRAPH_* is not present.
    gcp_cfg = _cfg_from_env("GCP_GRAPH_NEO4J", fallback_prefix="NEO4J")
    aws_cfg = _cfg_from_env("AWS_GRAPH_NEO4J")

    gcp_driver = GraphDatabase.driver(gcp_cfg.uri, auth=(gcp_cfg.user, gcp_cfg.password))
    aws_driver = GraphDatabase.driver(aws_cfg.uri, auth=(aws_cfg.user, aws_cfg.password))

    try:
        gcp_counts = _run_query(
            gcp_driver,
            gcp_cfg.database,
            "MATCH (s:GCPService) RETURN count(s) AS service_count",
        )[0]["service_count"]
        gcp_variants = _run_query(
            gcp_driver,
            gcp_cfg.database,
            "MATCH (v:GCPServiceVariant) RETURN count(v) AS variant_count",
        )[0]["variant_count"]

        aws_counts = _run_query(
            aws_driver,
            aws_cfg.database,
            "MATCH (s:Service) RETURN count(s) AS service_count",
        )[0]["service_count"]
        aws_variants = _run_query(
            aws_driver,
            aws_cfg.database,
            "MATCH (v:ServiceVariant) RETURN count(v) AS variant_count",
        )[0]["variant_count"]

        print("=== DB Health ===")
        print(f"GCP services={gcp_counts}, GCP variants={gcp_variants}")
        print(f"AWS services={aws_counts}, AWS variants={aws_variants}")

        gcp_top = _run_query(
            gcp_driver,
            gcp_cfg.database,
            """
            MATCH (s:GCPService)
            RETURN s.key AS key, s.name AS name, s.family AS family, s.priority AS priority
            ORDER BY s.priority DESC
            LIMIT 15
            """,
        )

        print("\n=== Retrieval + Mapping Test (Semantic + Popularity) ===")
        for service in gcp_top:
            key = service["key"]
            family = service.get("family") or ""
            intent = _intent_for_gcp_service(key, family)
            aws_category = intent["category"]

            top_variant = _choose_gcp_variant(gcp_driver, gcp_cfg.database, key)

            aws_candidates, mode = _find_aws_candidates(
                aws_driver,
                aws_cfg.database,
                aws_category,
                intent["hints"],
                intent.get("required_tokens", []),
            )

            print(f"\nGCP: {service['name']} ({key}) | family={family} | priority={service['priority']}")
            print(
                f"Top customization: {top_variant['variant_name']} "
                f"(priority={top_variant['variant_priority']}, tier={top_variant['variant_tier']})"
            )
            print(f"Mapped AWS category: {aws_category}")
            if mode == "global+intent_filtered":
                print(f"Rationale: {intent['why']} Specific AWS service was not present in category; used global intent-filtered fallback.")
            elif mode == "category_only":
                print(f"Rationale: {intent['why']} No strong service-name hint match in current AWS graph, so ranking used popularity signals.")
            elif mode == "no_intent_match":
                print(f"Rationale: {intent['why']} No intent-compatible AWS service exists in current AWS graph. This is a catalog coverage gap, not a ranking issue.")
            else:
                print(f"Rationale: {intent['why']} Used category + service-name hints + popularity signals.")
            if not aws_candidates:
                print("AWS candidates: none")
            else:
                for c in aws_candidates:
                    print(
                        f"  - {c['aws_name']} ({c['aws_id']}) "
                        f"score={c['match_score']} base={c['aws_priority']} "
                        f"bestVariant={c['best_variant']} regions={c['region_count']} hintMatch={c['hint_match']}"
                    )

        print("\nDone: retrieval and cross-DB mapping checks completed.")

    finally:
        gcp_driver.close()
        aws_driver.close()


if __name__ == "__main__":
    main()
