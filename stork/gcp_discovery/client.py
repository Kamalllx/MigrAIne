import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Tuple

from google.auth import default
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import asset_v1

if TYPE_CHECKING:
    from .models import GCPResource


class GCPDiscoveryClient:
    """Discover and normalize GCP assets for migration planning."""

    CATEGORY_BY_TYPE = {
        "compute.googleapis.com/Address": "network",
        "compute.googleapis.com/Disk": "compute",
        "compute.googleapis.com/Firewall": "network",
        "compute.googleapis.com/GlobalAddress": "network",
        "compute.googleapis.com/Instance": "compute",
        "compute.googleapis.com/Network": "network",
        "compute.googleapis.com/Project": "compute",
        "compute.googleapis.com/Route": "network",
        "compute.googleapis.com/Router": "network",
        "compute.googleapis.com/Subnetwork": "network",
        "storage.googleapis.com/Bucket": "storage",
        "artifactregistry.googleapis.com/Repository": "registry",
        "artifactregistry.googleapis.com/DockerImage": "registry",
        "artifactregistry.googleapis.com/Package": "registry",
        "sql.googleapis.com/Instance": "database",
        "bigquery.googleapis.com/Dataset": "analytics",
        "bigquery.googleapis.com/Table": "analytics",
        "pubsub.googleapis.com/Topic": "messaging",
        "pubsub.googleapis.com/Subscription": "messaging",
        "cloudfunctions.googleapis.com/CloudFunction": "serverless",
        "run.googleapis.com/Service": "serverless",
        "run.googleapis.com/Revision": "serverless",
        "container.googleapis.com/Cluster": "container",
        "iam.googleapis.com/ServiceAccount": "iam",
        "serviceusage.googleapis.com/Service": "service",
        "cloudbilling.googleapis.com/ProjectBillingInfo": "billing",
        "cloudresourcemanager.googleapis.com/Project": "project",
        "osconfig.googleapis.com/OSPolicyAssignment": "security",
        "secretmanager.googleapis.com/Secret": "security",
        "kms.googleapis.com/KeyRing": "security",
        "kms.googleapis.com/CryptoKey": "security",
    }

    CATEGORY_BY_PREFIX = {
        "compute.googleapis.com": "compute",
        "storage.googleapis.com": "storage",
        "artifactregistry.googleapis.com": "registry",
        "serviceusage.googleapis.com": "service",
        "iam.googleapis.com": "iam",
        "sql.googleapis.com": "database",
        "redis.googleapis.com": "database",
        "firestore.googleapis.com": "database",
        "spanner.googleapis.com": "database",
        "bigquery.googleapis.com": "analytics",
        "pubsub.googleapis.com": "messaging",
        "run.googleapis.com": "serverless",
        "cloudfunctions.googleapis.com": "serverless",
    }

    MIGRATION_HINTS = {
        "compute.googleapis.com/Instance": {
            "aws_service": "EC2",
            "notes": "Map machine type, boot disk, NICs, firewall, IAM role, and startup scripts.",
        },
        "compute.googleapis.com/Disk": {
            "aws_service": "EBS",
            "notes": "Map disk type, size, encryption, and attachment mode.",
        },
        "compute.googleapis.com/Network": {
            "aws_service": "VPC",
            "notes": "Translate global network and routing mode into VPC design.",
        },
        "compute.googleapis.com/Subnetwork": {
            "aws_service": "VPC Subnet",
            "notes": "Convert CIDR, region, and secondary ranges.",
        },
        "compute.googleapis.com/Firewall": {
            "aws_service": "Security Group / NACL",
            "notes": "Translate ingress and egress rules, ranges, and tags.",
        },
        "compute.googleapis.com/Route": {
            "aws_service": "Route Table",
            "notes": "Map destination ranges and next-hop targets.",
        },
        "storage.googleapis.com/Bucket": {
            "aws_service": "S3",
            "notes": "Map storage class, retention/lifecycle, and public access controls.",
        },
        "artifactregistry.googleapis.com/Repository": {
            "aws_service": "ECR",
            "notes": "Map formats, image retention, and repository IAM permissions.",
        },
        "serviceusage.googleapis.com/Service": {
            "aws_service": "Service Catalog",
            "notes": "Track enabled GCP APIs to infer AWS services required post-migration.",
        },
        "iam.googleapis.com/ServiceAccount": {
            "aws_service": "IAM Role/User",
            "notes": "Translate workload identities into AWS IAM roles and policies.",
        },
        "cloudbilling.googleapis.com/ProjectBillingInfo": {
            "aws_service": "Billing",
            "notes": "Capture project-level billing enablement and account linkage.",
        },
    }

    PLANE_BY_CATEGORY = {
        "compute": "compute_plane",
        "container": "compute_plane",
        "serverless": "compute_plane",
        "database": "data_plane",
        "storage": "data_plane",
        "analytics": "data_plane",
        "messaging": "data_plane",
        "registry": "data_plane",
        "network": "network_plane",
        "iam": "identity_plane",
        "security": "security_plane",
        "project": "control_plane",
        "billing": "control_plane",
        "service": "control_plane",
        "unknown": "operations_plane",
    }

    SOURCE_TYPE_BY_ASSET = {
        "compute.googleapis.com/Address": "google_compute_address",
        "compute.googleapis.com/Disk": "google_compute_disk",
        "compute.googleapis.com/Firewall": "google_compute_firewall",
        "compute.googleapis.com/Instance": "google_compute_instance",
        "compute.googleapis.com/Network": "google_compute_network",
        "compute.googleapis.com/Route": "google_compute_route",
        "compute.googleapis.com/Subnetwork": "google_compute_subnetwork",
        "storage.googleapis.com/Bucket": "google_storage_bucket",
        "artifactregistry.googleapis.com/Repository": "google_artifact_registry_repository",
        "iam.googleapis.com/ServiceAccount": "google_service_account",
        "serviceusage.googleapis.com/Service": "google_project_service",
        "sql.googleapis.com/Instance": "google_sql_database_instance",
        "cloudfunctions.googleapis.com/CloudFunction": "google_cloudfunctions2_function",
        "run.googleapis.com/Service": "google_cloud_run_v2_service",
        "cloudresourcemanager.googleapis.com/Project": "google_project",
        "cloudbilling.googleapis.com/ProjectBillingInfo": "google_project_billing_info",
        "secretmanager.googleapis.com/Secret": "google_secret_manager_secret",
        "kms.googleapis.com/CryptoKey": "google_kms_crypto_key",
        "kms.googleapis.com/KeyRing": "google_kms_key_ring",
    }

    CORE_MIGRATION_CATEGORIES = {
        "compute",
        "container",
        "serverless",
        "database",
        "storage",
        "analytics",
        "messaging",
        "registry",
    }

    CORE_INCLUDED_SOURCE_TYPES = {
        "google_compute_instance",
        "google_compute_disk",
        "google_cloud_run_v2_service",
        "google_cloudfunctions2_function",
        "google_sql_database_instance",
        "google_redis_instance",
        "google_firestore_database",
        "google_storage_bucket",
        "google_artifact_registry_repository",
        "google_pubsub_topic",
        "google_pubsub_subscription",
        "google_bigquery_dataset",
        "google_bigquery_table",
        "google_container_cluster",
    }

    CORE_EXCLUDED_SOURCE_TYPES = {
        "google_project",
        "google_project_service",
        "google_project_billing_info",
        "google_service_account",
        "google_compute_project",
        "google_compute_instance_settings",
        "google_compute_resource_policy",
        "google_compute_network",
        "google_compute_subnetwork",
        "google_compute_firewall",
        "google_compute_route",
        "google_compute_router",
        "google_compute_address",
        "google_compute_global_address",
        "google_dns_managed_zone",
        "google_dns_resource_record_set",
        "google_monitoring_alert_policy",
        "google_monitoring_dashboard",
        "google_monitoring_notification_channel",
        "google_monitoring_uptime_check_config",
        "google_secret_manager_secret",
        "google_secretmanager_secret_version",
        "google_cloudkms_key_ring",
        "google_cloudkms_crypto_key",
        "google_cloudkms_crypto_key_version",
        "google_artifactregistry_docker_image",
        "google_pubsub_schema",
        "google_run_revision",
    }

    CORE_EXCLUDED_NAME_SUBSTRINGS_BY_SOURCE_TYPE = {
        "google_storage_bucket": (
            "_cloudbuild",
            "cloudbuild",
            "gcf-v2-sources-",
            "run-sources-",
            "functions-src",
            "terraform-state",
            "tfstate",
        ),
        "google_artifact_registry_repository": (
            "cloud-run-source-deploy",
        ),
    }

    DEFAULT_CONTENT_TYPES = ("RESOURCE", "IAM_POLICY", "ORG_POLICY", "ACCESS_POLICY")

    def __init__(self, project_id: Optional[str] = None):
        try:
            credentials, adc_project = default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        except DefaultCredentialsError as exc:
            raise ValueError(
                "Application Default Credentials not found. Run: "
                "gcloud auth application-default login"
            ) from exc

        env_project = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.project_id = project_id or env_project or adc_project
        if not self.project_id:
            raise ValueError(
                "project_id not resolved. Provide project_id, set GCP_PROJECT_ID/GOOGLE_CLOUD_PROJECT, "
                "or set a default gcloud project (gcloud config set project <PROJECT_ID>)."
            )

        self.client = asset_v1.AssetServiceClient(credentials=credentials)
        self.parent = f"projects/{self.project_id}"
        self.last_discovery_warnings: List[str] = []

        self._parser_map: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = {
            "compute.googleapis.com/Instance": self._parse_compute_instance,
            "compute.googleapis.com/Disk": self._parse_compute_disk,
            "compute.googleapis.com/Network": self._parse_compute_network,
            "compute.googleapis.com/Subnetwork": self._parse_compute_subnetwork,
            "compute.googleapis.com/Firewall": self._parse_compute_firewall,
            "compute.googleapis.com/Route": self._parse_compute_route,
            "compute.googleapis.com/Project": self._parse_compute_project,
            "storage.googleapis.com/Bucket": self._parse_storage_bucket,
            "iam.googleapis.com/ServiceAccount": self._parse_iam_service_account,
            "serviceusage.googleapis.com/Service": self._parse_service_usage,
            "artifactregistry.googleapis.com/Repository": self._parse_artifact_repository,
            "artifactregistry.googleapis.com/DockerImage": self._parse_artifact_docker_image,
            "cloudbilling.googleapis.com/ProjectBillingInfo": self._parse_project_billing,
            "cloudresourcemanager.googleapis.com/Project": self._parse_resource_manager_project,
            "run.googleapis.com/Service": self._parse_cloud_run_service,
            "cloudfunctions.googleapis.com/CloudFunction": self._parse_cloud_function,
            "osconfig.googleapis.com/OSPolicyAssignment": self._parse_os_policy_assignment,
        }

    def discover_all(
        self,
        include_content_types: Optional[List[str]] = None,
        include_search_metadata: bool = True,
    ) -> List[Dict[str, Any]]:
        """Discover assets across multiple content types and merge them."""
        self.last_discovery_warnings = []
        merged: Dict[Tuple[str, str], Dict[str, Any]] = {}

        content_type_names = include_content_types or list(self.DEFAULT_CONTENT_TYPES)
        for content_type_name in content_type_names:
            content_type_enum = getattr(asset_v1.ContentType, content_type_name, None)
            if content_type_enum is None:
                self.last_discovery_warnings.append(
                    f"ContentType.{content_type_name} is unavailable in installed client library."
                )
                continue

            assets = self._list_assets(content_type_enum, content_type_name)
            for asset in assets:
                key = (asset.get("name", ""), asset.get("asset_type", ""))
                existing = merged.get(key)
                merged[key] = self._merge_assets(existing, asset) if existing else asset

        if include_search_metadata:
            for record in self._search_all_resources():
                key = (record.get("name", ""), record.get("asset_type", ""))
                existing = merged.get(key)
                if existing:
                    existing["search_metadata"] = record
                else:
                    merged[key] = {
                        "name": record.get("name", ""),
                        "asset_type": record.get("asset_type", "unknown"),
                        "project": record.get("project") or self.project_id,
                        "source_content_types": ["SEARCH"],
                        "search_metadata": record,
                    }

        return sorted(
            merged.values(),
            key=lambda item: (item.get("asset_type", ""), item.get("name", "")),
        )

    def discover(
        self,
        asset_types: List[str],
        include_content_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Discover selected asset types across content types."""
        self.last_discovery_warnings = []
        merged: Dict[Tuple[str, str], Dict[str, Any]] = {}

        content_type_names = include_content_types or list(self.DEFAULT_CONTENT_TYPES)
        for content_type_name in content_type_names:
            content_type_enum = getattr(asset_v1.ContentType, content_type_name, None)
            if content_type_enum is None:
                continue

            assets = self._list_assets(
                content_type_enum=content_type_enum,
                content_type_name=content_type_name,
                asset_types=asset_types,
            )
            for asset in assets:
                key = (asset.get("name", ""), asset.get("asset_type", ""))
                existing = merged.get(key)
                merged[key] = self._merge_assets(existing, asset) if existing else asset

        return sorted(
            merged.values(),
            key=lambda item: (item.get("asset_type", ""), item.get("name", "")),
        )

    def discover_iter(self) -> Iterator[Dict[str, Any]]:
        """Iterate over merged assets."""
        for asset in self.discover_all():
            yield asset

    def get_iam_policies(self) -> List[Dict[str, Any]]:
        """Fetch IAM policy content only."""
        content_type_enum = getattr(asset_v1.ContentType, "IAM_POLICY", None)
        if content_type_enum is None:
            return []
        return self._list_assets(content_type_enum, "IAM_POLICY")

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Search resources using Cloud Asset Inventory query language."""
        request = asset_v1.SearchAllResourcesRequest(
            scope=self.parent,
            query=query,
            page_size=500,
        )

        results = []
        for resource in self.client.search_all_resources(request=request):
            results.append(
                {
                    "name": resource.name,
                    "asset_type": resource.asset_type,
                    "project": resource.project,
                    "display_name": resource.display_name,
                    "description": resource.description,
                    "location": resource.location,
                    "state": resource.state,
                    "parent_full_resource_name": resource.parent_full_resource_name,
                    "labels": self._to_plain_value(resource.labels) or {},
                    "additional_attributes": self._to_plain_value(resource.additional_attributes)
                    or {},
                }
            )

        return results

    def build_inventory_snapshot(
        self,
        raw_assets: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build a migration inventory payload for APIs and reports."""
        if raw_assets is None:
            raw_assets = self.discover_all()

        resources = self.normalize(raw_assets)
        by_type = dict(Counter(resource.asset_type for resource in resources))
        by_category = dict(Counter(resource.category for resource in resources))

        by_aws_target = Counter()
        enabled_services = []

        for resource in resources:
            aws_service = (
                resource.config.get("migration_hint", {}).get("aws_service")
                or "Review Required"
            )
            by_aws_target[aws_service] += 1

            if resource.asset_type == "serviceusage.googleapis.com/Service":
                if resource.config.get("state") == "ENABLED":
                    enabled_services.append(resource.config.get("service_name", resource.name))

        return {
            "project_id": self.project_id,
            "total_raw_assets": len(raw_assets),
            "total_resources": len(resources),
            "discovery_warnings": list(self.last_discovery_warnings),
            "by_type": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
            "by_category": dict(
                sorted(by_category.items(), key=lambda item: (-item[1], item[0]))
            ),
            "by_aws_target": dict(
                sorted(by_aws_target.items(), key=lambda item: (-item[1], item[0]))
            ),
            "enabled_services": sorted(set(enabled_services)),
            "resources": [self._resource_to_dict(resource) for resource in resources],
        }

    def build_migration_blueprint(
        self,
        raw_assets: Optional[List[Dict[str, Any]]] = None,
        target_provider: str = "aws",
        target_primary_region: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a plane-based migration blueprint payload."""
        if raw_assets is None:
            raw_assets = self.discover_all()

        normalized_resources = self.normalize(raw_assets)
        resource_dicts = [self._resource_to_dict(resource) for resource in normalized_resources]
        catalog = self._build_resource_catalog(resource_dicts)
        dependencies = self._build_dependency_graph(catalog)

        enabled_service_names = [
            str(item.get("name"))
            for item in catalog
            if item.get("source_type") == "google_project_service"
            and item.get("name")
            and str(item.get("data_context", {}).get("state", "")).upper() == "ENABLED"
        ]
        enabled_services = sorted(set(enabled_service_names))

        resolved_environment = environment or os.getenv("MIGRATION_ENV", "prod")
        resolved_target_region = target_primary_region or os.getenv(
            "AWS_TARGET_REGION",
            "us-east-1",
        )

        project_context = self._build_project_context(
            resources=resource_dicts,
            source_provider="gcp",
            target_provider=target_provider,
            environment=resolved_environment,
            target_primary_region=resolved_target_region,
        )

        canonical_project_id = str(project_context.get("id") or self.project_id)
        canonical_project_name = str(project_context.get("name") or canonical_project_id)
        for item in catalog:
            item_project_id = str(item.get("project_id") or canonical_project_id)
            item["project_id"] = item_project_id
            if item_project_id == canonical_project_id:
                item["project_name"] = canonical_project_name
            elif not item.get("project_name"):
                item["project_name"] = item_project_id

        planes = self._build_plane_payload(
            resources=resource_dicts,
            catalog=catalog,
            dependencies=dependencies,
            enabled_services=enabled_services,
        )

        by_plane = Counter(item.get("plane", "unknown") for item in catalog)
        by_source_type = Counter(item.get("source_type", "unknown") for item in catalog)

        payload = {
            "schema_version": "1.0",
            "project": project_context,
            "planes": planes,
            "dependencies": dependencies,
            "migration_constraints": {
                "downtime_tolerance_minutes": 20,
                "cutover_strategy": "blue_green",
                "budget_tier": "balanced",
                "must_keep_service_names": True,
            },
            "resource_catalog": catalog,
            "summary": {
                "total_raw_assets": len(raw_assets),
                "total_resources": len(catalog),
                "total_dependencies": len(dependencies),
                "discovery_warning_count": len(self.last_discovery_warnings),
                "by_plane": dict(sorted(by_plane.items(), key=lambda item: (-item[1], item[0]))),
                "by_source_type": dict(
                    sorted(by_source_type.items(), key=lambda item: (-item[1], item[0]))
                ),
            },
            "discovery_warnings": list(self.last_discovery_warnings),
        }

        safe_payload = self._json_safe(payload)
        return safe_payload if isinstance(safe_payload, dict) else payload

    def normalize(self, raw_assets: List[Dict[str, Any]]) -> List["GCPResource"]:
        """Normalize raw assets with asset-type specific parsers."""
        from .models import GCPResource

        normalized: List[GCPResource] = []

        for asset in raw_assets:
            asset_type = asset.get("asset_type", "unknown")
            resource_section = asset.get("resource", {})
            resource_data = resource_section.get("data", {})

            if not isinstance(resource_data, dict):
                plain = self._to_plain_value(resource_data)
                resource_data = plain if isinstance(plain, dict) else {"value": plain}

            parser = self._parser_map.get(asset_type, self._parse_generic_resource)
            parsed = parser(resource_data, asset)

            name_candidate = (
                parsed.get("name")
                or resource_data.get("name")
                or self._extract_name_from_asset(asset.get("name", ""))
            )
            name = str(name_candidate) if name_candidate not in (None, "") else "unknown"
            category = self._category_for_asset_type(asset_type)

            labels = asset.get("labels") or parsed.get("labels") or self._extract_labels(resource_data)
            if not isinstance(labels, dict):
                labels = {}

            tags = parsed.get("tags")
            if not isinstance(tags, list):
                tags = self._extract_tags(resource_data)

            dependencies = parsed.get("dependencies", [])
            if not isinstance(dependencies, list):
                dependencies = [dependencies]
            dependencies = [str(dep) for dep in dependencies if dep]
            dependencies.extend(self._extract_dependencies(resource_data))
            dependencies = sorted(set(dependencies))

            location = (
                parsed.get("zone")
                or parsed.get("region")
                or parsed.get("location")
                or self._extract_location(asset, resource_data)
            )

            config = dict(parsed)
            config.setdefault("name", name)
            config.setdefault("labels", labels)
            config.setdefault("tags", tags)
            config["asset_name"] = asset.get("name")
            config["asset_type"] = asset_type
            config["category"] = category
            config["location"] = location
            config["update_time"] = asset.get("update_time")
            config["ancestors"] = asset.get("ancestors", [])
            config["source_content_types"] = asset.get("source_content_types", ["RESOURCE"])
            config["search_metadata"] = asset.get("search_metadata", {})
            config["iam_bindings"] = self._flatten_iam_bindings(asset.get("iam_policy"))
            config["org_policy"] = asset.get("org_policy")
            config["access_policy"] = asset.get("access_policy")
            config["migration_hint"] = self.MIGRATION_HINTS.get(
                asset_type,
                {
                    "aws_service": "Review Required",
                    "notes": "No direct mapping rule is configured for this asset type.",
                },
            )
            config["raw_data"] = resource_data

            normalized.append(
                GCPResource(
                    id=asset.get("name", ""),
                    name=name,
                    asset_type=asset_type,
                    category=category,
                    project=asset.get("project", self.project_id),
                    location=location,
                    labels=labels,
                    tags=tags,
                    config=config,
                    iam_policy=asset.get("iam_policy"),
                    dependencies=dependencies,
                    raw=asset,
                )
            )

        return normalized

    def _list_assets(
        self,
        content_type_enum,
        content_type_name: str,
        asset_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        request_kwargs: Dict[str, Any] = {
            "parent": self.parent,
            "content_type": content_type_enum,
        }
        if asset_types:
            request_kwargs["asset_types"] = asset_types

        request = asset_v1.ListAssetsRequest(**request_kwargs)
        assets: List[Dict[str, Any]] = []

        try:
            for asset in self.client.list_assets(request=request):
                assets.append(self._asset_to_dict(asset, source_content_type=content_type_name))
        except Exception as exc:
            self.last_discovery_warnings.append(
                f"{content_type_name} discovery failed: {exc}"
            )

        return assets

    def _search_all_resources(self) -> List[Dict[str, Any]]:
        request = asset_v1.SearchAllResourcesRequest(
            scope=self.parent,
            query="",
            page_size=500,
        )

        results: List[Dict[str, Any]] = []
        try:
            for resource in self.client.search_all_resources(request=request):
                results.append(
                    {
                        "name": resource.name,
                        "asset_type": resource.asset_type,
                        "project": resource.project,
                        "display_name": resource.display_name,
                        "description": resource.description,
                        "location": resource.location,
                        "state": resource.state,
                        "parent_full_resource_name": resource.parent_full_resource_name,
                        "labels": self._to_plain_value(resource.labels) or {},
                        "additional_attributes": self._to_plain_value(
                            resource.additional_attributes
                        )
                        or {},
                    }
                )
        except Exception as exc:
            self.last_discovery_warnings.append(f"Search metadata discovery failed: {exc}")

        return results

    def _merge_assets(
        self,
        existing: Dict[str, Any],
        incoming: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(existing)

        source_content_types = list(merged.get("source_content_types", []))
        source_content_types.extend(incoming.get("source_content_types", []))
        merged["source_content_types"] = sorted(set(source_content_types))

        for key, value in incoming.items():
            if key in {"name", "asset_type", "project", "source_content_types"}:
                continue

            if key == "labels":
                current = dict(merged.get("labels", {}))
                current.update(value or {})
                merged["labels"] = current
                continue

            if key == "ancestors":
                merged["ancestors"] = sorted(
                    set(list(merged.get("ancestors", [])) + list(value or []))
                )
                continue

            if key not in merged or merged[key] in (None, "", {}, []):
                merged[key] = value

        return merged

    def _asset_to_dict(self, asset, source_content_type: str = "RESOURCE") -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "name": asset.name,
            "asset_type": asset.asset_type,
            "project": self.project_id,
            "source_content_types": [source_content_type],
        }

        resource_value = self._safe_get_attr(asset, "resource")
        if resource_value:
            result["resource"] = {
                "version": resource_value.version,
                "discovery_document_uri": resource_value.discovery_document_uri,
                "discovery_name": resource_value.discovery_name,
                "parent": resource_value.parent,
                "data": self._to_plain_value(resource_value.data) if resource_value.data else {},
            }

        iam_policy = self._safe_get_attr(asset, "iam_policy")
        if iam_policy:
            result["iam_policy"] = self._to_plain_value(iam_policy)

        org_policy = self._safe_get_attr(asset, "org_policy")
        if org_policy:
            result["org_policy"] = self._to_plain_value(org_policy)

        access_policy = self._safe_get_attr(asset, "access_policy")
        if access_policy:
            result["access_policy"] = self._to_plain_value(access_policy)

        relationship_attributes = self._safe_get_attr(asset, "relationship_attributes")
        if relationship_attributes:
            result["relationship_attributes"] = self._to_plain_value(relationship_attributes)

        update_time = self._safe_get_attr(asset, "update_time")
        if update_time:
            try:
                result["update_time"] = update_time.isoformat()
            except Exception:
                result["update_time"] = str(update_time)

        ancestors = self._safe_get_attr(asset, "ancestors")
        if ancestors:
            result["ancestors"] = list(ancestors)

        labels = {}
        top_level_labels = self._safe_get_attr(asset, "labels")
        if top_level_labels:
            labels = self._to_plain_value(top_level_labels) or {}

        if not labels and resource_value and resource_value.data:
            maybe_labels = self._to_plain_value(resource_value.data.get("labels"))
            if isinstance(maybe_labels, dict):
                labels = maybe_labels

        if labels:
            result["labels"] = labels

        return result

    def _to_plain_value(self, value):
        if value is None:
            return None

        if isinstance(value, Mapping):
            return {k: self._to_plain_value(v) for k, v in value.items()}

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._to_plain_value(v) for v in value]

        if hasattr(value, "_pb"):
            from google.protobuf.json_format import MessageToDict

            try:
                pb_value = getattr(value, "_pb", None)
                if pb_value is not None:
                    return MessageToDict(pb_value, preserving_proto_field_name=True)
                return value
            except Exception:
                return value

        return value

    def _safe_get_attr(self, obj, attr_name: str):
        try:
            return getattr(obj, attr_name)
        except AttributeError:
            return None

    def _resource_to_dict(self, resource) -> Dict[str, Any]:
        return {
            "id": resource.id,
            "name": resource.name,
            "asset_type": resource.asset_type,
            "category": resource.category,
            "project": resource.project,
            "location": resource.location,
            "labels": self._json_safe(resource.labels),
            "tags": self._json_safe(resource.tags),
            "config": self._json_safe(resource.config),
            "iam_policy": self._json_safe(resource.iam_policy),
            "dependencies": self._json_safe(resource.dependencies),
            "raw": self._json_safe(resource.raw),
            "discovered_at": resource.discovered_at.isoformat(),
        }

    def _json_safe(self, value):
        plain = self._to_plain_value(value)

        if isinstance(plain, Mapping):
            return {str(key): self._json_safe(item) for key, item in plain.items()}

        if isinstance(plain, Sequence) and not isinstance(plain, (str, bytes, bytearray)):
            return [self._json_safe(item) for item in plain]

        if isinstance(plain, (str, int, float, bool)) or plain is None:
            return plain

        return str(plain)

    def _category_for_asset_type(self, asset_type: str) -> str:
        if asset_type in self.CATEGORY_BY_TYPE:
            return self.CATEGORY_BY_TYPE[asset_type]

        for prefix, category in self.CATEGORY_BY_PREFIX.items():
            if asset_type.startswith(prefix):
                return category

        return "unknown"

    def _parse_generic_resource(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "description": data.get("description"),
            "location": self._extract_location(asset, data),
            "state": data.get("state") or data.get("status") or data.get("lifecycleState"),
            "labels": self._extract_labels(data),
            "tags": self._extract_tags(data),
        }

    def _parse_compute_instance(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        zone = self._short_name(data.get("zone"))
        region = self._zone_to_region(zone)

        networks = []
        for interface in data.get("networkInterfaces", []):
            access_configs = interface.get("accessConfigs", []) or []
            external_ips = [cfg.get("natIP") for cfg in access_configs if cfg.get("natIP")]
            alias_ips = [
                alias.get("ipCidrRange")
                for alias in interface.get("aliasIpRanges", [])
                if alias.get("ipCidrRange")
            ]

            networks.append(
                {
                    "network": self._short_name(interface.get("network")),
                    "subnetwork": self._short_name(interface.get("subnetwork")),
                    "internal_ip": interface.get("networkIP"),
                    "external_ip": external_ips[0] if external_ips else None,
                    "external_ips": external_ips,
                    "alias_ips": alias_ips,
                    "stack_type": interface.get("stackType"),
                    "network_tier": access_configs[0].get("networkTier")
                    if access_configs
                    else None,
                }
            )

        disks = []
        for disk in data.get("disks", []):
            source = disk.get("source", "")
            disk_item = {
                "boot": disk.get("boot", False),
                "auto_delete": disk.get("autoDelete", False),
                "device_name": disk.get("deviceName"),
                "source": self._short_name(source),
                "source_full": source,
                "mode": disk.get("mode", "READ_WRITE"),
                "type": disk.get("type", "PERSISTENT"),
                "interface": disk.get("interface", "SCSI"),
            }
            disks.append(disk_item)

        metadata_items = data.get("metadata", {}).get("items", [])
        metadata = {}
        for item in metadata_items:
            key = item.get("key")
            if key:
                metadata[key] = item.get("value")

        service_accounts = []
        for account in data.get("serviceAccounts", []):
            scopes = account.get("scopes", [])
            service_accounts.append(
                {
                    "email": account.get("email"),
                    "scopes": list(scopes) if isinstance(scopes, list) else [scopes],
                }
            )

        machine_type = self._short_name(data.get("machineType"))

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "machine_type": machine_type,
            "cpu_platform": data.get("cpuPlatform"),
            "status": data.get("status"),
            "zone": zone,
            "region": region,
            "networks": networks,
            "disks": disks,
            "boot_disk": next((disk for disk in disks if disk.get("boot")), None),
            "service_accounts": service_accounts,
            "metadata": metadata,
            "startup_script": metadata.get("startup-script"),
            "deletion_protection": data.get("deletionProtection", False),
            "min_cpu_platform": data.get("minCpuPlatform"),
            "scheduling": data.get("scheduling", {}),
            "shielded_vm": data.get("shieldedInstanceConfig", {}),
            "confidential_computing": data.get("confidentialInstanceConfig", {}),
            "can_ip_forward": data.get("canIpForward", False),
            "tags": self._extract_tags(data),
            "labels": self._extract_labels(data),
        }

    def _parse_compute_disk(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        zone = self._short_name(data.get("zone"))
        region = self._zone_to_region(zone)

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "zone": zone,
            "region": region,
            "status": data.get("status"),
            "size_gb": self._safe_int(data.get("sizeGb")),
            "type": self._short_name(data.get("type")),
            "source_snapshot": self._short_name(data.get("sourceSnapshot")),
            "users": [self._short_name(user) for user in data.get("users", [])],
            "encrypted": bool(data.get("diskEncryptionKey")),
            "physical_block_size_bytes": self._safe_int(data.get("physicalBlockSizeBytes")),
            "labels": self._extract_labels(data),
        }

    def _parse_compute_network(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        peerings = []
        for peering in data.get("peerings", []):
            peerings.append(
                {
                    "name": peering.get("name"),
                    "network": self._short_name(peering.get("network")),
                    "state": peering.get("state"),
                    "auto_create_routes": peering.get("autoCreateRoutes"),
                }
            )

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "description": data.get("description"),
            "auto_create_subnetworks": data.get("autoCreateSubnetworks"),
            "routing_mode": data.get("routingConfig", {}).get("routingMode"),
            "mtu": data.get("mtu"),
            "peerings": peerings,
            "labels": self._extract_labels(data),
        }

    def _parse_compute_subnetwork(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        secondary_ranges = [
            {
                "name": item.get("rangeName"),
                "cidr": item.get("ipCidrRange"),
            }
            for item in data.get("secondaryIpRanges", [])
        ]

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "region": self._short_name(data.get("region")),
            "network": self._short_name(data.get("network")),
            "ip_cidr_range": data.get("ipCidrRange"),
            "gateway_address": data.get("gatewayAddress"),
            "private_ip_google_access": data.get("privateIpGoogleAccess", False),
            "purpose": data.get("purpose"),
            "stack_type": data.get("stackType"),
            "secondary_ranges": secondary_ranges,
            "labels": self._extract_labels(data),
        }

    def _parse_compute_firewall(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "network": self._short_name(data.get("network")),
            "direction": data.get("direction", "INGRESS"),
            "priority": self._safe_int(data.get("priority")),
            "disabled": data.get("disabled", False),
            "source_ranges": data.get("sourceRanges", []),
            "destination_ranges": data.get("destinationRanges", []),
            "target_tags": data.get("targetTags", []),
            "source_tags": data.get("sourceTags", []),
            "allowed": data.get("allowed", []),
            "denied": data.get("denied", []),
            "labels": self._extract_labels(data),
        }

    def _parse_compute_route(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        next_hop = (
            data.get("nextHopGateway")
            or data.get("nextHopInstance")
            or data.get("nextHopIlb")
            or data.get("nextHopVpnTunnel")
            or data.get("nextHopIp")
        )

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "network": self._short_name(data.get("network")),
            "destination_range": data.get("destRange"),
            "priority": self._safe_int(data.get("priority")),
            "next_hop": self._short_name(next_hop) if next_hop and "/" in str(next_hop) else next_hop,
            "tags": data.get("tags", []),
            "route_type": data.get("routeType"),
        }

    def _parse_compute_project(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        metadata = {}
        metadata_items = data.get("commonInstanceMetadata", {}).get("items", [])
        for item in metadata_items:
            key = item.get("key")
            if key:
                metadata[key] = item.get("value")

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "default_service_account": data.get("defaultServiceAccount"),
            "default_network_tier": data.get("defaultNetworkTier"),
            "vm_dns_setting": data.get("vmDnsSetting"),
            "xpn_project_status": data.get("xpnProjectStatus"),
            "metadata": metadata,
        }

    def _parse_storage_bucket(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        iam_cfg = data.get("iamConfiguration", {})

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "location": data.get("location"),
            "storage_class": data.get("storageClass"),
            "versioning_enabled": data.get("versioning", {}).get("enabled", False),
            "retention_policy": data.get("retentionPolicy", {}),
            "lifecycle_rules": data.get("lifecycle", {}).get("rule", []),
            "cors": data.get("cors", []),
            "logging": data.get("logging", {}),
            "website": data.get("website", {}),
            "uniform_bucket_level_access": iam_cfg.get("uniformBucketLevelAccess", {}).get(
                "enabled", False
            ),
            "public_access_prevention": iam_cfg.get("publicAccessPrevention"),
            "labels": self._extract_labels(data),
        }

    def _parse_iam_service_account(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        email = data.get("email")
        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "email": email,
            "display_name": data.get("displayName"),
            "description": data.get("description"),
            "disabled": data.get("disabled", False),
            "oauth2_client_id": data.get("oauth2ClientId"),
            "unique_id": data.get("uniqueId"),
            "service_account_id": email.split("@")[0] if email else None,
        }

    def _parse_service_usage(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        full_name = data.get("name") or asset.get("name", "")
        service_name = full_name.split("/services/")[-1] if "/services/" in full_name else full_name

        return {
            "name": service_name,
            "service_name": service_name,
            "state": data.get("state"),
            "title": data.get("config", {}).get("title"),
            "documentation": data.get("config", {}).get("documentation", {}),
            "quota": data.get("config", {}).get("quota", {}),
        }

    def _parse_artifact_repository(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        repo_name = data.get("name") or ""
        location = None
        if "/locations/" in repo_name:
            location = repo_name.split("/locations/")[-1].split("/")[0]

        return {
            "name": repo_name or self._extract_name_from_asset(asset.get("name", "")),
            "location": location,
            "format": data.get("format"),
            "mode": data.get("mode"),
            "description": data.get("description"),
            "kms_key_name": data.get("kmsKeyName"),
            "cleanup_policies": data.get("cleanupPolicies", {}),
            "labels": self._extract_labels(data),
        }

    def _parse_artifact_docker_image(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "uri": data.get("uri"),
            "media_type": data.get("mediaType"),
            "image_size_bytes": self._safe_int(data.get("imageSizeBytes")),
            "upload_time": data.get("uploadTime"),
            "build_time": data.get("buildTime"),
        }

    def _parse_project_billing(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "billing_account_name": data.get("billingAccountName"),
            "billing_enabled": data.get("billingEnabled", False),
        }

    def _parse_resource_manager_project(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "name": data.get("displayName")
            or data.get("name")
            or self._extract_name_from_asset(asset.get("name", "")),
            "project_id": data.get("projectId"),
            "project_number": data.get("projectNumber"),
            "lifecycle_state": data.get("lifecycleState") or data.get("state"),
            "parent": data.get("parent"),
            "labels": self._extract_labels(data),
        }

    def _parse_cloud_run_service(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        template = data.get("template", {})
        containers = template.get("containers", [])
        container_images = [container.get("image") for container in containers if container.get("image")]

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "location": self._short_name(data.get("location")) or self._extract_location(asset, data),
            "ingress": data.get("ingress"),
            "launch_stage": data.get("launchStage"),
            "service_url": data.get("uri") or data.get("status", {}).get("url"),
            "container_images": container_images,
            "service_account": template.get("serviceAccount"),
            "vpc_connector": template.get("vpcAccess", {}).get("connector"),
            "labels": self._extract_labels(data),
        }

    def _parse_cloud_function(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        event_trigger = data.get("eventTrigger", {})
        https_trigger = data.get("httpsTrigger", {})

        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "runtime": data.get("runtime"),
            "entry_point": data.get("entryPoint"),
            "status": data.get("status"),
            "region": self._extract_location(asset, data),
            "service_account_email": data.get("serviceAccountEmail"),
            "available_memory_mb": self._safe_int(data.get("availableMemoryMb")),
            "timeout": data.get("timeout"),
            "vpc_connector": data.get("vpcConnector"),
            "trigger": {
                "https_url": https_trigger.get("url"),
                "event_type": event_trigger.get("eventType"),
                "resource": event_trigger.get("resource"),
            },
            "labels": self._extract_labels(data),
        }

    def _parse_os_policy_assignment(
        self,
        data: Dict[str, Any],
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        rollout = data.get("rollout", {})
        return {
            "name": data.get("name") or self._extract_name_from_asset(asset.get("name", "")),
            "description": data.get("description"),
            "location": self._extract_location(asset, data),
            "instance_filter": data.get("instanceFilter", {}),
            "os_policies": data.get("osPolicies", []),
            "rollout_state": rollout.get("state"),
            "rollout_disruption_budget": rollout.get("disruptionBudget", {}),
            "revision_id": data.get("revisionId"),
        }

    def _extract_name_from_asset(self, asset_name: str) -> str:
        if not asset_name:
            return "unknown"
        return asset_name.rstrip("/").split("/")[-1]

    def _extract_location(self, asset: Dict[str, Any], data: Dict[str, Any]) -> Optional[str]:
        for key in ("zone", "region", "location"):
            value = data.get(key)
            if value:
                return self._short_name(value)

        search_metadata = asset.get("search_metadata", {})
        for key in ("location", "region"):
            value = search_metadata.get(key)
            if value:
                return value

        return None

    def _extract_labels(self, data: Dict[str, Any]) -> Dict[str, str]:
        labels = data.get("labels") or data.get("resourceLabels") or {}
        return labels if isinstance(labels, dict) else {}

    def _extract_tags(self, data: Dict[str, Any]) -> List[str]:
        tags = data.get("tags")
        if isinstance(tags, dict):
            items = tags.get("items", [])
            if isinstance(items, list):
                return [str(item) for item in items]
            return []

        if isinstance(tags, list):
            return [str(item) for item in tags]

        return []

    def _short_name(self, value: Any) -> Optional[str]:
        if not value:
            return None
        text = str(value)
        return text.split("/")[-1]

    def _zone_to_region(self, zone: Optional[str]) -> Optional[str]:
        if not zone:
            return None
        zone_name = self._short_name(zone)
        if not zone_name:
            return None
        parts = zone_name.rsplit("-", 1)
        return parts[0] if len(parts) == 2 else zone_name

    def _safe_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _flatten_iam_bindings(self, iam_policy: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not iam_policy:
            return []

        policy = iam_policy if isinstance(iam_policy, dict) else self._to_plain_value(iam_policy)
        if not isinstance(policy, dict):
            return []

        bindings = policy.get("bindings", [])
        if not isinstance(bindings, list):
            return []

        flattened = []
        for binding in bindings:
            members = binding.get("members", [])
            flattened.append(
                {
                    "role": binding.get("role"),
                    "members": members if isinstance(members, list) else [members],
                    "condition": binding.get("condition"),
                }
            )
        return flattened

    def _extract_dependencies(self, config: Dict[str, Any]) -> List[str]:
        text_values: List[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
                return

            if isinstance(value, list):
                for item in value:
                    walk(item)
                return

            if isinstance(value, str):
                text_values.append(value)

        walk(config)

        patterns = [
            r"projects/[^/]+/zones/[^/]+/instances/[^/\s\"']+",
            r"projects/[^/]+/zones/[^/]+/disks/[^/\s\"']+",
            r"projects/[^/]+/regions/[^/]+/subnetworks/[^/\s\"']+",
            r"projects/[^/]+/global/networks/[^/\s\"']+",
            r"projects/[^/]+/global/firewalls/[^/\s\"']+",
            r"projects/[^/]+/global/routes/[^/\s\"']+",
            r"projects/[^/]+/serviceAccounts/[^/\s\"']+",
            r"projects/[^/]+/buckets/[^/\s\"']+",
        ]

        dependencies = []
        for value in text_values:
            for pattern in patterns:
                dependencies.extend(re.findall(pattern, value))

        return sorted(set(dependencies))

    def _build_project_context(
        self,
        resources: List[Dict[str, Any]],
        source_provider: str,
        target_provider: str,
        environment: str,
        target_primary_region: str,
    ) -> Dict[str, Any]:
        project_name = self.project_id
        region_candidates: List[str] = []

        for resource in resources:
            if resource.get("asset_type") == "cloudresourcemanager.googleapis.com/Project":
                project_name = resource.get("name") or project_name

            config = resource.get("config", {})
            location = resource.get("location") or config.get("region") or config.get("zone")
            region = self._zone_to_region(location)
            if region and region.lower() != "global":
                region_candidates.append(region)

        source_primary_region = None
        if region_candidates:
            source_primary_region = Counter(region_candidates).most_common(1)[0][0]

        return {
            "id": self.project_id,
            "name": project_name,
            "source_provider": source_provider,
            "target_provider": target_provider,
            "environment": environment,
            "regions": {
                "source_primary": source_primary_region,
                "target_primary": target_primary_region,
            },
        }

    def _build_plane_payload(
        self,
        resources: List[Dict[str, Any]],
        catalog: List[Dict[str, Any]],
        dependencies: List[Dict[str, Any]],
        enabled_services: List[str],
    ) -> Dict[str, Any]:
        policy_constraints: List[str] = []
        wildcard_member_found = False

        for resource in resources:
            config = resource.get("config", {})
            policy_blob = config.get("org_policy")
            if isinstance(policy_blob, dict):
                for key in policy_blob.keys():
                    policy_constraints.append(f"{resource.get('name')}: {key}")
            elif policy_blob:
                policy_constraints.append(f"{resource.get('name')}: org_policy_present")

            for binding in config.get("iam_bindings", []):
                members = binding.get("members", [])
                if any(member in {"allUsers", "allAuthenticatedUsers", "*"} for member in members):
                    wildcard_member_found = True

        vpcs: List[Dict[str, Any]] = []
        subnets: List[Dict[str, Any]] = []
        firewall_rule_count = 0
        route_count = 0

        for item in catalog:
            source_type = item.get("source_type")
            network_context = item.get("network_context", {})

            if source_type == "google_compute_network":
                vpcs.append(
                    {
                        "id": item.get("name"),
                        "cidr": None,
                        "target_vpc_name": f"{self.project_id}-vpc",
                    }
                )
            elif source_type == "google_compute_subnetwork":
                subnets.append(
                    {
                        "id": item.get("name"),
                        "cidr": item.get("data_context", {}).get("cidr"),
                        "zone": item.get("where_it_runs", {}).get("zone"),
                        "region": item.get("where_it_runs", {}).get("region"),
                        "network": network_context.get("vpc"),
                        "target_subnet_tier": self._suggest_placement(item),
                    }
                )
            elif source_type == "google_compute_firewall":
                firewall_rule_count += 1
            elif source_type == "google_compute_route":
                route_count += 1

        subnet_by_network: Dict[str, List[str]] = {}
        for subnet in subnets:
            network_name = subnet.get("network")
            cidr = subnet.get("cidr")
            if network_name and cidr:
                subnet_by_network.setdefault(network_name, []).append(cidr)

        for vpc in vpcs:
            network_id = vpc.get("id")
            if not isinstance(network_id, str):
                continue
            cidrs = subnet_by_network.get(network_id, [])
            if cidrs:
                vpc["cidr"] = cidrs[0]

        internet_ingress = any(
            "0.0.0.0/0" in (item.get("network_context", {}).get("sources") or [])
            and str(item.get("network_context", {}).get("direction", "")).lower() == "ingress"
            for item in catalog
            if item.get("source_type") == "google_compute_firewall"
        )

        service_accounts: List[Dict[str, Any]] = []
        for item in catalog:
            if item.get("source_type") != "google_service_account":
                continue
            roles = [str(role) for role in item.get("identity_context", {}).get("iam_roles", []) if role]
            service_accounts.append(
                {
                    "name": item.get("name"),
                    "roles": sorted(set(roles)),
                }
            )

        aws_roles = []
        for account in service_accounts:
            aws_roles.append(
                {
                    "name": self._extract_role_from_service_account(account.get("name", "workload")),
                    "managed_policies": ["CloudWatchAgentServerPolicy"],
                }
            )

        kms_entries = []
        for item in catalog:
            for kms_key in item.get("security_context", {}).get("kms_keys", []):
                kms_entries.append(
                    {
                        "source": kms_key,
                        "target_alias": f"alias/{self.project_id}-{item.get('name', 'resource')}",
                    }
                )

        compute_resource_ids = [item.get("id") for item in catalog if item.get("plane") == "compute_plane"]
        data_resource_ids = [item.get("id") for item in catalog if item.get("plane") == "data_plane"]
        identity_resource_ids = [
            item.get("id") for item in catalog if item.get("plane") == "identity_plane"
        ]
        network_resource_ids = [item.get("id") for item in catalog if item.get("plane") == "network_plane"]

        logging_enabled = any(
            "logging" in service.lower()
            for service in enabled_services
        )

        compliance_tags = sorted(
            {
                tag
                for item in catalog
                for tag in item.get("security_context", {}).get("compliance_tags", [])
            }
        )

        return {
            "control_plane": {
                "policy_constraints_count": len(policy_constraints),
                "critical_policy_hints": policy_constraints[:10],
                "iam_governance": {
                    "least_privilege": not wildcard_member_found,
                    "break_glass_role": "platform-admin",
                },
                "change_controls": {
                    "terraform_managed": None,
                    "approval_required_for_prod": True,
                },
                "api_enablement": {
                    "enabled_services": enabled_services,
                },
            },
            "network_plane": {
                "vpcs": vpcs,
                "subnets": subnets,
                "route_count": route_count,
                "firewall_rule_count": firewall_rule_count,
                "exposure_model": {
                    "internet_ingress_via_lb_only": not internet_ingress,
                    "nat_for_private_egress": None,
                },
                "aws_target_controls": {
                    "security_groups_required": True,
                    "nacl_strategy": "default-plus-explicit-deny",
                    "route53_private_zone": f"{self.project_id}.internal",
                },
                "resource_ids": [resource_id for resource_id in network_resource_ids if resource_id],
            },
            "identity_plane": {
                "service_accounts": service_accounts,
                "aws_target_principals": {
                    "iam_roles": aws_roles,
                },
                "resource_ids": [resource_id for resource_id in identity_resource_ids if resource_id],
            },
            "compute_plane": {
                "resource_ids": [resource_id for resource_id in compute_resource_ids if resource_id],
            },
            "data_plane": {
                "resource_ids": [resource_id for resource_id in data_resource_ids if resource_id],
                "rpo_rto": {
                    "rpo_minutes": 15 if data_resource_ids else 60,
                    "rto_minutes": 60 if data_resource_ids else 120,
                },
            },
            "security_plane": {
                "encryption": {
                    "at_rest_required": True,
                    "in_transit_required": True,
                    "kms_keys": kms_entries,
                },
                "secrets": {
                    "source_manager": "gcp_secret_manager",
                    "target_manager": "aws_secrets_manager",
                    "rotation_days": 30,
                },
                "edge_protection": {
                    "waf_required_for_public_http": internet_ingress,
                    "ddos_protection_required": internet_ingress,
                },
                "compliance_tags": compliance_tags,
            },
            "operations_plane": {
                "logging": {
                    "centralized": logging_enabled,
                    "retention_days": 30,
                },
                "metrics": {
                    "sli": ["latency_p95", "error_rate", "cpu", "db_connections"],
                    "alerts": ["high_error_rate", "db_storage_low"],
                },
                "slo": {
                    "availability": "99.9%",
                    "latency_p95_ms": 300,
                },
                "dependency_edges": len(dependencies),
            },
        }

    def _build_resource_catalog(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        catalog: List[Dict[str, Any]] = []

        for resource in resources:
            config = resource.get("config", {})
            category = resource.get("category", "unknown")
            asset_type = resource.get("asset_type", "unknown")
            plane = self.PLANE_BY_CATEGORY.get(category, "operations_plane")
            source_type = self._source_type_for_asset(asset_type)

            aws_service = config.get("migration_hint", {}).get("aws_service")
            aws_service_family = self._normalize_aws_service_family(aws_service)

            zone = config.get("zone")
            region = config.get("region") or self._zone_to_region(zone or resource.get("location"))

            primary_network = {}
            networks = config.get("networks", [])
            if isinstance(networks, list) and networks:
                primary_network = networks[0] if isinstance(networks[0], dict) else {}

            data_engine = config.get("engine") or config.get("runtime") or config.get("format")
            state_value = config.get("status") or config.get("state")
            service_accounts = [
                item.get("email")
                for item in config.get("service_accounts", [])
                if isinstance(item, dict) and item.get("email")
            ]

            if config.get("service_account"):
                service_accounts.append(config.get("service_account"))
            if config.get("service_account_email"):
                service_accounts.append(config.get("service_account_email"))
            service_accounts = [str(account) for account in service_accounts if account]

            iam_roles = sorted(
                {
                    str(binding.get("role"))
                    for binding in config.get("iam_bindings", [])
                    if isinstance(binding, dict) and binding.get("role")
                }
            )

            firewall_ports: List[str] = []
            firewall_protocol = None
            allowed_rules = config.get("allowed", [])
            if isinstance(allowed_rules, list):
                for allowed in allowed_rules:
                    if not isinstance(allowed, dict):
                        continue
                    firewall_protocol = firewall_protocol or allowed.get("IPProtocol")
                    for port in allowed.get("ports", []):
                        firewall_ports.append(str(port))

            item = {
                "id": "",
                "source_asset_id": resource.get("id"),
                "project_id": resource.get("project") or self.project_id,
                "project_name": self._resource_project_name(resource),
                "source_type": source_type,
                "name": resource.get("name"),
                "category": category,
                "plane": plane,
                "what_it_is": {
                    "asset_type": asset_type,
                    "source_type": source_type,
                    "subtype": data_engine,
                },
                "what_it_does": {
                    "workload_intent": self._guess_workload_intent(category, source_type),
                    "state": state_value,
                },
                "where_it_runs": {
                    "location": resource.get("location"),
                    "region": region,
                    "zone": zone,
                    "global": str(resource.get("location", "")).lower() == "global",
                },
                "network_context": {
                    "vpc": primary_network.get("network") or config.get("network"),
                    "subnet": primary_network.get("subnetwork") or config.get("subnetwork"),
                    "public_exposure": self._infer_public_exposure(resource),
                    "direction": config.get("direction"),
                    "protocol": firewall_protocol,
                    "ports": sorted(set(firewall_ports)),
                    "sources": config.get("source_ranges", []),
                    "destination_range": config.get("destination_range"),
                    "next_hop": config.get("next_hop"),
                },
                "identity_context": {
                    "service_accounts": sorted(set(service_accounts)),
                    "iam_roles": iam_roles,
                },
                "data_context": {
                    "stateful": category in {"database", "storage", "analytics", "messaging", "registry"}
                    or source_type in {"google_compute_disk", "google_sql_database_instance"},
                    "engine": data_engine,
                    "engine_version": config.get("engine_version"),
                    "storage_class": config.get("storage_class"),
                    "retention": config.get("retention_policy") or config.get("backup"),
                    "state": config.get("state"),
                    "cidr": config.get("ip_cidr_range"),
                },
                "security_context": {
                    "encryption_at_rest": bool(config.get("encrypted") or self._collect_kms_key_refs(config)),
                    "encryption_in_transit": True,
                    "kms_keys": self._collect_kms_key_refs(config),
                    "compliance_tags": self._collect_compliance_tags(resource.get("labels", {})),
                },
                "operations_context": {
                    "status": state_value,
                    "logging_enabled": "logging" in str(config.get("labels", {})).lower(),
                    "monitoring_enabled": bool(state_value),
                },
                "target_preferences": {
                    "aws_service_family": aws_service_family or ["Review Required"],
                    "placement": self._suggest_placement(
                        {
                            "category": category,
                            "network_context": {
                                "public_exposure": self._infer_public_exposure(resource),
                            },
                        }
                    ),
                    "notes": config.get("migration_hint", {}).get("notes"),
                },
                "migration_details": self._build_migration_details(
                    source_type=source_type,
                    category=category,
                    config=config,
                ),
                "migration_constraints": {
                    "downtime_tolerance_minutes": self._resource_downtime_tolerance(category),
                    "cutover_strategy": self._resource_cutover_strategy(category),
                    "budget_tier": "balanced",
                },
                "dependencies": [
                    str(dep) for dep in resource.get("dependencies", []) if dep
                ],
            }
            item["is_core_migration_resource"] = self._is_core_migration_resource(item)
            catalog.append(item)

        catalog.sort(
            key=lambda item: (
                item.get("plane", "unknown"),
                item.get("source_type", "unknown"),
                item.get("name", ""),
            )
        )

        for index, item in enumerate(catalog, start=1):
            item["id"] = f"res-{self._plane_prefix(item.get('plane', 'unknown'))}-{index}"

        return catalog

    def _resource_project_name(self, resource: Dict[str, Any]) -> str:
        config = resource.get("config", {})
        search_metadata = config.get("search_metadata", {})
        asset_type = resource.get("asset_type")
        project_id = str(resource.get("project") or self.project_id)

        if isinstance(search_metadata, dict):
            project_value = search_metadata.get("project")
            if project_value:
                text = str(project_value)
                return text.rsplit("/", 1)[-1] if "/" in text else text

        if asset_type in {
            "cloudresourcemanager.googleapis.com/Project",
            "compute.googleapis.com/Project",
        }:
            for key in ("name", "project_id"):
                value = config.get(key)
                if value:
                    return str(value)

        return project_id

    def _build_dependency_graph(self, catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not catalog:
            return []

        by_source_asset = {
            item.get("source_asset_id"): item for item in catalog if item.get("source_asset_id")
        }
        by_name = {
            item.get("name"): item for item in catalog if item.get("name")
        }

        dependencies: List[Dict[str, Any]] = []
        seen = set()

        for source in catalog:
            for dep_ref in source.get("dependencies", []):
                target = by_source_asset.get(dep_ref)
                if target is None:
                    target = by_name.get(self._extract_name_from_asset(dep_ref))
                if target is None:
                    continue
                if source.get("id") == target.get("id"):
                    continue

                relationship, protocol, port = self._infer_dependency_shape(source, target)
                edge_key = (source.get("id"), target.get("id"), relationship, protocol, port)
                if edge_key in seen:
                    continue
                seen.add(edge_key)

                dependencies.append(
                    {
                        "source_id": source.get("id"),
                        "target_id": target.get("id"),
                        "relationship": relationship,
                        "protocol": protocol,
                        "port": port,
                        "required": True,
                    }
                )

        return dependencies

    def _infer_dependency_shape(
        self,
        source: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Tuple[str, str, int]:
        target_source_type = str(target.get("source_type", ""))
        target_engine = str(target.get("data_context", {}).get("engine", "")).lower()

        if target_source_type == "google_storage_bucket":
            return "reads_from", "https", 443

        if "sql_database_instance" in target_source_type or target_engine in {
            "postgres",
            "mysql",
            "sqlserver",
        }:
            if "mysql" in target_engine:
                return "connects_to", "tcp", 3306
            if "sqlserver" in target_engine:
                return "connects_to", "tcp", 1433
            return "connects_to", "tcp", 5432

        if target_source_type == "google_project_service":
            return "uses_service_api", "https", 443

        return "depends_on", "https", 443

    def _source_type_for_asset(self, asset_type: str) -> str:
        source_type = self.SOURCE_TYPE_BY_ASSET.get(asset_type)
        if source_type:
            return source_type
        return self._asset_type_to_source_type(asset_type)

    def _asset_type_to_source_type(self, asset_type: str) -> str:
        if not asset_type or "/" not in asset_type:
            return "google_unknown_resource"

        service, kind = asset_type.split("/", 1)
        service_name = service.split(".")[0].replace("-", "_")
        kind_snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", kind).replace("-", "_").lower()
        return f"google_{service_name}_{kind_snake}"

    def _guess_workload_intent(self, category: str, source_type: str) -> str:
        if category == "database":
            return "oltp_database"
        if category == "storage":
            return "static_assets"
        if category in {"serverless", "messaging"}:
            return "event_processing"
        if category == "analytics":
            return "analytics_processing"
        if category == "network":
            return "traffic_routing"
        if "service_account" in source_type:
            return "workload_identity"
        return "application_runtime"

    def _resource_downtime_tolerance(self, category: str) -> int:
        if category == "database":
            return 10
        if category in {"storage", "analytics"}:
            return 30
        return 20

    def _resource_cutover_strategy(self, category: str) -> str:
        if category in {"database", "analytics", "storage"}:
            return "replicate_then_cutover"
        return "blue_green"

    def _build_migration_details(
        self,
        source_type: str,
        category: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        raw_data = config.get("raw_data")
        raw = raw_data if isinstance(raw_data, dict) else {}
        aws_targets = self._normalize_aws_service_family(
            config.get("migration_hint", {}).get("aws_service")
        ) or ["Review Required"]

        base = {
            "aws_target_candidates": aws_targets,
            "migration_notes": config.get("migration_hint", {}).get("notes"),
        }

        if source_type == "google_compute_instance":
            network_interfaces = []
            for network in config.get("networks", []):
                if not isinstance(network, dict):
                    continue
                network_interfaces.append(
                    {
                        "network": network.get("network"),
                        "subnetwork": network.get("subnetwork"),
                        "internal_ip": network.get("internal_ip"),
                        "external_ip": network.get("external_ip"),
                    }
                )

            disk_profiles = []
            for disk in config.get("disks", []):
                if not isinstance(disk, dict):
                    continue
                disk_profiles.append(
                    {
                        "name": disk.get("device_name"),
                        "source": disk.get("source"),
                        "mode": disk.get("mode"),
                        "type": disk.get("type"),
                        "boot": bool(disk.get("boot")),
                    }
                )

            return {
                **base,
                "service_configuration": {
                    "machine_type": config.get("machine_type"),
                    "cpu_platform": config.get("cpu_platform"),
                    "os_hint": self._infer_compute_os_hint(config),
                    "zone": config.get("zone"),
                    "region": config.get("region"),
                    "network_interfaces": network_interfaces,
                    "disk_profiles": disk_profiles,
                    "service_accounts": [
                        account.get("email")
                        for account in config.get("service_accounts", [])
                        if isinstance(account, dict) and account.get("email")
                    ],
                    "startup_script_present": bool(config.get("startup_script")),
                    "shielded_vm": config.get("shielded_vm", {}),
                    "confidential_computing": config.get("confidential_computing", {}),
                },
            }

        if source_type == "google_compute_disk":
            return {
                **base,
                "service_configuration": {
                    "disk_type": config.get("type"),
                    "size_gb": self._safe_int(config.get("size_gb")),
                    "zone": config.get("zone"),
                    "region": config.get("region"),
                    "encrypted": bool(config.get("encrypted")),
                    "attached_users": config.get("users", []),
                },
            }

        if source_type == "google_cloud_run_v2_service":
            template = raw.get("template", {}) if isinstance(raw, dict) else {}
            scaling = template.get("scaling", {}) if isinstance(template, dict) else {}

            return {
                **base,
                "service_configuration": {
                    "location": config.get("location"),
                    "ingress": config.get("ingress"),
                    "service_url": config.get("service_url"),
                    "container_images": config.get("container_images", []),
                    "service_account": config.get("service_account"),
                    "vpc_connector": config.get("vpc_connector"),
                    "min_instance_count": self._safe_int(scaling.get("minInstanceCount")),
                    "max_instance_count": self._safe_int(scaling.get("maxInstanceCount")),
                },
            }

        if source_type == "google_cloudfunctions2_function":
            trigger = config.get("trigger", {}) if isinstance(config.get("trigger"), dict) else {}
            return {
                **base,
                "service_configuration": {
                    "runtime": config.get("runtime"),
                    "entry_point": config.get("entry_point"),
                    "region": config.get("region"),
                    "service_account_email": config.get("service_account_email"),
                    "available_memory_mb": self._safe_int(config.get("available_memory_mb")),
                    "timeout": config.get("timeout"),
                    "trigger": trigger,
                },
            }

        if source_type == "google_container_cluster":
            node_pool_profiles = []
            for node_pool in raw.get("nodePools", []):
                if not isinstance(node_pool, dict):
                    continue
                node_config = node_pool.get("config", {})
                if not isinstance(node_config, dict):
                    node_config = {}
                node_pool_profiles.append(
                    {
                        "name": node_pool.get("name"),
                        "machine_type": node_config.get("machineType"),
                        "disk_size_gb": self._safe_int(node_config.get("diskSizeGb")),
                        "image_type": node_config.get("imageType"),
                        "initial_node_count": self._safe_int(node_pool.get("initialNodeCount")),
                    }
                )

            autopilot = raw.get("autopilot", {}) if isinstance(raw.get("autopilot"), dict) else {}
            release_channel = raw.get("releaseChannel", {})
            if not isinstance(release_channel, dict):
                release_channel = {}

            return {
                **base,
                "service_configuration": {
                    "location": raw.get("location") or config.get("location"),
                    "release_channel": release_channel.get("channel"),
                    "master_version": raw.get("currentMasterVersion"),
                    "node_version": raw.get("currentNodeVersion"),
                    "autopilot_enabled": bool(autopilot.get("enabled")),
                    "network": self._short_name(raw.get("network")),
                    "subnetwork": self._short_name(raw.get("subnetwork")),
                    "node_pools": node_pool_profiles,
                },
            }

        if source_type == "google_artifact_registry_repository":
            cleanup_policies = config.get("cleanup_policies", {})
            cleanup_count = len(cleanup_policies) if isinstance(cleanup_policies, dict) else 0
            return {
                **base,
                "service_configuration": {
                    "location": config.get("location"),
                    "format": config.get("format"),
                    "mode": config.get("mode"),
                    "kms_key_name": config.get("kms_key_name"),
                    "cleanup_policy_count": cleanup_count,
                },
            }

        if source_type == "google_bigquery_dataset":
            dataset_ref = raw.get("datasetReference", {}) if isinstance(raw.get("datasetReference"), dict) else {}
            return {
                **base,
                "service_configuration": {
                    "dataset_id": dataset_ref.get("datasetId") or config.get("name"),
                    "location": raw.get("location") or config.get("location"),
                    "default_table_expiration_ms": raw.get("defaultTableExpirationMs"),
                    "default_partition_expiration_ms": raw.get("defaultPartitionExpirationMs"),
                },
            }

        if source_type == "google_bigquery_table":
            table_ref = raw.get("tableReference", {}) if isinstance(raw.get("tableReference"), dict) else {}
            schema = raw.get("schema", {}) if isinstance(raw.get("schema"), dict) else {}
            schema_fields = schema.get("fields", []) if isinstance(schema.get("fields"), list) else []
            partitioning = raw.get("timePartitioning", {})
            if not isinstance(partitioning, dict):
                partitioning = {}
            clustering = raw.get("clustering", {})
            if not isinstance(clustering, dict):
                clustering = {}

            return {
                **base,
                "service_configuration": {
                    "dataset_id": table_ref.get("datasetId"),
                    "table_id": table_ref.get("tableId") or config.get("name"),
                    "table_type": raw.get("type"),
                    "schema_field_count": len(schema_fields),
                    "partitioning_type": partitioning.get("type"),
                    "partition_expiration_ms": partitioning.get("expirationMs"),
                    "clustering_fields": clustering.get("fields", []),
                    "size_bytes": self._safe_int(raw.get("numBytes")),
                    "row_count": self._safe_int(raw.get("numRows")),
                },
            }

        if source_type == "google_firestore_database":
            return {
                **base,
                "service_configuration": {
                    "database_type": raw.get("type"),
                    "location_id": raw.get("locationId") or config.get("location"),
                    "concurrency_mode": raw.get("concurrencyMode"),
                    "app_engine_integration_mode": raw.get("appEngineIntegrationMode"),
                    "point_in_time_recovery": raw.get("pointInTimeRecoveryEnablement"),
                },
            }

        if source_type == "google_pubsub_topic":
            message_storage_policy = raw.get("messageStoragePolicy", {})
            if not isinstance(message_storage_policy, dict):
                message_storage_policy = {}
            return {
                **base,
                "service_configuration": {
                    "topic_name": raw.get("name") or config.get("name"),
                    "kms_key_name": raw.get("kmsKeyName"),
                    "schema_settings": raw.get("schemaSettings", {}),
                    "allowed_persistence_regions": message_storage_policy.get(
                        "allowedPersistenceRegions",
                        [],
                    ),
                },
            }

        if source_type == "google_pubsub_subscription":
            dead_letter_policy = raw.get("deadLetterPolicy", {})
            if not isinstance(dead_letter_policy, dict):
                dead_letter_policy = {}
            retry_policy = raw.get("retryPolicy", {})
            if not isinstance(retry_policy, dict):
                retry_policy = {}
            push_config = raw.get("pushConfig", {})
            if not isinstance(push_config, dict):
                push_config = {}

            return {
                **base,
                "service_configuration": {
                    "topic": self._short_name(raw.get("topic")),
                    "ack_deadline_seconds": self._safe_int(raw.get("ackDeadlineSeconds")),
                    "message_retention_duration": raw.get("messageRetentionDuration"),
                    "dead_letter_topic": self._short_name(dead_letter_policy.get("deadLetterTopic")),
                    "max_delivery_attempts": self._safe_int(
                        dead_letter_policy.get("maxDeliveryAttempts")
                    ),
                    "push_endpoint": push_config.get("pushEndpoint"),
                    "retry_policy": retry_policy,
                },
            }

        if source_type == "google_redis_instance":
            persistence_config = raw.get("persistenceConfig", {})
            if not isinstance(persistence_config, dict):
                persistence_config = {}
            return {
                **base,
                "service_configuration": {
                    "tier": raw.get("tier"),
                    "memory_size_gb": self._safe_int(raw.get("memorySizeGb"))
                    or self._safe_int(config.get("memory_size_gb")),
                    "redis_version": raw.get("redisVersion"),
                    "authorized_network": self._short_name(raw.get("authorizedNetwork")),
                    "connect_mode": raw.get("connectMode"),
                    "transit_encryption_mode": raw.get("transitEncryptionMode"),
                    "persistence_mode": persistence_config.get("persistenceMode"),
                },
            }

        if source_type == "google_sql_database_instance":
            settings = raw.get("settings", {})
            if not isinstance(settings, dict):
                settings = {}
            ip_config = settings.get("ipConfiguration", {})
            if not isinstance(ip_config, dict):
                ip_config = {}
            backup_config = settings.get("backupConfiguration", {})
            if not isinstance(backup_config, dict):
                backup_config = {}
            return {
                **base,
                "service_configuration": {
                    "database_version": raw.get("databaseVersion"),
                    "tier": settings.get("tier"),
                    "availability_type": settings.get("availabilityType"),
                    "disk_type": settings.get("dataDiskType"),
                    "disk_size_gb": self._safe_int(settings.get("dataDiskSizeGb")),
                    "private_network": self._short_name(ip_config.get("privateNetwork")),
                    "ipv4_enabled": bool(ip_config.get("ipv4Enabled")),
                    "backup_enabled": bool(backup_config.get("enabled")),
                },
            }

        if source_type == "google_storage_bucket":
            lifecycle_rules = config.get("lifecycle_rules", [])
            lifecycle_count = len(lifecycle_rules) if isinstance(lifecycle_rules, list) else 0
            return {
                **base,
                "service_configuration": {
                    "location": config.get("location"),
                    "storage_class": config.get("storage_class"),
                    "versioning_enabled": bool(config.get("versioning_enabled")),
                    "uniform_bucket_level_access": bool(config.get("uniform_bucket_level_access")),
                    "public_access_prevention": config.get("public_access_prevention"),
                    "lifecycle_rule_count": lifecycle_count,
                },
            }

        return {
            **base,
            "service_configuration": {
                "category": category,
                "state": config.get("status") or config.get("state"),
                "location": config.get("location"),
            },
        }

    def _infer_compute_os_hint(self, config: Dict[str, Any]) -> Optional[str]:
        raw_data = config.get("raw_data")
        raw = raw_data if isinstance(raw_data, dict) else {}

        disks = raw.get("disks", []) if isinstance(raw.get("disks"), list) else []
        for disk in disks:
            if not isinstance(disk, dict) or not disk.get("boot"):
                continue

            licenses = disk.get("licenses", [])
            if isinstance(licenses, list) and licenses:
                license_name = self._short_name(licenses[0])
                if license_name:
                    return license_name

            initialize_params = disk.get("initializeParams", {})
            if isinstance(initialize_params, dict):
                source_image = initialize_params.get("sourceImage")
                if source_image:
                    return self._short_name(source_image)

        boot_disk = config.get("boot_disk", {})
        if isinstance(boot_disk, dict):
            source_name = boot_disk.get("source")
            if source_name:
                return self._short_name(source_name)

        return None

    def _infer_public_exposure(self, resource: Dict[str, Any]) -> bool:
        config = resource.get("config", {})
        asset_type = resource.get("asset_type")

        if asset_type == "compute.googleapis.com/Firewall":
            source_ranges = config.get("source_ranges", [])
            if isinstance(source_ranges, list) and "0.0.0.0/0" in source_ranges:
                return True

        networks = config.get("networks", [])
        if isinstance(networks, list):
            for network in networks:
                if isinstance(network, dict) and network.get("external_ip"):
                    return True

        service_url = config.get("service_url") or config.get("trigger", {}).get("https_url")
        if service_url:
            return True

        return False

    def _normalize_aws_service_family(self, aws_service: Any) -> List[str]:
        if not aws_service:
            return []

        if isinstance(aws_service, list):
            raw_values = [str(item) for item in aws_service]
        else:
            raw_values = [str(aws_service)]

        families: List[str] = []
        for value in raw_values:
            parts = re.split(r"[|/,]", value)
            for part in parts:
                cleaned = part.strip()
                if cleaned:
                    families.append(cleaned)

        return sorted(set(families))

    def _suggest_placement(self, item: Dict[str, Any]) -> str:
        category = item.get("category")
        is_public = bool(item.get("network_context", {}).get("public_exposure"))

        if is_public:
            return "public-edge"
        if category in {"database", "network", "security"}:
            return "private-db"
        if category in {"serverless", "compute", "container", "messaging"}:
            return "private-app"
        return "private-core"

    def _extract_role_from_service_account(self, service_account_name: str) -> str:
        if not service_account_name:
            return "workload-role"

        local_name = service_account_name.split("@")[0]
        sanitized = re.sub(r"[^a-zA-Z0-9-]", "-", local_name).strip("-")
        return f"{sanitized or 'workload'}-role"

    def _collect_kms_key_refs(self, config: Dict[str, Any]) -> List[str]:
        kms_keys: List[str] = []

        for key in ("kms_key_name", "kmsKeyName"):
            value = config.get(key)
            if value:
                kms_keys.append(str(value))

        disk_key = config.get("diskEncryptionKey")
        if isinstance(disk_key, dict):
            for nested_key in ("kmsKeyName", "kms_key_name", "rawKey"):
                nested_value = disk_key.get(nested_key)
                if nested_value:
                    kms_keys.append(str(nested_value))

        return sorted(set(kms_keys))

    def _collect_compliance_tags(self, labels: Dict[str, Any]) -> List[str]:
        if not isinstance(labels, dict):
            return []

        tags: List[str] = []
        for key, value in labels.items():
            lowered = str(key).lower()
            if any(term in lowered for term in ("compliance", "pci", "hipaa", "gdpr", "sox", "iso")):
                tags.append(f"{key}:{value}")

        return sorted(tags)

    def _plane_prefix(self, plane_name: str) -> str:
        mapping = {
            "control_plane": "ctl",
            "network_plane": "net",
            "identity_plane": "id",
            "compute_plane": "cmp",
            "data_plane": "data",
            "security_plane": "sec",
            "operations_plane": "ops",
        }
        return mapping.get(plane_name, "res")

    def _has_excluded_core_name_pattern(self, item: Dict[str, Any]) -> bool:
        source_type = str(item.get("source_type", ""))
        name = str(item.get("name", "")).lower()

        if not name:
            return False

        excluded_tokens = self.CORE_EXCLUDED_NAME_SUBSTRINGS_BY_SOURCE_TYPE.get(source_type, ())
        return any(token in name for token in excluded_tokens)

    def _is_core_migration_resource(self, item: Dict[str, Any]) -> bool:
        source_type = str(item.get("source_type", ""))

        if source_type in self.CORE_EXCLUDED_SOURCE_TYPES:
            return False

        if source_type.endswith("_revision") or source_type.endswith("_version"):
            return False

        if source_type not in self.CORE_INCLUDED_SOURCE_TYPES:
            return False

        if self._has_excluded_core_name_pattern(item):
            return False

        return True

    def build_terraform_file(
        self,
        raw_assets: Optional[List[Dict[str, Any]]] = None,
        core_only: bool = True,
    ) -> str:
        """Render a Terraform template of discovered GCP architecture and dependencies."""
        blueprint = self.build_migration_blueprint(raw_assets=raw_assets)
        catalog = list(blueprint.get("resource_catalog", []))
        dependencies = list(blueprint.get("dependencies", []))

        if core_only:
            catalog = [item for item in catalog if item.get("is_core_migration_resource")]
            core_ids = {
                item.get("id") for item in catalog if item.get("id")
            }
            dependencies = [
                edge
                for edge in dependencies
                if edge.get("source_id") in core_ids and edge.get("target_id") in core_ids
            ]

        entries = []
        name_counts: Dict[str, int] = {}

        for item in catalog:
            source_type = str(item.get("source_type", ""))
            tf_type = self._tf_type_for_source(source_type)
            if not tf_type:
                continue

            seed = str(item.get("name") or item.get("id") or "resource")
            base_name = self._tf_identifier(seed)
            count = name_counts.get(base_name, 0) + 1
            name_counts[base_name] = count
            tf_name = base_name if count == 1 else f"{base_name}_{count}"

            entries.append(
                {
                    "item": item,
                    "tf_type": tf_type,
                    "tf_name": tf_name,
                    "ref": f"{tf_type}.{tf_name}",
                }
            )

        by_id = {
            entry["item"].get("id"): entry
            for entry in entries
            if entry["item"].get("id")
        }

        dependency_map: Dict[str, List[str]] = {}
        for edge in dependencies:
            source_id = edge.get("source_id")
            target_id = edge.get("target_id")
            if source_id not in by_id or target_id not in by_id:
                continue

            source_ref = by_id[source_id]["ref"]
            target_ref = by_id[target_id]["ref"]
            dependency_map.setdefault(source_ref, [])
            if target_ref not in dependency_map[source_ref]:
                dependency_map[source_ref].append(target_ref)

        header_lines = [
            'terraform {',
            '  required_providers {',
            '    google = {',
            '      source  = "hashicorp/google"',
            '      version = "~> 5.0"',
            '    }',
            '  }',
            '}',
            '',
            'provider "google" {',
            '  project = var.project_id',
            '  region  = var.default_region',
            '}',
            '',
            'variable "project_id" {',
            '  description = "GCP project id"',
            '  type        = string',
            '}',
            '',
            'variable "default_region" {',
            '  description = "Default GCP region"',
            '  type        = string',
            '  default     = "us-central1"',
            '}',
            '',
        ]

        block_lines: List[str] = []
        for entry in entries:
            attrs = self._terraform_attributes(entry["tf_type"], entry["item"], by_id)
            depends_on = dependency_map.get(entry["ref"], [])
            block_lines.extend(
                self._render_terraform_block(
                    tf_type=entry["tf_type"],
                    tf_name=entry["tf_name"],
                    attributes=attrs,
                    depends_on=depends_on,
                )
            )
            block_lines.append("")

        return "\n".join(header_lines + block_lines).rstrip() + "\n"

    def _tf_type_for_source(self, source_type: str) -> Optional[str]:
        mapping = {
            "google_compute_disk": "google_compute_disk",
            "google_compute_instance": "google_compute_instance",
            "google_storage_bucket": "google_storage_bucket",
            "google_artifact_registry_repository": "google_artifact_registry_repository",
            "google_cloud_run_v2_service": "google_cloud_run_v2_service",
            "google_cloudfunctions2_function": "google_cloudfunctions2_function",
            "google_sql_database_instance": "google_sql_database_instance",
            "google_redis_instance": "google_redis_instance",
            "google_pubsub_topic": "google_pubsub_topic",
            "google_pubsub_subscription": "google_pubsub_subscription",
            "google_bigquery_dataset": "google_bigquery_dataset",
            "google_bigquery_table": "google_bigquery_table",
            "google_firestore_database": "google_firestore_database",
            "google_container_cluster": "google_container_cluster",
        }
        return mapping.get(source_type)

    def _tf_identifier(self, value: str) -> str:
        text = re.sub(r"[^a-zA-Z0-9_]", "_", value.lower())
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            text = "resource"
        if text[0].isdigit():
            text = f"r_{text}"
        return text[:48]

    def _render_terraform_block(
        self,
        tf_type: str,
        tf_name: str,
        attributes: List[str],
        depends_on: List[str],
    ) -> List[str]:
        lines = [f'resource "{tf_type}" "{tf_name}" {{']
        lines.extend([f"  {line}" for line in attributes])

        if depends_on:
            depends_joined = ", ".join(depends_on)
            lines.append(f"  depends_on = [{depends_joined}]")

        lines.append("}")
        return lines

    def _terraform_attributes(
        self,
        tf_type: str,
        item: Dict[str, Any],
        by_id: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        name = str(item.get("name") or item.get("id") or "resource")
        where = item.get("where_it_runs", {})
        network = item.get("network_context", {})
        data = item.get("data_context", {})
        identity = item.get("identity_context", {})

        region = str(where.get("region") or "${var.default_region}")
        zone = str(where.get("zone") or f"{region}-a")

        if tf_type == "google_compute_disk":
            return [
                f'name  = "{name}"',
                f'type  = "pd-balanced"',
                f'zone  = "{zone}"',
                "size  = 50",
            ]

        if tf_type == "google_compute_instance":
            machine_type = "n2-standard-2"
            subtype = item.get("what_it_is", {}).get("subtype")
            if isinstance(subtype, str) and subtype:
                machine_type = subtype

            attrs = [
                f'name         = "{name}"',
                f'machine_type = "{machine_type}"',
                f'zone         = "{zone}"',
                "boot_disk {",
                "  initialize_params {",
                '    image = "debian-cloud/debian-11"',
                "  }",
                "}",
                "network_interface {",
            ]

            subnet = network.get("subnet")
            if subnet:
                attrs.append(f'  subnetwork = "{subnet}"')
            else:
                attrs.append('  network = "default"')

            if network.get("public_exposure"):
                attrs.extend([
                    "  access_config {}",
                ])

            attrs.append("}")

            service_accounts = identity.get("service_accounts") or []
            if service_accounts:
                attrs.extend([
                    "service_account {",
                    f'  email  = "{service_accounts[0]}"',
                    '  scopes = ["https://www.googleapis.com/auth/cloud-platform"]',
                    "}",
                ])

            return attrs

        if tf_type == "google_storage_bucket":
            location = str(where.get("region") or "US").upper()
            return [
                f'name          = "{name}"',
                f'location      = "{location}"',
                'storage_class = "STANDARD"',
                "uniform_bucket_level_access = true",
            ]

        if tf_type == "google_artifact_registry_repository":
            repository_id = self._tf_identifier(name)[:63]
            return [
                f'repository_id = "{repository_id}"',
                f'location      = "{region}"',
                'format        = "DOCKER"',
            ]

        if tf_type == "google_cloud_run_v2_service":
            ingress = "INGRESS_TRAFFIC_ALL" if network.get("public_exposure") else "INGRESS_TRAFFIC_INTERNAL_ONLY"
            return [
                f'name     = "{name}"',
                f'location = "{region}"',
                f'ingress  = "{ingress}"',
                "template {",
                "  containers {",
                '    image = "us-docker.pkg.dev/cloudrun/container/hello"',
                "  }",
                "}",
            ]

        if tf_type == "google_cloudfunctions2_function":
            return [
                f'name     = "{name}"',
                f'location = "{region}"',
                "build_config {",
                '  runtime     = "python311"',
                '  entry_point = "handler"',
                "  source {",
                "    storage_source {",
                '      bucket = "REPLACE_ME_BUCKET"',
                '      object = "function.zip"',
                "    }",
                "  }",
                "}",
                "service_config {",
                "  timeout_seconds = 60",
                '  available_memory = "512M"',
                "}",
            ]

        if tf_type == "google_sql_database_instance":
            engine = str(data.get("engine") or "postgres").lower()
            if "mysql" in engine:
                database_version = "MYSQL_8_0"
            elif "sqlserver" in engine:
                database_version = "SQLSERVER_2019_STANDARD"
            else:
                database_version = "POSTGRES_14"

            return [
                f'name             = "{name}"',
                f'database_version = "{database_version}"',
                f'region           = "{region}"',
                "settings {",
                '  tier = "db-custom-1-3840"',
                "  backup_configuration {",
                "    enabled = true",
                "  }",
                "}",
            ]

        if tf_type == "google_redis_instance":
            return [
                f'name           = "{name}"',
                f'region         = "{region}"',
                'tier           = "BASIC"',
                "memory_size_gb = 1",
            ]

        if tf_type == "google_pubsub_topic":
            return [
                f'name = "{name}"',
            ]

        if tf_type == "google_pubsub_subscription":
            topic_name = "REPLACE_ME_TOPIC"
            for dependency in item.get("dependencies", []):
                if "/topics/" in str(dependency):
                    topic_name = str(dependency).split("/topics/")[-1]
                    break

            return [
                f'name  = "{name}"',
                f'topic = "{topic_name}"',
            ]

        if tf_type == "google_bigquery_dataset":
            dataset_id = self._tf_identifier(name)[:1024]
            return [
                f'dataset_id = "{dataset_id}"',
                f'location   = "{region.upper()}"',
            ]

        if tf_type == "google_bigquery_table":
            table_id = self._tf_identifier(name)[:1024]
            return [
                'dataset_id = "REPLACE_ME_DATASET"',
                f'table_id   = "{table_id}"',
                "deletion_protection = false",
                'schema = jsonencode([{name = "id", type = "STRING", mode = "NULLABLE"}])',
            ]

        if tf_type == "google_firestore_database":
            return [
                'name        = "(default)"',
                'location_id = "us-central"',
                'type        = "FIRESTORE_NATIVE"',
            ]

        if tf_type == "google_container_cluster":
            return [
                f'name     = "{name}"',
                f'location = "{region}"',
                "remove_default_node_pool = true",
                "initial_node_count       = 1",
            ]

        return [f'# Unsupported mapping for {tf_type} ({name})']
