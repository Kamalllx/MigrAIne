import os
import re
from datetime import datetime
from collections import defaultdict
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from neo4j import GraphDatabase

from gcp_official_sources import GCP_OFFICIAL_SOURCES


GITHUB_TREE_API = "https://api.github.com/repos/hashicorp/terraform-provider-google/git/trees/main?recursive=1"
RAW_BASE = "https://raw.githubusercontent.com/hashicorp/terraform-provider-google/main/"
CUSTOMIZATION_ATTRS = {
    "machine_type",
    "database_version",
    "tier",
    "edition",
    "storage_class",
    "runtime",
    "availability_type",
    "network_tier",
    "billing_mode",
}


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {message}")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_backtick_values(text: str) -> list[str]:
    return re.findall(r"`([a-zA-Z0-9_\-\.]+)`", text)


def _doc_paths_from_tree(tree: Iterable[dict], prefix: str) -> list[str]:
    doc_paths = []
    for item in tree:
        path = item.get("path", "")
        if not path.startswith(prefix):
            continue
        if path.endswith(".html.markdown") or path.endswith(".markdown") or path.endswith(".md"):
            doc_paths.append(path)
    return sorted(doc_paths)


def _normalize_resource_key(filename: str) -> str:
    base = filename.replace(".html.markdown", "").replace(".markdown", "").replace(".md", "")
    if base.startswith("google_"):
        return base
    return f"google_{base}"


def _family_from_resource_key(resource_key: str) -> str:
    parts = resource_key.split("_")
    return parts[1] if len(parts) > 1 else "misc"


def _download_tree(session: requests.Session) -> list[dict]:
    _log("Fetching Terraform provider repository tree...")
    response = session.get(GITHUB_TREE_API, timeout=30)
    response.raise_for_status()
    return response.json().get("tree", [])


def _fetch_doc_content(session: requests.Session, path: str) -> dict | None:
    raw_url = RAW_BASE + path
    raw = session.get(raw_url, timeout=30)
    if raw.status_code != 200:
        return None
    filename = path.split("/")[-1]
    resource_key = _normalize_resource_key(filename)
    content = raw.text
    title_match = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else resource_key
    return {
        "resource_key": resource_key,
        "title": title,
        "url": raw_url,
        "content": content,
    }


def fetch_gcp_product_catalog(session: requests.Session) -> list[dict]:
    _log("Downloading Google Cloud product catalog...")
    response = session.get("https://cloud.google.com/products", timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    products = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        text = _clean_text(anchor.get_text(" "))
        if not href.startswith("https://cloud.google.com") and not href.startswith("/"):
            continue
        if not text or len(text) < 3:
            continue
        if href.startswith("/"):
            href = "https://cloud.google.com" + href
        if "/products" not in href and not re.match(r"https://cloud.google.com/[a-z0-9-]+$", href):
            continue
        key = (text.lower(), href.lower())
        if key in seen:
            continue
        seen.add(key)
        products.append({"title": text, "url": href})

    _log(f"Collected {len(products)} product catalog entries.")
    return products


def infer_variants_from_doc(resource_key: str, content: str) -> list[dict]:
    variants = []
    seen = set()
    declared_attrs = set()

    def add_variant(attribute: str, value: str, position: int, source: str) -> None:
        key = f"{resource_key}::{attribute.lower()}::{value.lower()}"
        if key in seen:
            return
        seen.add(key)
        variants.append(
            {
                "variant_key": key,
                "name": value,
                "tier": attribute.lower(),
                "position": position,
                "source": source,
            }
        )

    current_attr = "general"
    attr_line = re.compile(r"^\s*[*-]\s+`([a-zA-Z0-9_\-\.]+)`\s+-")
    values_line = re.compile(
        r"possible values are|must be one of|accepted values are|valid values are|can be one of",
        flags=re.IGNORECASE,
    )

    for line in content.splitlines():
        attr_match = attr_line.search(line)
        if attr_match:
            current_attr = attr_match.group(1)
            declared_attrs.add(current_attr.lower())
            continue
        if values_line.search(line):
            extracted = _extract_backtick_values(line)
            for idx, value in enumerate(extracted, start=1):
                add_variant(current_attr, value, idx, "enum_line")

    enum_pattern = re.compile(
        r"Possible values are\s+([^\n]+(?:\n[^\n]+){0,2})",
        flags=re.IGNORECASE,
    )
    for match in enum_pattern.finditer(content):
        values = _extract_backtick_values(match.group(1))
        for idx, value in enumerate(values, start=1):
            add_variant("enum_option", value, idx, "enum_block")

    for attr in CUSTOMIZATION_ATTRS:
        if attr not in declared_attrs:
            continue
        if attr == "machine_type" and not any(
            token in resource_key for token in ["instance", "template", "node_pool", "autoscaler", "machine_image"]
        ):
            continue
        example_pattern = re.compile(rf'{attr}\s*=\s*"([^"]+)"')
        examples = example_pattern.findall(content)
        for idx, value in enumerate(examples, start=1):
            add_variant(attr, value, idx, "example_assignment")

    return variants


def _create_constraints(neo) -> None:
    neo.run("CREATE CONSTRAINT gcp_source_id IF NOT EXISTS FOR (s:DocSource) REQUIRE s.id IS UNIQUE")
    neo.run("CREATE CONSTRAINT gcp_doc_url IF NOT EXISTS FOR (d:DocPage) REQUIRE d.url IS UNIQUE")
    neo.run("CREATE CONSTRAINT gcp_service_key IF NOT EXISTS FOR (s:GCPService) REQUIRE s.key IS UNIQUE")
    neo.run("CREATE CONSTRAINT gcp_variant_key IF NOT EXISTS FOR (v:GCPServiceVariant) REQUIRE v.key IS UNIQUE")
    neo.run("CREATE CONSTRAINT gcp_family_name IF NOT EXISTS FOR (f:GCPServiceFamily) REQUIRE f.name IS UNIQUE")


def _service_priority(resource_key: str, family_counts: dict[str, int], max_count: int) -> int:
    family = _family_from_resource_key(resource_key)
    breadth_signal = family_counts.get(family, 1) / max_count
    return int(round(30 + (70 * breadth_signal)))


def seed_docs_graph(uri: str, username: str, password: str, database: str | None = None) -> None:
    session_http = requests.Session()
    session_http.headers.update({"User-Agent": "MigrAI-Docs-Ingest/1.0"})

    _log("Stage 1/6: Discovering document inventory...")
    tree = _download_tree(session_http)

    resource_paths = _doc_paths_from_tree(tree, "website/docs/r/")
    data_paths = _doc_paths_from_tree(tree, "website/docs/d/")

    max_resource = os.getenv("MIGRAI_MAX_RESOURCE_DOCS")
    max_data = os.getenv("MIGRAI_MAX_DATA_DOCS")
    if max_resource:
        resource_paths = resource_paths[: int(max_resource)]
    if max_data:
        data_paths = data_paths[: int(max_data)]

    family_counts = defaultdict(int)
    for path in resource_paths:
        family_counts[_family_from_resource_key(_normalize_resource_key(path.split("/")[-1]))] += 1
    max_count = max(family_counts.values()) if family_counts else 1

    _log(f"Inventory ready: {len(resource_paths)} resource docs, {len(data_paths)} data-source docs.")

    _log("Stage 2/6: Connecting to Neo4j and initializing schema...")
    driver = GraphDatabase.driver(uri, auth=(username, password))
    session_ctx = driver.session(database=database) if database else driver.session()

    variant_records = []
    value_freq = defaultdict(int)
    products_count = 0
    services_count = 0
    variants_count = 0
    data_docs_count = 0

    with session_ctx as neo:
        _create_constraints(neo)
        neo.run("MERGE (c:GCPCloud {key:'gcp'}) SET c.name='Google Cloud Platform', c.priority_mode='graph_inferred'")
        for source in GCP_OFFICIAL_SOURCES:
            neo.run(
                "MERGE (s:DocSource {id:$id}) "
                "SET s.name=$name, s.authority=$authority, s.url=$url",
                **source,
            )

        _log("Stage 3/6: Downloading product catalog and writing to DB...")
        products = fetch_gcp_product_catalog(session_http)
        for idx, product in enumerate(products, start=1):
            neo.run(
                "MERGE (d:DocPage {url:$url}) "
                "SET d.title=$title, d.doc_type='product_page' "
                "WITH d MATCH (s:DocSource {id:'gcp_products_catalog'}) "
                "MERGE (s)-[:PUBLISHES]->(d)",
                url=product["url"],
                title=product["title"],
            )
            products_count += 1
            if idx % 100 == 0:
                _log(f"  Product docs written: {idx}/{len(products)}")

        _log("Stage 4/6: Streaming Terraform resource docs -> Neo4j (live writes)...")
        for idx, path in enumerate(resource_paths, start=1):
            doc = _fetch_doc_content(session_http, path)
            if not doc:
                continue

            service_key = doc["resource_key"]
            family = _family_from_resource_key(service_key)
            service_priority = _service_priority(service_key, family_counts, max_count)

            neo.run(
                "MERGE (f:GCPServiceFamily {name:$family}) "
                "WITH f MATCH (c:GCPCloud {key:'gcp'}) "
                "MERGE (c)-[:HAS_FAMILY]->(f)",
                family=family,
            )
            neo.run(
                "MERGE (s:GCPService {key:$service_key}) "
                "SET s.name=$title, "
                "    s.family=$family, "
                "    s.priority=$priority, "
                "    s.priority_mode='graph_inferred', "
                "    s.priority_formula='30 + 70*family_breadth_signal' "
                "WITH s MATCH (f:GCPServiceFamily {name:$family}) "
                "MERGE (f)-[:HAS_SERVICE]->(s)",
                service_key=service_key,
                title=doc["title"],
                family=family,
                priority=service_priority,
            )
            neo.run(
                "MERGE (d:DocPage {url:$url}) "
                "SET d.title=$title, d.doc_type='terraform_resource_doc' "
                "WITH d MATCH (src:DocSource {id:'terraform_google_provider_repo'}) "
                "MERGE (src)-[:PUBLISHES]->(d) "
                "WITH d MATCH (s:GCPService {key:$service_key}) "
                "MERGE (s)-[:EVIDENCED_BY]->(d)",
                url=doc["url"],
                title=doc["title"],
                service_key=service_key,
            )
            services_count += 1

            variants = infer_variants_from_doc(service_key, doc["content"])
            for variant in variants:
                value_key = variant["name"].lower()
                value_freq[value_key] += 1
                provisional_priority = int(round(min(100, 20 + (30 * (1 / variant["position"])))))
                neo.run(
                    "MERGE (v:GCPServiceVariant {key:$variant_key}) "
                    "SET v.name=$name, "
                    "    v.tier=$tier, "
                    "    v.position=$position, "
                    "    v.priority=$priority, "
                    "    v.priority_mode='graph_inferred_pending_finalize', "
                    "    v.priority_formula='20 + 50*global_value_frequency + 30*(1/position)' "
                    "WITH v MATCH (s:GCPService {key:$service_key}) "
                    "MERGE (s)-[:HAS_VARIANT]->(v)",
                    variant_key=variant["variant_key"],
                    name=variant["name"],
                    tier=variant["tier"],
                    position=variant["position"],
                    priority=provisional_priority,
                    service_key=service_key,
                )
                variant_records.append((variant["variant_key"], value_key, variant["position"]))
                variants_count += 1

            if idx % 50 == 0:
                _log(
                    f"  Resource docs written: {idx}/{len(resource_paths)} | "
                    f"services={services_count}, variants={variants_count}"
                )

        _log("Stage 5/6: Streaming Terraform data-source docs -> Neo4j...")
        for idx, path in enumerate(data_paths, start=1):
            doc = _fetch_doc_content(session_http, path)
            if not doc:
                continue
            neo.run(
                "MERGE (d:DocPage {url:$url}) "
                "SET d.title=$title, d.doc_type='terraform_data_source_doc' "
                "WITH d MATCH (src:DocSource {id:'terraform_google_provider_repo'}) "
                "MERGE (src)-[:PUBLISHES]->(d)",
                url=doc["url"],
                title=doc["title"],
            )
            data_docs_count += 1
            if idx % 100 == 0:
                _log(f"  Data-source docs written: {idx}/{len(data_paths)}")

        _log("Stage 6/6: Finalizing variant scores and service similarity links...")
        max_freq = max(value_freq.values()) if value_freq else 1
        for idx, (variant_key, value_key, position) in enumerate(variant_records, start=1):
            freq_signal = value_freq[value_key] / max_freq
            pos_signal = 1 / position
            final_score = int(round(min(100, 20 + (50 * freq_signal) + (30 * pos_signal))))
            neo.run(
                "MATCH (v:GCPServiceVariant {key:$variant_key}) "
                "SET v.priority=$priority, v.priority_mode='graph_inferred'",
                variant_key=variant_key,
                priority=final_score,
            )
            if idx % 500 == 0:
                _log(f"  Finalized variant scores: {idx}/{len(variant_records)}")

        neo.run(
            "MATCH (a:GCPService), (b:GCPService) "
            "WHERE a.family = b.family AND a.key < b.key "
            "MERGE (a)-[r:SIMILAR_TO]->(b) "
            "SET r.family = a.family "
            "MERGE (b)-[:SIMILAR_TO {family:a.family}]->(a)"
        )

    driver.close()
    _log(
        "Build complete: "
        f"product_docs={products_count}, services={services_count}, "
        f"variants={variants_count}, data_source_docs={data_docs_count}."
    )


def main() -> None:
    load_dotenv()
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE") or None
    dry_run = os.getenv("MIGRAI_DRY_RUN", "0") == "1"

    if dry_run:
        raise RuntimeError("Dry run disabled for this build mode. Set MIGRAI_DRY_RUN=0.")

    if not password:
        raise RuntimeError("NEO4J_PASSWORD is not set. Set Neo4j env vars in .env and rerun.")

    seed_docs_graph(uri, username, password, database)
    print("Seeded Neo4j from official documentation sources with live streaming writes.")


if __name__ == "__main__":
    main()
