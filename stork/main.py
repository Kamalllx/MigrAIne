import os
import time
from collections import Counter
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request

from gcp_discovery import GCPDiscoveryClient


app = Flask(__name__)

_CACHE_TTL_SECONDS = 180
_cache_lock = Lock()
_cache = {
    "timestamp": 0.0,
    "snapshot": None,
    "error": None,
}


def _build_core_inventory_payload(snapshot: dict) -> dict:
    resources = [
        item
        for item in snapshot.get("resource_catalog", [])
        if item.get("is_core_migration_resource")
    ]

    core_ids = {item.get("id") for item in resources if item.get("id")}
    dependencies = [
        edge
        for edge in snapshot.get("dependencies", [])
        if edge.get("source_id") in core_ids and edge.get("target_id") in core_ids
    ]

    by_category = dict(
        sorted(
            Counter(item.get("category", "unknown") for item in resources).items(),
            key=lambda pair: (-pair[1], pair[0]),
        )
    )
    by_source_type = dict(
        sorted(
            Counter(item.get("source_type", "unknown") for item in resources).items(),
            key=lambda pair: (-pair[1], pair[0]),
        )
    )

    project_names = sorted(
        {
            str(item.get("project_name"))
            for item in resources
            if item.get("project_name")
        }
    )

    return {
        "project": snapshot.get("project", {}),
        "resources": resources,
        "dependencies": dependencies,
        "summary": {
            "total_core_resources": len(resources),
            "total_core_dependencies": len(dependencies),
            "by_category": by_category,
            "by_source_type": by_source_type,
            "project_names": project_names,
        },
    }


def _filter_core_items(items: list[dict]) -> list[dict]:
    project_name = request.args.get("project_name")
    category = request.args.get("category")
    source_type = request.args.get("source_type")
    search_term = request.args.get("q", "").strip().lower()
    limit = request.args.get("limit", "0")

    if project_name:
        items = [
            item
            for item in items
            if str(item.get("project_name", "")).lower() == project_name.strip().lower()
        ]

    if category:
        items = [item for item in items if item.get("category") == category]

    if source_type:
        items = [item for item in items if item.get("source_type") == source_type]

    if search_term:

        def _matches(item):
            aws_families = item.get("target_preferences", {}).get("aws_service_family", [])
            haystack = " ".join(
                [
                    str(item.get("project_name", "")),
                    str(item.get("name", "")),
                    str(item.get("source_type", "")),
                    str(item.get("category", "")),
                    str(item.get("where_it_runs", {}).get("region", "")),
                    str(item.get("where_it_runs", {}).get("zone", "")),
                    " ".join(aws_families),
                ]
            ).lower()
            return search_term in haystack

        items = [item for item in items if _matches(item)]

    try:
        parsed_limit = int(limit)
    except ValueError:
        parsed_limit = 0

    if parsed_limit > 0:
        items = items[:parsed_limit]

    return items


def _load_inventory(force_refresh: bool = False):
    now = time.time()

    with _cache_lock:
        cache_age = now - _cache["timestamp"]
        if (
            not force_refresh
            and _cache["snapshot"] is not None
            and cache_age < _CACHE_TTL_SECONDS
        ):
            return _cache["snapshot"], _cache["error"], False, cache_age

    try:
        client = GCPDiscoveryClient()
        snapshot = client.build_migration_blueprint()
        error = None
    except Exception as exc:
        snapshot = None
        error = str(exc)

    with _cache_lock:
        _cache["timestamp"] = time.time()
        _cache["snapshot"] = snapshot
        _cache["error"] = error

    return snapshot, error, True, 0.0


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard.html")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "gcp-migration-dashboard"})


@app.route("/api/inventory")
def inventory():
    refresh = request.args.get("refresh", "0") in {"1", "true", "yes"}
    snapshot, error, refreshed, cache_age = _load_inventory(force_refresh=refresh)

    if error:
        return (
            jsonify(
                {
                    "status": "error",
                    "error": error,
                    "refreshed": refreshed,
                    "cache_age_seconds": cache_age,
                }
            ),
            500,
        )

    if snapshot is None:
        return (
            jsonify(
                {
                    "status": "error",
                    "error": "Inventory cache is empty.",
                    "refreshed": refreshed,
                    "cache_age_seconds": cache_age,
                }
            ),
            500,
        )

    payload = dict(snapshot)
    payload["status"] = "ok"
    payload["refreshed"] = refreshed
    payload["cache_age_seconds"] = round(cache_age, 2)
    return jsonify(payload)


@app.route("/api/resources")
def resources():
    refresh = request.args.get("refresh", "0") in {"1", "true", "yes"}
    snapshot, error, _, _ = _load_inventory(force_refresh=refresh)
    if error:
        return jsonify({"status": "error", "error": error}), 500
    if snapshot is None:
        return jsonify({"status": "error", "error": "Inventory cache is empty."}), 500

    items = list(snapshot.get("resource_catalog", []))
    plane = request.args.get("plane")
    source_type = request.args.get("source_type")
    aws_service = request.args.get("aws_service")
    category = request.args.get("category")
    project_id = request.args.get("project_id")
    project_name = request.args.get("project_name")
    asset_type = request.args.get("asset_type")
    search_term = request.args.get("q", "").strip().lower()
    limit = request.args.get("limit", "0")

    if plane:
        items = [item for item in items if item.get("plane") == plane]

    if source_type:
        items = [item for item in items if item.get("source_type") == source_type]

    if aws_service:
        items = [
            item
            for item in items
            if aws_service in item.get("target_preferences", {}).get("aws_service_family", [])
        ]

    if category:
        items = [item for item in items if item.get("category") == category]

    if project_id:
        items = [item for item in items if item.get("project_id") == project_id]

    if project_name:
        project_name_folded = project_name.strip().lower()
        items = [
            item
            for item in items
            if str(item.get("project_name", "")).lower() == project_name_folded
        ]

    # Backward compatibility with existing callers using asset_type filter.
    if asset_type:
        items = [
            item
            for item in items
            if item.get("what_it_is", {}).get("asset_type") == asset_type
        ]

    if search_term:

        def _matches(item):
            haystack = " ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("source_type", "")),
                    str(item.get("plane", "")),
                    str(item.get("category", "")),
                    str(item.get("project_id", "")),
                    str(item.get("project_name", "")),
                    str(item.get("where_it_runs", {}).get("location", "")),
                    " ".join(item.get("target_preferences", {}).get("aws_service_family", [])),
                ]
            ).lower()
            return search_term in haystack

        items = [item for item in items if _matches(item)]

    try:
        parsed_limit = int(limit)
    except ValueError:
        parsed_limit = 0
    if parsed_limit > 0:
        items = items[:parsed_limit]

    return jsonify(
        {
            "status": "ok",
            "count": len(items),
            "resources": items,
        }
    )


@app.route("/api/dependencies")
def dependency_edges():
    refresh = request.args.get("refresh", "0") in {"1", "true", "yes"}
    snapshot, error, _, _ = _load_inventory(force_refresh=refresh)
    if error:
        return jsonify({"status": "error", "error": error}), 500
    if snapshot is None:
        return jsonify({"status": "error", "error": "Inventory cache is empty."}), 500

    edges = list(snapshot.get("dependencies", []))
    return jsonify({"status": "ok", "count": len(edges), "dependencies": edges})


@app.route("/api/core-inventory")
def core_inventory():
    refresh = request.args.get("refresh", "0") in {"1", "true", "yes"}
    snapshot, error, _, _ = _load_inventory(force_refresh=refresh)
    if error:
        return jsonify({"status": "error", "error": error}), 500
    if snapshot is None:
        return jsonify({"status": "error", "error": "Inventory cache is empty."}), 500

    core_payload = _build_core_inventory_payload(snapshot)
    items = _filter_core_items(list(core_payload.get("resources", [])))

    filtered_ids = {item.get("id") for item in items if item.get("id")}
    dependencies = [
        edge
        for edge in core_payload.get("dependencies", [])
        if edge.get("source_id") in filtered_ids and edge.get("target_id") in filtered_ids
    ]

    filtered_summary = {
        "total_core_resources": len(items),
        "total_core_dependencies": len(dependencies),
        "by_category": dict(
            sorted(
                Counter(item.get("category", "unknown") for item in items).items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ),
        "by_source_type": dict(
            sorted(
                Counter(item.get("source_type", "unknown") for item in items).items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ),
        "project_names": core_payload.get("summary", {}).get("project_names", []),
    }

    return jsonify(
        {
            "status": "ok",
            "project": core_payload.get("project", {}),
            "summary": filtered_summary,
            "resources": items,
            "dependencies": dependencies,
        }
    )


@app.route("/api/core-migration-json")
def core_migration_json():
    refresh = request.args.get("refresh", "0") in {"1", "true", "yes"}
    snapshot, error, _, _ = _load_inventory(force_refresh=refresh)
    if error:
        return jsonify({"status": "error", "error": error}), 500
    if snapshot is None:
        return jsonify({"status": "error", "error": "Inventory cache is empty."}), 500

    core_payload = _build_core_inventory_payload(snapshot)
    items = _filter_core_items(list(core_payload.get("resources", [])))

    filtered_ids = {item.get("id") for item in items if item.get("id")}
    dependencies = [
        edge
        for edge in core_payload.get("dependencies", [])
        if edge.get("source_id") in filtered_ids and edge.get("target_id") in filtered_ids
    ]

    migration_resources = [
        {
            "id": item.get("id"),
            "project_id": item.get("project_id"),
            "project_name": item.get("project_name"),
            "name": item.get("name"),
            "source_type": item.get("source_type"),
            "category": item.get("category"),
            "region": item.get("where_it_runs", {}).get("region"),
            "zone": item.get("where_it_runs", {}).get("zone"),
            "aws_target_candidates": item.get("target_preferences", {}).get("aws_service_family", []),
            "migration_details": item.get("migration_details", {}),
            "migration_constraints": item.get("migration_constraints", {}),
            "dependencies": item.get("dependencies", []),
        }
        for item in items
    ]

    summary = {
        "total_migration_resources": len(migration_resources),
        "total_migration_dependencies": len(dependencies),
        "by_category": dict(
            sorted(
                Counter(item.get("category", "unknown") for item in migration_resources).items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ),
        "by_source_type": dict(
            sorted(
                Counter(item.get("source_type", "unknown") for item in migration_resources).items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ),
        "project_names": core_payload.get("summary", {}).get("project_names", []),
    }

    return jsonify(
        {
            "status": "ok",
            "project": core_payload.get("project", {}),
            "summary": summary,
            "resources": migration_resources,
            "dependencies": dependencies,
        }
    )


@app.route("/api/core-terraform")
def core_terraform():
    refresh = request.args.get("refresh", "0") in {"1", "true", "yes"}
    terraform_path = Path("gcp_architecture.tf")

    if refresh or not terraform_path.exists():
        try:
            client = GCPDiscoveryClient()
            terraform_text = client.build_terraform_file(core_only=True)
            terraform_path.write_text(terraform_text, encoding="utf-8")
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500
    else:
        terraform_text = terraform_path.read_text(encoding="utf-8")

    return jsonify(
        {
            "status": "ok",
            "line_count": len(terraform_text.splitlines()),
            "terraform": terraform_text,
        }
    )


@app.route("/api/resources/<path:resource_id>")
def resource_details(resource_id: str):
    snapshot, error, _, _ = _load_inventory(force_refresh=False)
    if error:
        return jsonify({"status": "error", "error": error}), 500
    if snapshot is None:
        return jsonify({"status": "error", "error": "Inventory cache is empty."}), 500

    for item in snapshot.get("resource_catalog", []):
        if (
            item.get("id") == resource_id
            or item.get("source_asset_id") == resource_id
            or item.get("name") == resource_id
        ):
            return jsonify({"status": "ok", "resource": item})

    return jsonify({"status": "not_found", "resource_id": resource_id}), 404


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=True,
    )
