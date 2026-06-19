import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from neo4j import GraphDatabase


OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


DEFAULT_GCP_SERVICE_KEYS = [
    "google_compute_instance",
    "google_storage_bucket",
    "google_sql_database_instance",
    "google_pubsub_topic",
    "google_cloudfunctions2_function",
    "google_cloud_run_service",
    "google_compute_network",
    "google_dns_managed_zone",
    "google_secret_manager_secret",
    "google_monitoring_alert_policy",
]


@dataclass
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str | None = None


def extract_resource_blocks(tf_text: str) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    pattern = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')

    pos = 0
    while True:
        match = pattern.search(tf_text, pos)
        if not match:
            break

        resource_type = match.group(1)
        resource_name = match.group(2)
        brace_start = tf_text.find("{", match.end() - 1)
        if brace_start == -1:
            pos = match.end()
            continue

        depth = 0
        idx = brace_start
        while idx < len(tf_text):
            ch = tf_text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            idx += 1

        body = tf_text[brace_start + 1 : idx] if idx < len(tf_text) else ""
        resources.append(
            {
                "type": resource_type,
                "name": resource_name,
                "body": body,
            }
        )
        pos = idx + 1

    return resources


def parse_input_tf(tf_path: str) -> dict[str, dict[str, Any]]:
    if not os.path.exists(tf_path):
        return {}

    with open(tf_path, "r", encoding="utf-8") as f:
        tf_text = f.read()

    parsed: dict[str, dict[str, Any]] = {}
    for res in extract_resource_blocks(tf_text):
        body = res["body"]
        attrs: dict[str, Any] = {
            "resource_name": res["name"],
        }

        machine_type = re.search(r'machine_type\s*=\s*"([^"]+)"', body)
        if machine_type:
            attrs["machine_type"] = machine_type.group(1)

        zone = re.search(r'zone\s*=\s*"([^"]+)"', body)
        if zone:
            attrs["zone"] = zone.group(1)

        region = re.search(r'region\s*=\s*"([^"]+)"', body)
        if region:
            attrs["region"] = region.group(1)

        db_version = re.search(r'database_version\s*=\s*"([^"]+)"', body)
        if db_version:
            attrs["database_version"] = db_version.group(1)

        tier = re.search(r'tier\s*=\s*"([^"]+)"', body)
        if tier:
            attrs["tier"] = tier.group(1)

        bucket_location = re.search(r'location\s*=\s*"([^"]+)"', body)
        if bucket_location:
            attrs["location"] = bucket_location.group(1)

        storage_class = re.search(r'storage_class\s*=\s*"([^"]+)"', body)
        if storage_class:
            attrs["storage_class"] = storage_class.group(1)

        disk_size = re.search(r'size\s*=\s*(\d+)', body)
        if disk_size:
            attrs["disk_size_gb"] = int(disk_size.group(1))

        disk_type = re.search(r'type\s*=\s*"([^"]+)"', body)
        if disk_type:
            attrs["disk_type"] = disk_type.group(1)

        parsed[res["type"]] = attrs

    return parsed


def _ec2_size_from_gcp_machine_type(machine_type: str | None) -> tuple[str, str]:
    if not machine_type:
        return "t3", "micro"

    mt = machine_type.lower()
    explicit_map = {
        "f1-micro": ("t3", "micro"),
        "g1-small": ("t3", "small"),
        "n1-standard-1": ("t3", "small"),
        "n1-standard-2": ("t3", "medium"),
        "n1-standard-4": ("t3", "xlarge"),
        "e2-medium": ("t3", "medium"),
        "e2-standard-2": ("t3", "medium"),
        "e2-standard-4": ("m6i", "xlarge"),
    }
    if mt in explicit_map:
        return explicit_map[mt]

    if "micro" in mt:
        return "t3", "micro"
    if "small" in mt:
        return "t3", "small"
    if "medium" in mt:
        return "t3", "medium"
    if "standard-2" in mt:
        return "t3", "medium"
    if "standard-4" in mt:
        return "m6i", "xlarge"
    return "t3", "micro"


def input_config_for_service(gcp_service_key: str, attrs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    config: dict[str, Any] = {}
    sources: dict[str, str] = {}

    if gcp_service_key == "google_compute_instance":
        family, size = _ec2_size_from_gcp_machine_type(attrs.get("machine_type"))
        config.update(
            {
                "instance_family": family,
                "instance_size": size,
                "purchase_model": "on_demand",
            }
        )
        sources.update(
            {
                "instance_family": "input_tf.machine_type",
                "instance_size": "input_tf.machine_type",
                "purchase_model": "rule.default",
            }
        )

        if attrs.get("disk_size_gb"):
            config["root_volume_size_gb"] = attrs["disk_size_gb"]
            sources["root_volume_size_gb"] = "input_tf.disk_size_gb"
        if attrs.get("disk_type"):
            dt = str(attrs["disk_type"]).lower()
            config["root_volume_type"] = "gp3" if "ssd" in dt or "pd-balanced" in dt else "gp2"
            sources["root_volume_type"] = "input_tf.disk_type"

    elif gcp_service_key == "google_sql_database_instance":
        version = str(attrs.get("database_version", "POSTGRES_14")).lower()
        engine = "postgres"
        if "mysql" in version:
            engine = "mysql"
        elif "sqlserver" in version:
            engine = "sqlserver"

        instance_class = "db.t4g.micro"
        tier = str(attrs.get("tier", "")).lower()
        if "micro" in tier:
            instance_class = "db.t4g.micro"
        elif "small" in tier:
            instance_class = "db.t4g.small"
        elif "medium" in tier:
            instance_class = "db.t4g.medium"

        config.update(
            {
                "engine": engine,
                "instance_class": instance_class,
                "storage_type": "gp3",
                "multi_az": False,
            }
        )
        sources.update(
            {
                "engine": "input_tf.database_version",
                "instance_class": "input_tf.tier",
                "storage_type": "rule.default",
                "multi_az": "rule.default",
            }
        )

    elif gcp_service_key == "google_storage_bucket":
        location = str(attrs.get("location", "")).upper()
        access_pattern = "hot"
        if location in {"US", "EU", "ASIA"}:
            access_pattern = "global_multi_region"

        config.update(
            {
                "storage_class": attrs.get("storage_class", "STANDARD"),
                "versioning": True,
                "encryption": "SSE-S3",
                "access_pattern": access_pattern,
            }
        )
        sources.update(
            {
                "storage_class": "input_tf.storage_class|rule.default",
                "versioning": "rule.default",
                "encryption": "rule.default",
                "access_pattern": "input_tf.location|rule.default",
            }
        )

    elif gcp_service_key == "google_compute_network":
        config.update(
            {
                "cidr_block": "10.0.0.0/16",
                "az_count": 2,
                "nat_strategy": "single_nat_gateway",
            }
        )
        sources.update(
            {
                "cidr_block": "rule.default",
                "az_count": "rule.default",
                "nat_strategy": "rule.default",
            }
        )

    return config, sources


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def clean_gcp_name(service_key: str, raw_name: str | None) -> str:
    name = (raw_name or "").strip()
    if not name or re.fullmatch(r"[-_\s]+", name):
        return service_key
    return name


def sanitize(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    while cleaned.startswith("="):
        cleaned = cleaned[1:].strip()
    return cleaned or None


def load_neo4j_config(prefix: str, fallback_prefix: str | None = None) -> Neo4jConfig:
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
        raise ValueError(f"Missing {prefix}_URI/{prefix}_USER/{prefix}_PASSWORD in .env")

    return Neo4jConfig(uri=uri, user=user, password=password, database=database or None)


def load_neo4j_driver(config: Neo4jConfig) -> GraphDatabase.driver:
    load_dotenv()
    return GraphDatabase.driver(config.uri, auth=(config.user, config.password))


def fetch_aws_catalog(driver: GraphDatabase.driver, database: str | None) -> list[dict[str, Any]]:
    query = """
    MATCH (s:Service)
    OPTIONAL MATCH (s)-[:IN_CATEGORY]->(c:ServiceCategory)
    OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:ServiceVariant)
    WITH s, c, collect(v)[0..5] AS variants
    RETURN s.id AS id,
           s.name AS name,
           s.providerCode AS providerCode,
           coalesce(s.basePriority, 0.5) AS basePriority,
           coalesce(c.name, 'Other') AS category,
           [x IN variants | {
              id: x.id,
              name: x.name,
              dimension: x.dimension,
              priority: x.priority,
              configuration: x.configuration
           }] AS variants
    ORDER BY basePriority DESC, name ASC
    """

    session = driver.session(database=database) if database else driver.session()
    with session:
        rows = session.run(query)
        return [dict(row) for row in rows]


def fetch_gcp_context(
    driver: GraphDatabase.driver,
    database: str | None,
    gcp_service_keys: list[str],
) -> list[dict[str, Any]]:
    query = """
    MATCH (s:GCPService {key: $key})
    OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:GCPServiceVariant)
    OPTIONAL MATCH (s)-[:EVIDENCED_BY]->(d:DocPage)
    WITH s,
         collect(DISTINCT v)[0..8] AS variants,
         collect(DISTINCT d.title)[0..6] AS evidence_docs
    RETURN s.key AS key,
           s.name AS name,
           s.family AS family,
           s.priority AS priority,
           [x IN variants | {
              key: x.key,
              name: x.name,
              tier: x.tier,
              priority: x.priority
           }] AS variants,
           evidence_docs
    """

    results: list[dict[str, Any]] = []
    session = driver.session(database=database) if database else driver.session()
    with session:
        for key in gcp_service_keys:
            record = session.run(query, {"key": key}).single()
            if record:
                row = dict(record)
                row["name"] = clean_gcp_name(row["key"], row.get("name"))
                results.append(row)
            else:
                results.append(
                    {
                        "key": key,
                        "name": key,
                        "family": None,
                        "priority": None,
                        "variants": [],
                        "evidence_docs": [],
                    }
                )

    return results


def build_prompt(gcp_services: list[dict[str, Any]], aws_catalog: list[dict[str, Any]]) -> str:
    catalog_for_prompt = [
        {
            "id": x["id"],
            "name": x["name"],
            "providerCode": x.get("providerCode"),
            "category": x["category"],
            "basePriority": x["basePriority"],
            "variants": [
                {
                    "name": v.get("name"),
                    "dimension": v.get("dimension"),
                    "priority": v.get("priority"),
                }
                for v in x.get("variants", [])
            ],
        }
        for x in aws_catalog
    ]

    return (
        "You are a cloud migration mapper.\n"
        "Map each GCP service to the most suitable AWS service using only the provided AWS catalog and GCP evidence context.\n"
        "Use semantic reasoning from service purpose and evidence docs.\n"
        "Do NOT do naive category-only mapping.\n"
        "Prefer higher basePriority/variant priority only after semantic fit is satisfied.\n"
        "Return strict JSON array only, with objects having keys:\n"
        "gcp_service_key, gcp_service_name, aws_service_id, aws_service_name, suggested_variant, confidence, rationale, target_configuration.\n"
        "target_configuration must be an object with concrete keys based on service type, e.g.\n"
        "EC2: instance_family, instance_size, purchase_model, root_volume_type, root_volume_size_gb\n"
        "RDS: engine, edition, instance_class, storage_type, multi_az\n"
        "S3: storage_class, versioning, encryption, access_pattern\n"
        "Lambda: runtime, architecture, memory_mb, timeout_sec\n"
        "VPC: cidr_block, az_count, nat_strategy\n\n"
        f"GCP services:\n{json.dumps(gcp_services, indent=2)}\n\n"
        f"AWS catalog from Neo4j:\n{json.dumps(catalog_for_prompt, indent=2)}"
    )


def call_openai(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required in .env")

    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are an expert cloud migration architect."},
            {"role": "user", "content": prompt},
        ],
    }

    request = Request(
        OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(request, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8"))

    return body["choices"][0]["message"]["content"]


def try_parse_json(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    return json.loads(text)


def heuristic_fallback(gcp_services: list[dict[str, Any]], aws_catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = {normalize(x["name"]): x for x in aws_catalog}

    def pick_by_name(candidates: list[str]) -> dict[str, Any] | None:
        for c in candidates:
            if normalize(c) in lookup:
                return lookup[normalize(c)]
        return None

    fallback_by_key_pattern = [
        (r"compute_instance", ["Amazon EC2"]),
        (r"storage_bucket", ["Amazon S3"]),
        (r"sql_database|cloudsql|alloydb", ["Amazon RDS", "Amazon Aurora"]),
        (r"pubsub|scheduler|eventarc|cloud_tasks", ["Amazon SNS", "Amazon SQS", "Amazon EventBridge"]),
        (r"cloudfunctions|cloud_function", ["AWS Lambda"]),
        (r"cloud_run|container_cluster|container_node_pool", ["Amazon ECS", "Amazon EKS"]),
        (r"compute_network|vpc", ["Amazon VPC"]),
        (r"dns_managed_zone", ["Amazon Route 53"]),
        (r"secret_manager|kms", ["AWS Secrets Manager"]),
        (r"monitoring|logging", ["Amazon CloudWatch"]),
    ]

    results: list[dict[str, Any]] = []
    for gcp in gcp_services:
        gcp_name = gcp.get("name") or gcp.get("key")
        gcp_key = gcp.get("key") or gcp_name

        candidates: list[str] = []
        for pattern, mapped in fallback_by_key_pattern:
            if re.search(pattern, gcp_key):
                candidates = mapped
                break

        if not candidates:
            candidates = []

        service = pick_by_name(candidates)
        if service is None and aws_catalog:
            service = aws_catalog[0]

        variant_name = None
        if service and service.get("variants"):
            sorted_variants = sorted(
                service["variants"],
                key=lambda x: x.get("priority") or 0,
                reverse=True,
            )
            if sorted_variants:
                variant_name = sorted_variants[0].get("name")

        results.append(
            {
                "gcp_service_key": gcp_key,
                "gcp_service_name": gcp_name,
                "aws_service_id": service["id"] if service else None,
                "aws_service_name": service["name"] if service else None,
                "suggested_variant": variant_name,
                "confidence": 0.35,
                "rationale": "Heuristic fallback using Neo4j AWS catalog.",
                "target_configuration": {},
            }
        )

    return results


def enrich_with_db_evidence(
    driver: GraphDatabase.driver,
    database: str | None,
    mapping: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    query = """
    MATCH (s:Service {id: $service_id})
    OPTIONAL MATCH (s)-[:IN_CATEGORY]->(c:ServiceCategory)
    OPTIONAL MATCH (s)-[:HAS_VARIANT]->(v:ServiceVariant)
    RETURN s.id AS id,
           s.name AS name,
           coalesce(c.name, 'Other') AS category,
           count(v) AS variantCount,
           max(v.priority) AS bestVariantPriority
    """

    enriched = []
    session = driver.session(database=database) if database else driver.session()
    with session:
        for item in mapping:
            service_id = item.get("aws_service_id")
            if not service_id:
                item["db_evidence"] = {"found": False}
                enriched.append(item)
                continue

            record = session.run(query, {"service_id": service_id}).single()
            if not record:
                item["db_evidence"] = {"found": False}
            else:
                item["db_evidence"] = {
                    "found": True,
                    "service": record["name"],
                    "category": record["category"],
                    "variantCount": record["variantCount"],
                    "bestVariantPriority": record["bestVariantPriority"],
                }
            enriched.append(item)

    return enriched


def apply_target_configuration_defaults(mapping: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def defaults_for_service(aws_id: str, aws_name: str) -> dict[str, Any]:
        text = f"{aws_id} {aws_name}".lower()
        if "ec2" in text:
            return {
                "instance_family": "t3",
                "instance_size": "micro",
                "purchase_model": "on_demand",
                "root_volume_type": "gp3",
                "root_volume_size_gb": 30,
            }
        if "rds" in text or "aurora" in text:
            return {
                "engine": "postgres",
                "edition": "standard",
                "instance_class": "db.t4g.micro",
                "storage_type": "gp3",
                "multi_az": False,
            }
        if "s3" in text:
            return {
                "storage_class": "STANDARD",
                "versioning": True,
                "encryption": "SSE-S3",
                "access_pattern": "hot",
            }
        if "lambda" in text:
            return {
                "runtime": "python3.12",
                "architecture": "x86_64",
                "memory_mb": 512,
                "timeout_sec": 30,
            }
        if "vpc" in text:
            return {
                "cidr_block": "10.0.0.0/16",
                "az_count": 2,
                "nat_strategy": "single_nat_gateway",
            }
        if "cloudwatch" in text:
            return {
                "log_retention_days": 30,
                "alarm_sensitivity": "medium",
            }
        if "secrets" in text:
            return {
                "rotation_enabled": True,
                "rotation_days": 30,
                "kms_key": "aws/secretsmanager",
            }
        return {
            "configuration_note": "No default config template available for this AWS service yet."
        }

    for item in mapping:
        aws_id = item.get("aws_service_id") or ""
        aws_name = item.get("aws_service_name") or ""
        cfg = item.get("target_configuration")
        if isinstance(cfg, dict) and cfg:
            continue
        item["target_configuration"] = defaults_for_service(aws_id, aws_name)

    return mapping


def apply_input_bound_config(
    mapping: list[dict[str, Any]],
    parsed_input: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    for item in mapping:
        key = item.get("gcp_service_key")
        if not key or key not in parsed_input:
            continue

        derived_cfg, source_map = input_config_for_service(key, parsed_input[key])
        if not derived_cfg:
            continue

        current_cfg = item.get("target_configuration")
        if not isinstance(current_cfg, dict):
            current_cfg = {}

        current_cfg.update(derived_cfg)
        item["target_configuration"] = current_cfg
        item["target_configuration_sources"] = source_map

    return mapping


def main() -> None:
    load_dotenv()

    gcp_cfg = load_neo4j_config("GCP_GRAPH_NEO4J", fallback_prefix="NEO4J")
    aws_cfg = load_neo4j_config("AWS_GRAPH_NEO4J")

    gcp_driver = load_neo4j_driver(gcp_cfg)
    aws_driver = load_neo4j_driver(aws_cfg)

    input_tf_path = sanitize(os.getenv("MIGRAI_INPUT_TF")) or "sample_input.tf"
    parsed_input = parse_input_tf(input_tf_path)

    keys_env = sanitize(os.getenv("MIGRAI_GCP_SERVICE_KEYS"))
    if keys_env:
        gcp_service_keys = [x.strip() for x in keys_env.split(",") if x.strip()]
    elif parsed_input:
        gcp_service_keys = list(parsed_input.keys())
    else:
        gcp_service_keys = DEFAULT_GCP_SERVICE_KEYS

    try:
        gcp_services = fetch_gcp_context(gcp_driver, gcp_cfg.database, gcp_service_keys)
        aws_catalog = fetch_aws_catalog(aws_driver, aws_cfg.database)
        if not aws_catalog:
            raise ValueError("No AWS services found in Neo4j. Import AWS data first.")

        print(f"Loaded {len(gcp_services)} GCP services from Neo4j.")
        print(f"Loaded {len(aws_catalog)} AWS services from Neo4j.")

        prompt = build_prompt(gcp_services, aws_catalog)

        try:
            llm_text = call_openai(prompt)
            mapping = try_parse_json(llm_text)
            print("OpenAI mapping completed.")
        except (ValueError, KeyError, json.JSONDecodeError, HTTPError, URLError, TimeoutError) as err:
            print(f"OpenAI mapping failed, using fallback: {err}")
            mapping = heuristic_fallback(gcp_services, aws_catalog)

        final_mapping = enrich_with_db_evidence(aws_driver, aws_cfg.database, mapping)
        final_mapping = apply_target_configuration_defaults(final_mapping)
        final_mapping = apply_input_bound_config(final_mapping, parsed_input)

        print("\n=== GCP -> AWS Mapping (DB-backed) ===")
        print(json.dumps(final_mapping, indent=2))

    finally:
        gcp_driver.close()
        aws_driver.close()


if __name__ == "__main__":
    main()
