import json
import os
import re
from collections import Counter, defaultdict
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from neo4j import GraphDatabase
from dotenv import load_dotenv


AWS_PRICING_BASE = "https://pricing.us-east-1.amazonaws.com"
AWS_PRICING_INDEX = f"{AWS_PRICING_BASE}/offers/v1.0/aws/index.json"

DEFAULT_MAX_SERVICES = 0
DEFAULT_MAX_PRODUCTS_PER_SERVICE = 2500
DEFAULT_MAX_VARIANTS_PER_DIMENSION = 6
DEFAULT_START_INDEX = 0
DEFAULT_START_SERVICE_CODE = ""

IMPORTANT_POSITIVE_TOKENS = {
    "standard": 1.6,
    "general": 1.3,
    "gp3": 2.0,
    "on_demand": 1.8,
    "serverless": 1.7,
    "managed": 1.4,
    "fargate": 1.6,
    "postgresql": 1.9,
    "mysql": 1.5,
    "redis": 1.4,
    "http": 1.2,
    "internet_facing": 1.1,
    "public": 1.0,
    "multi_az": 1.6,
    "intelligent_tiering": 1.8,
}

IMPORTANT_NEGATIVE_TOKENS = {
    "legacy": -2.4,
    "previous": -1.6,
    "deprecated": -2.5,
    "old": -1.2,
    "sc1": -0.8,
    "gp2": -0.7,
    "cold": -0.6,
    "deep_archive": -1.1,
    "oracle": -0.4,
    "x86_64": -0.1,
}


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "unknown"


def fetch_json(url: str, timeout: int = 45) -> dict:
    request = Request(url, headers={"User-Agent": "MigrAI-AWS-Graph-Loader/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def infer_category(service_code: str) -> str:
    code = service_code.lower()
    if any(token in code for token in ["s3", "storage", "backup", "fsx", "efs", "ebs"]):
        return "Storage"
    if any(token in code for token in ["ec2", "lambda", "compute", "ecs", "eks", "batch"]):
        return "Compute"
    if any(token in code for token in ["rds", "dynamodb", "redshift", "db", "database", "neptune"]):
        return "Databases"
    if any(token in code for token in ["vpc", "route53", "apigateway", "cloudfront", "elb"]):
        return "Networking"
    if any(token in code for token in ["iam", "kms", "security", "waf", "guardduty", "secrets"]):
        return "Security"
    if any(token in code for token in ["cloudwatch", "cloudtrail", "xray", "config"]):
        return "Observability"
    if any(token in code for token in ["sqs", "sns", "kinesis", "msk", "mq", "eventbridge"]):
        return "Integration"
    if any(token in code for token in ["athena", "glue", "emr", "analytics", "quicksight"]):
        return "Analytics"
    return "Other"


def create_schema(session) -> None:
    session.run(
        "CREATE CONSTRAINT aws_provider_name_unique IF NOT EXISTS "
        "FOR (p:CloudProvider) REQUIRE p.name IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT aws_category_name_unique IF NOT EXISTS "
        "FOR (c:ServiceCategory) REQUIRE c.name IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT aws_service_id_unique IF NOT EXISTS "
        "FOR (s:Service) REQUIRE s.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT aws_variant_id_unique IF NOT EXISTS "
        "FOR (v:ServiceVariant) REQUIRE v.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT aws_region_code_unique IF NOT EXISTS "
        "FOR (r:AwsRegion) REQUIRE r.code IS UNIQUE"
    )


def upsert_service(session, service_code: str, offer_url: str) -> str:
    service_id = f"aws.{slugify(service_code)}"
    category = infer_category(service_code)
    session.run(
        """
        MERGE (aws:CloudProvider {name: 'AWS'})
        SET aws.source = $index_url
        MERGE (c:ServiceCategory {name: $category})
        MERGE (aws)-[:HAS_CATEGORY]->(c)
        MERGE (s:Service {id: $service_id})
        SET s.name = $service_name,
            s.providerCode = $service_code,
            s.officialSource = $offer_url,
            s.basePriority = coalesce(s.basePriority, 0.5)
        MERGE (aws)-[:HAS_SERVICE]->(s)
        MERGE (s)-[:IN_CATEGORY]->(c)
        """,
        {
            "index_url": AWS_PRICING_INDEX,
            "category": category,
            "service_id": service_id,
            "service_name": service_code,
            "service_code": service_code,
            "offer_url": offer_url,
        },
    )
    return service_id


def upsert_regions(session, service_id: str, region_index_url: str) -> int:
    try:
        region_data = fetch_json(region_index_url)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return 0

    regions = region_data.get("regions", {})
    count = 0
    for region_code in regions.keys():
        session.run(
            """
            MATCH (s:Service {id: $service_id})
            MERGE (r:AwsRegion {code: $region_code})
            MERGE (s)-[:AVAILABLE_IN]->(r)
            """,
            {"service_id": service_id, "region_code": region_code},
        )
        count += 1
    return count


def extract_variants_from_offer(offer_data: dict, max_products: int) -> dict[str, Counter]:
    blocked_dimensions = {
        "servicecode",
        "servicename",
        "sku",
        "operation",
        "location",
        "locationtype",
        "regioncode",
        "usagetype",
        "group",
        "groupdescription",
        "fromlocation",
        "tolocation",
        "fromlocationtype",
        "tolocationtype",
        "fromregioncode",
        "toregioncode",
    }

    counters = defaultdict(Counter)
    scanned = 0
    for product in offer_data.get("products", {}).values():
        attributes = product.get("attributes", {})
        for key, raw_value in attributes.items():
            normalized_key = key.lower().strip()
            if normalized_key in blocked_dimensions:
                continue

            value = str(raw_value).strip()
            if not value or value.lower() in {"na", "n/a", "none", "null"}:
                continue
            if len(value) > 80:
                continue

            counters[normalized_key][value] += 1

        scanned += 1
        if scanned >= max_products:
            break

    return counters


def normalize_token_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def score_variant_value(dimension: str, value: str, frequency: int) -> float:
    normalized = normalize_token_text(value)
    score = 0.0

    # Frequency still matters, but we bias toward meaningful production variants.
    score += frequency * 0.35

    if "version" in dimension:
        # Avoid over-biasing toward old versions that may dominate frequency.
        score -= 0.2

    for token, weight in IMPORTANT_POSITIVE_TOKENS.items():
        if token in normalized:
            score += weight

    for token, weight in IMPORTANT_NEGATIVE_TOKENS.items():
        if token in normalized:
            score += weight

    # Light preference for shorter, cleaner values over noisy strings.
    score += max(0, 24 - len(normalized)) * 0.01
    return score


def pick_important_variants(counter: Counter, dimension: str, limit: int) -> list[tuple[str, int]]:
    scored = []
    for value, freq in counter.items():
        scored.append((value, freq, score_variant_value(dimension, value, freq)))

    scored.sort(key=lambda item: (item[2], item[1]), reverse=True)
    selected = [(value, freq) for value, freq, _ in scored[:limit]]
    return selected


def upsert_variants(
    session,
    service_id: str,
    variant_counters: dict[str, Counter],
    max_variants_per_dimension: int,
) -> int:
    inserted = 0
    for dimension, counter in variant_counters.items():
        if len(counter) < 2:
            continue

        top_values = pick_important_variants(counter, dimension, max_variants_per_dimension)
        denominator = max(sum(counter.values()), 1)

        for value, freq in top_values:
            variant_id = f"{service_id}.{slugify(dimension)}.{slugify(value)[:48]}"
            priority = round(freq / denominator, 6)
            session.run(
                """
                MATCH (s:Service {id: $service_id})
                MERGE (v:ServiceVariant {id: $variant_id})
                SET v.name = $variant_name,
                    v.dimension = $dimension,
                    v.configuration = $configuration,
                    v.priority = $priority,
                    v.source = $source
                MERGE (s)-[:HAS_VARIANT]->(v)
                """,
                {
                    "service_id": service_id,
                    "variant_id": variant_id,
                    "variant_name": f"{dimension}={value}",
                    "dimension": dimension,
                    "configuration": value,
                    "priority": priority,
                    "source": "aws-pricing-offer-attributes",
                },
            )
            inserted += 1
    return inserted


def run_import() -> None:
    load_dotenv()

    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    max_services = int(os.getenv("AWS_IMPORT_MAX_SERVICES", DEFAULT_MAX_SERVICES))
    max_products = int(os.getenv("AWS_IMPORT_MAX_PRODUCTS_PER_SERVICE", DEFAULT_MAX_PRODUCTS_PER_SERVICE))
    max_variants = int(os.getenv("AWS_IMPORT_MAX_VARIANTS_PER_DIMENSION", DEFAULT_MAX_VARIANTS_PER_DIMENSION))
    start_index = int(os.getenv("AWS_IMPORT_START_INDEX", DEFAULT_START_INDEX))
    start_service_code = os.getenv("AWS_IMPORT_START_SERVICE_CODE", DEFAULT_START_SERVICE_CODE).strip()

    if not neo4j_password:
        raise ValueError("NEO4J_PASSWORD is required in .env")

    pricing_index = fetch_json(AWS_PRICING_INDEX)
    offers = pricing_index.get("offers", {})
    all_service_items = sorted(offers.items(), key=lambda item: item[0])
    total_services = len(all_service_items)

    if start_service_code:
        found_index = None
        for idx, (_, data) in enumerate(all_service_items):
            service_code = data.get("offerCode")
            if service_code == start_service_code:
                found_index = idx
                break
        if found_index is not None:
            start_index = found_index
        else:
            print(f"[WARN] AWS_IMPORT_START_SERVICE_CODE not found: {start_service_code}")

    if start_index < 0:
        start_index = 0
    if start_index >= total_services:
        raise ValueError(
            f"AWS_IMPORT_START_INDEX ({start_index}) is out of range for {total_services} services"
        )

    service_items = all_service_items[start_index:]

    if max_services > 0:
        service_items = service_items[:max_services]

    print(
        "[INFO] Import configuration: "
        f"start_index={start_index}, "
        f"start_service_code={start_service_code or 'N/A'}, "
        f"selected_services={len(service_items)}, "
        f"total_services={total_services}",
        flush=True,
    )

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    service_count = 0
    region_count = 0
    variant_count = 0
    try:
        with driver.session() as session:
            create_schema(session)

            for local_index, (service_key, data) in enumerate(service_items, start=1):
                global_index = start_index + local_index
                service_code = data.get("offerCode", service_key)
                current_version_url = data.get("currentVersionUrl")
                region_index_suffix = data.get("currentRegionIndexUrl")

                print(
                    f"[STEP] local {local_index}/{len(service_items)} "
                    f"global {global_index}/{total_services} starting: {service_code}",
                    flush=True,
                )

                if not current_version_url:
                    print(f"[WARN] Skipping {service_code}: missing currentVersionUrl", flush=True)
                    continue

                offer_url = f"{AWS_PRICING_BASE}{current_version_url}"
                print(f"[STEP] {service_code}: upserting service node", flush=True)
                service_id = upsert_service(session, service_code, offer_url)
                service_count += 1

                if region_index_suffix:
                    print(f"[STEP] {service_code}: fetching/upserting regions", flush=True)
                    regions_added = upsert_regions(session, service_id, f"{AWS_PRICING_BASE}{region_index_suffix}")
                    region_count += regions_added
                    print(f"[STEP] {service_code}: regions added={regions_added}", flush=True)

                try:
                    print(f"[STEP] {service_code}: downloading offer document", flush=True)
                    offer_data = fetch_json(offer_url)
                    print(f"[STEP] {service_code}: extracting important variants", flush=True)
                    counters = extract_variants_from_offer(offer_data, max_products=max_products)
                    variant_count += upsert_variants(
                        session,
                        service_id,
                        counters,
                        max_variants_per_dimension=max_variants,
                    )
                except (HTTPError, URLError, TimeoutError, ValueError) as err:
                    print(f"[WARN] Could not fetch variants for {service_code}: {err}", flush=True)

                print(
                    f"[OK] local {local_index}/{len(service_items)} "
                    f"global {global_index}/{total_services} imported: {service_code}",
                    flush=True,
                )

    finally:
        driver.close()

    print("\nAWS graph import completed from official AWS pricing endpoints.", flush=True)
    print(f"Services imported: {service_count}", flush=True)
    print(f"Region relationships created: {region_count}", flush=True)
    print(f"Service variants created: {variant_count}", flush=True)


def main() -> None:
    run_import()


if __name__ == "__main__":
    main()
