import time

from fastapi.testclient import TestClient

import backend_api


def _wait_for_status(client: TestClient, job_id: str, terminal_states: set[str], timeout: float = 5.0):
    start = time.time()
    while time.time() - start < timeout:
        res = client.get(f"/api/migrations/{job_id}")
        assert res.status_code == 200
        data = res.json()
        if data["status"] in terminal_states:
            return data
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {terminal_states} for job {job_id}")


def setup_function():
    with backend_api._jobs_lock:
        backend_api._jobs.clear()


def test_health_endpoint():
    client = TestClient(backend_api.app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_start_and_complete_migration_job(monkeypatch):
    def fake_run_pipeline(terraform: str, verbose: bool = False, log_callback=None):
        assert terraform.strip()
        if log_callback:
            log_callback("[0/4] DB Mapper")
            log_callback("[1/4] Planner")
            log_callback("[2/4] Critic")
            log_callback("[3/4] Refiner")
            log_callback("[4/4] Runbook")
        return {
            "cloudformation": "Resources: {}",
            "final_critique": "{}",
            "runbook": "# Migration Runbook",
            "cycles_used": 1,
        }

    monkeypatch.setattr(backend_api, "run_pipeline", fake_run_pipeline)
    client = TestClient(backend_api.app)

    start_res = client.post("/api/migrations", json={"terraform": 'resource "x" "y" {}'})
    assert start_res.status_code == 200
    started = start_res.json()
    assert started["status"] in {"queued", "running", "completed"}
    assert started["id"]

    done = _wait_for_status(client, started["id"], {"completed"})
    assert done["progress"] == 100
    assert done["result"]["cloudformation"] == "Resources: {}"
    assert any("[4/4]" in log for log in done["logs"])


def test_invalid_payload_returns_422():
    client = TestClient(backend_api.app)
    res = client.post("/api/migrations", json={})
    assert res.status_code == 422


def test_unknown_job_returns_404():
    client = TestClient(backend_api.app)
    res = client.get("/api/migrations/not-real")
    assert res.status_code == 404
    assert res.json()["detail"] == "Job not found"


def test_failed_job_sets_failed_status(monkeypatch):
    def failing_pipeline(terraform: str, verbose: bool = False, log_callback=None):
        if log_callback:
            log_callback("[0/4] DB Mapper")
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(backend_api, "run_pipeline", failing_pipeline)
    client = TestClient(backend_api.app)

    start_res = client.post("/api/migrations", json={"terraform": 'resource "x" "y" {}'})
    assert start_res.status_code == 200
    job_id = start_res.json()["id"]

    failed = _wait_for_status(client, job_id, {"failed"})
    assert failed["error"] == "synthetic failure"


def test_list_migrations(monkeypatch):
    def fake_run_pipeline(terraform: str, verbose: bool = False, log_callback=None):
        return {
            "cloudformation": "Resources: {}",
            "final_critique": "{}",
            "runbook": "# Migration Runbook",
            "cycles_used": 1,
        }

    monkeypatch.setattr(backend_api, "run_pipeline", fake_run_pipeline)
    client = TestClient(backend_api.app)
    client.post("/api/migrations", json={"terraform": 'resource "x" "y" {}'})
    client.post("/api/migrations", json={"terraform": 'resource "a" "b" {}'})

    list_res = client.get("/api/migrations")
    assert list_res.status_code == 200
    data = list_res.json()
    assert data["count"] >= 2
    assert len(data["jobs"]) >= 2
