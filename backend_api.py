import json
import os
import threading
import uuid
import re
from time import sleep
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from deploy_stack import deploy
from diagram_generator import DiagramGenerator
from engine import run_pipeline


class MigrateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: Optional[Any] = Field(default=None, description="Raw source infrastructure input (Terraform, JSON, or a structured migration bundle)")
    terraform: Optional[Any] = Field(default=None, description="Legacy field: raw GCP Terraform input")


class JobState(BaseModel):
    id: str
    status: str
    progress: int
    created_at: str
    updated_at: str
    logs: List[str]
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DeployRequest(BaseModel):
    cloudformation: Optional[str] = Field(
        default=None,
        description="CloudFormation YAML content. If provided, backend writes it to output_cloudformation.yaml before deploy.",
    )


class DeployResponse(BaseModel):
    status: str
    logs: List[str]
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DiagramRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_json: Any = Field(description="Migration input JSON content")
    cloudformation_yaml: Any = Field(description="CloudFormation YAML template")


class DiagramResponse(BaseModel):
    gcp_diagram: str
    aws_diagram: str
    mapping_diagram: str
    summary: Dict[str, int]


class SSMRunRequest(BaseModel):
    instance_id: str = Field(description="EC2 instance ID, e.g. i-0123456789abcdef0")
    commands: List[str] = Field(description="Shell commands to run on the instance")
    region: Optional[str] = Field(default=None, description="AWS region. Defaults to AWS_REGION or us-east-1.")
    comment: Optional[str] = Field(default="MigrAI SSM execution", description="RunCommand comment")
    poll_seconds: int = Field(default=2, ge=1, le=10, description="Polling interval in seconds")
    timeout_seconds: int = Field(default=90, ge=10, le=600, description="Max wait time for command completion")


class SSMRunResponse(BaseModel):
    status: str
    command_id: Optional[str] = None
    instance_id: str
    region: str
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    error: Optional[str] = None


class InstanceLookupResponse(BaseModel):
    stack_name: str
    region: str
    instance_ids: List[str]


class RepoRunRequest(BaseModel):
    github_url: str = Field(description="Public GitHub repository URL")
    branch: str = Field(default="main", description="Git branch to deploy")
    setup_command: Optional[str] = Field(default=None, description="Optional setup command, e.g. 'npm install'")
    start_command: str = Field(description="Command used to launch app/service")
    stack_name: Optional[str] = Field(default=None, description="CloudFormation stack name override")
    region: Optional[str] = Field(default=None, description="AWS region override")
    app_name: Optional[str] = Field(default=None, description="Optional app folder name under ~/migrai_apps")
    poll_seconds: int = Field(default=2, ge=1, le=10, description="Polling interval in seconds")
    timeout_seconds: int = Field(default=180, ge=10, le=1800, description="Max wait time for command completion")


class RepoRunResponse(BaseModel):
    status: str
    instance_id: str
    region: str
    stack_name: str
    app_dir: str
    command_id: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    error: Optional[str] = None


app = FastAPI(title="MigrAI Backend API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_jobs_file = Path(__file__).resolve().parent / ".jobs_state.json"
_output_cf = Path(__file__).resolve().parent / "output_cloudformation.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _progress_from_log(message: str, current: int) -> int:
    if "[0/4]" in message:
        return max(current, 5)
    if "[1/4]" in message:
        return max(current, 25)
    if "[2/4]" in message:
        return max(current, 50)
    if "[3/4]" in message:
        return max(current, 75)
    if "[4/4]" in message:
        return max(current, 90)
    return current


def _stringify_input(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2)
    return str(value).strip()


def _normalize_migration_source(payload: MigrateRequest) -> str:
    source = _stringify_input(payload.source)
    if source:
        return source

    terraform = _stringify_input(payload.terraform)
    if terraform:
        return terraform

    # If the caller sends a structured bundle directly, preserve it as JSON text so
    # the existing planner/input adapter can normalize it downstream.
    return json.dumps(payload.model_dump(exclude_none=True), indent=2)


def _sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "app"


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _derive_repo_name(github_url: str) -> str:
    trimmed = github_url.rstrip("/")
    name = trimmed.split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return _sanitize_name(name or "repo")


def _resolve_stack_and_region(
    stack_name: Optional[str],
    region: Optional[str],
    *,
    require_explicit: bool = False,
) -> tuple[str, str]:
    load_dotenv()
    provided_region = (region or "").strip()
    provided_stack = (stack_name or "").strip()

    if require_explicit and (not provided_region or not provided_stack):
        raise HTTPException(
            status_code=422,
            detail="stack_name and region are required for /api/ec2/run-repo to avoid targeting the wrong instance",
        )

    resolved_region = provided_region or os.getenv("AWS_REGION", "us-east-1")
    resolved_stack = provided_stack or os.getenv("CFN_STACK_NAME", "migrai-demo-stack")
    return resolved_stack, resolved_region


def _discover_stack_instance_ids(stack_name: str, region: str) -> List[str]:
    cfn = boto3.client("cloudformation", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)

    instance_ids: List[str] = []

    try:
        paginator = cfn.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for item in page.get("StackResourceSummaries", []):
                if item.get("ResourceType") == "AWS::EC2::Instance":
                    physical_id = (item.get("PhysicalResourceId") or "").strip()
                    if physical_id:
                        instance_ids.append(physical_id)
    except Exception:
        pass

    if not instance_ids:
        try:
            pages = ec2.get_paginator("describe_instances").paginate(
                Filters=[
                    {"Name": "tag:aws:cloudformation:stack-name", "Values": [stack_name]},
                    {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
                ]
            )
            for page in pages:
                for reservation in page.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        instance_id = (instance.get("InstanceId") or "").strip()
                        if instance_id:
                            instance_ids.append(instance_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"Unable to discover instances: {str(exc)}")

    return sorted(set(instance_ids))


def _instance_launch_times(instance_ids: List[str], region: str) -> Dict[str, datetime]:
    if not instance_ids:
        return {}

    ec2 = boto3.client("ec2", region_name=region)
    launch_times: Dict[str, datetime] = {}
    try:
        response = ec2.describe_instances(InstanceIds=instance_ids)
    except Exception:
        return launch_times

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instance_id = (instance.get("InstanceId") or "").strip()
            launch_time = instance.get("LaunchTime")
            state_name = ((instance.get("State") or {}).get("Name") or "").lower()
            if instance_id and launch_time and state_name == "running":
                launch_times[instance_id] = launch_time

    return launch_times


def _ssm_online_instance_ids(instance_ids: List[str], region: str) -> List[str]:
    if not instance_ids:
        return []

    ssm = boto3.client("ssm", region_name=region)
    online: List[str] = []
    try:
        paginator = ssm.get_paginator("describe_instance_information")
        for page in paginator.paginate(
            Filters=[
                {"Key": "InstanceIds", "Values": instance_ids},
                {"Key": "PingStatus", "Values": ["Online"]},
            ]
        ):
            for item in page.get("InstanceInformationList", []):
                iid = (item.get("InstanceId") or "").strip()
                if iid:
                    online.append(iid)
    except Exception:
        return []

    return sorted(set(online))


def _pick_runnable_instance_id(stack_name: str, region: str) -> Tuple[str, List[str]]:
    discovered = _discover_stack_instance_ids(stack_name, region)
    if not discovered:
        raise HTTPException(status_code=404, detail=f"No EC2 instances found for stack '{stack_name}' in region '{region}'")

    running_by_launch = _instance_launch_times(discovered, region)
    running_ids = sorted(running_by_launch.keys())
    online_ids = _ssm_online_instance_ids(discovered, region)

    runnable = [iid for iid in discovered if iid in running_ids and iid in online_ids]
    if not runnable:
        raise HTTPException(
            status_code=422,
            detail=(
                "No runnable EC2 target found (requires EC2 state=running and SSM PingStatus=Online). "
                f"Discovered={discovered}, Running={running_ids}, SSMOnline={online_ids}"
            ),
        )

    runnable_sorted = sorted(runnable, key=lambda iid: running_by_launch[iid], reverse=True)
    return runnable_sorted[0], discovered


def _save_jobs_locked() -> None:
    """Write jobs to disk while lock is held."""
    _jobs_file.write_text(json.dumps(_jobs, indent=2), encoding="utf-8")


def _load_jobs() -> None:
    if not _jobs_file.exists():
        return

    try:
        data = json.loads(_jobs_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    if not isinstance(data, dict):
        return

    with _jobs_lock:
        _jobs.update(data)
        # Any in-flight jobs are lost on restart; mark them failed clearly.
        for job in _jobs.values():
            if job.get("status") in {"queued", "running"}:
                job["status"] = "failed"
                job["error"] = "Backend restarted while job was running. Please rerun."
                logs = job.get("logs", [])
                logs.append("Backend restart detected. Previous in-flight job was terminated.")
                job["logs"] = logs
                job["updated_at"] = _now_iso()
        _save_jobs_locked()


def _update_job(job_id: str, **fields: Any) -> None:
    with _jobs_lock:
        if job_id not in _jobs:
            return
        _jobs[job_id].update(fields)
        _jobs[job_id]["updated_at"] = _now_iso()
        _save_jobs_locked()


def _append_log(job_id: str, message: str) -> None:
    with _jobs_lock:
        if job_id not in _jobs:
            return
        _jobs[job_id]["logs"].append(message)
        _jobs[job_id]["progress"] = _progress_from_log(message, _jobs[job_id]["progress"])
        _jobs[job_id]["updated_at"] = _now_iso()
        _save_jobs_locked()


def _run_job(job_id: str, source_input: str) -> None:
    try:
        _update_job(job_id, status="running", progress=1)

        def log_callback(message: str) -> None:
            _append_log(job_id, message)

        result = run_pipeline(source_input, verbose=False, log_callback=log_callback)
        _update_job(job_id, status="completed", progress=100, result=result)
    except Exception as exc:  # noqa: BLE001
        _update_job(job_id, status="failed", error=str(exc))


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/migrations", response_model=JobState)
def start_migration(payload: MigrateRequest) -> JobState:
    source_input = _normalize_migration_source(payload)
    if not source_input:
        raise HTTPException(status_code=422, detail="Provide either 'source' or 'terraform' in request body")

    job_id = str(uuid.uuid4())
    now = _now_iso()
    job: Dict[str, Any] = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "created_at": now,
        "updated_at": now,
        "logs": ["Job queued"],
        "result": None,
        "error": None,
    }

    with _jobs_lock:
        _jobs[job_id] = job
        _save_jobs_locked()

    thread = threading.Thread(target=_run_job, args=(job_id, source_input), daemon=True)
    thread.start()

    return JobState(**job)


@app.get("/api/migrations/{job_id}", response_model=JobState)
def get_migration(job_id: str) -> JobState:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobState(**job)


@app.get("/api/migrations")
def list_migrations() -> Dict[str, Any]:
    with _jobs_lock:
        items = [
            {
                "id": j["id"],
                "status": j["status"],
                "progress": j["progress"],
                "updated_at": j["updated_at"],
            }
            for j in _jobs.values()
        ]
    return {"count": len(items), "jobs": items}


@app.post("/api/deploy", response_model=DeployResponse)
def deploy_latest_template(payload: DeployRequest) -> DeployResponse:
    template = (payload.cloudformation or "").strip()
    if template:
        _output_cf.write_text(template, encoding="utf-8")

    if not _output_cf.exists():
        raise HTTPException(status_code=422, detail="No output_cloudformation.yaml found. Run migration first.")

    logs: List[str] = []

    def logger(message: str) -> None:
        logs.append(message)

    try:
        result = deploy(log=logger)
        return DeployResponse(status="completed", logs=logs, result=result)
    except Exception as exc:  # noqa: BLE001
        return DeployResponse(status="failed", logs=logs, error=str(exc))


@app.post("/api/diagrams", response_model=DiagramResponse)
def generate_diagrams(payload: DiagramRequest) -> DiagramResponse:
    """Generate Mermaid diagrams from source JSON and CloudFormation YAML."""
    try:
        generator = DiagramGenerator()
        source_json = _stringify_input(payload.source_json)
        cloudformation_yaml = _stringify_input(payload.cloudformation_yaml)
        diagrams = generator.generate_all_diagrams(
            source_json,
            cloudformation_yaml,
        )
        return DiagramResponse(**diagrams)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Failed to generate diagrams: {str(exc)}")


@app.post("/api/ssm/run", response_model=SSMRunResponse)
def run_ssm_command(payload: SSMRunRequest) -> SSMRunResponse:
    load_dotenv()

    instance_id = payload.instance_id.strip()
    commands = [cmd for cmd in payload.commands if cmd.strip()]
    if not instance_id:
        raise HTTPException(status_code=422, detail="instance_id is required")
    if not commands:
        raise HTTPException(status_code=422, detail="commands must contain at least one non-empty command")

    region = (payload.region or "").strip() or os.getenv("AWS_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)

    try:
        send = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
            Comment=payload.comment or "MigrAI SSM execution",
        )
        command_id = send["Command"]["CommandId"]
    except Exception as exc:  # noqa: BLE001
        return SSMRunResponse(
            status="failed",
            instance_id=instance_id,
            region=region,
            error=str(exc),
        )

    terminal_states = {"Success", "Cancelled", "TimedOut", "Failed"}
    attempts = max(1, payload.timeout_seconds // payload.poll_seconds)
    last_error: Optional[str] = None

    for _ in range(attempts):
        sleep(payload.poll_seconds)
        try:
            inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except Exception as exc:  # noqa: BLE001
            # Invocation may briefly be unavailable right after send_command.
            last_error = str(exc)
            continue

        status = inv.get("Status", "Unknown")
        if status in terminal_states:
            return SSMRunResponse(
                status=status.lower(),
                command_id=command_id,
                instance_id=instance_id,
                region=region,
                stdout=inv.get("StandardOutputContent"),
                stderr=inv.get("StandardErrorContent"),
                error=None if status == "Success" else inv.get("StatusDetails", "SSM command failed"),
            )

    return SSMRunResponse(
        status="timeout",
        command_id=command_id,
        instance_id=instance_id,
        region=region,
        error=last_error or "Timed out waiting for command completion",
    )


@app.get("/api/deploy/instances", response_model=InstanceLookupResponse)
def list_stack_instances(stack_name: Optional[str] = None, region: Optional[str] = None) -> InstanceLookupResponse:
    """Return EC2 instance IDs associated with a deployed CloudFormation stack."""
    resolved_stack, resolved_region = _resolve_stack_and_region(stack_name, region)
    deduped = _discover_stack_instance_ids(resolved_stack, resolved_region)
    return InstanceLookupResponse(
        stack_name=resolved_stack,
        region=resolved_region,
        instance_ids=deduped,
    )


@app.post("/api/ec2/run-repo", response_model=RepoRunResponse)
def run_github_repo_on_stack_instance(payload: RepoRunRequest) -> RepoRunResponse:
    github_url = (payload.github_url or "").strip()
    if not github_url.startswith("https://github.com/"):
        raise HTTPException(status_code=422, detail="github_url must start with https://github.com/")

    start_command = (payload.start_command or "").strip()
    if not start_command:
        raise HTTPException(status_code=422, detail="start_command is required")

    stack_name, region = _resolve_stack_and_region(
        payload.stack_name,
        payload.region,
        require_explicit=True,
    )
    instance_id, _ = _pick_runnable_instance_id(stack_name, region)
    app_name = _sanitize_name(payload.app_name or _derive_repo_name(github_url))
    app_dir = f"/opt/migrai_apps/{app_name}"
    branch = (payload.branch or "main").strip() or "main"
    setup_command = (payload.setup_command or "").strip()

    quoted_url = _shell_single_quote(github_url)
    quoted_branch = _shell_single_quote(branch)
    quoted_app_dir = _shell_single_quote(app_dir)

    script_lines = [
        "set -e",
        "sudo mkdir -p /opt/migrai_apps",
        "sudo chown -R $(whoami):$(whoami) /opt/migrai_apps",
        "if ! command -v git >/dev/null 2>&1; then",
        "  if command -v dnf >/dev/null 2>&1; then sudo dnf -y install git;",
        "  elif command -v yum >/dev/null 2>&1; then sudo yum -y install git;",
        "  elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get -y install git;",
        "  else echo 'No supported package manager found to install git' && exit 1;",
        "  fi",
        "fi",
        f"REPO_URL={quoted_url}",
        f"BRANCH={quoted_branch}",
        f"APP_DIR={quoted_app_dir}",
        "if [ -d \"$APP_DIR/.git\" ]; then",
        "  git -C \"$APP_DIR\" fetch --all",
        "  git -C \"$APP_DIR\" checkout \"$BRANCH\"",
        "  git -C \"$APP_DIR\" reset --hard \"origin/$BRANCH\"",
        "else",
        "  git clone --branch \"$BRANCH\" \"$REPO_URL\" \"$APP_DIR\"",
        "fi",
        "cd \"$APP_DIR\"",
    ]

    if setup_command:
        script_lines.append(setup_command)

    script_lines.extend([
        "if command -v pgrep >/dev/null 2>&1; then",
        "  pgrep -f \"$APP_DIR\" | xargs -r kill -9 || true",
        "fi",
        f"nohup bash -lc {_shell_single_quote(start_command)} > app.out.log 2> app.err.log < /dev/null &",
        "sleep 1",
        "echo RUN_REPO_OK",
        "echo APP_DIR=$APP_DIR",
    ])

    ssm_result = run_ssm_command(
        SSMRunRequest(
            instance_id=instance_id,
            commands=["\n".join(script_lines)],
            region=region,
            comment=f"MigrAI GitHub runner: {app_name}",
            poll_seconds=payload.poll_seconds,
            timeout_seconds=payload.timeout_seconds,
        )
    )

    return RepoRunResponse(
        status=ssm_result.status,
        instance_id=instance_id,
        region=region,
        stack_name=stack_name,
        app_dir=f"/opt/migrai_apps/{app_name}",
        command_id=ssm_result.command_id,
        stdout=ssm_result.stdout,
        stderr=ssm_result.stderr,
        error=ssm_result.error,
    )


_load_jobs()
