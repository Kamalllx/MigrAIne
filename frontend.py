import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import streamlit as st

from deploy_stack import deploy
from engine import run_pipeline


ROOT = Path(__file__).resolve().parent
SAMPLE_TF = ROOT / "sample_input.tf"
OUTPUT_CF = ROOT / "output_cloudformation.yaml"
OUTPUT_RUNBOOK = ROOT / "output_runbook.md"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"


def load_sample_tf() -> str:
    if SAMPLE_TF.exists():
        return SAMPLE_TF.read_text(encoding="utf-8")
    return ""


def _http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 120) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def start_job(backend_url: str, terraform: str) -> dict:
    return _http_json(
        f"{backend_url}/api/migrations",
        method="POST",
        payload={"terraform": terraform},
        timeout=60,
    )


def get_job(backend_url: str, job_id: str) -> dict:
    return _http_json(f"{backend_url}/api/migrations/{job_id}", method="GET", timeout=60)


def check_backend_health(backend_url: str) -> dict:
    return _http_json(f"{backend_url}/health", method="GET", timeout=10)


def run_pipeline_via_backend(gcp_terraform: str, backend_url: str) -> dict:
    with st.status("Running MigrAI pipeline via backend", expanded=True) as status:
        st.write("Submitting migration job to backend API...")
        job = start_job(backend_url, gcp_terraform)
        job_id = job["id"]
        st.write(f"Job created: {job_id}")

        progress_bar = st.progress(0)
        log_view = st.empty()
        transient_failures = 0
        max_transient_failures = 12

        while True:
            try:
                job = get_job(backend_url, job_id)
                transient_failures = 0
            except HTTPError as exc:
                if exc.code == 404:
                    status.update(label="Pipeline failed", state="error")
                    raise RuntimeError(
                        "Backend no longer has this job (likely restarted while polling). "
                        "Please re-run the migration job."
                    ) from exc
                raise
            except (URLError, TimeoutError, ConnectionResetError):
                transient_failures += 1
                st.warning(
                    f"Backend connection interrupted (attempt {transient_failures}/{max_transient_failures}). "
                    "Retrying..."
                )
                if transient_failures >= max_transient_failures:
                    status.update(label="Pipeline failed", state="error")
                    raise RuntimeError(
                        "Lost connection to backend for too long. "
                        "Ensure uvicorn is running and retry."
                    )
                time.sleep(1.5)
                continue

            progress = int(job.get("progress", 0))
            progress_bar.progress(min(100, max(0, progress)))

            logs = job.get("logs", [])
            if logs:
                log_view.code("\n".join(logs[-20:]), language="text")

            state = job.get("status")
            if state == "completed":
                status.update(label="Pipeline finished", state="complete")
                result = job.get("result", {})
                cloudformation = result.get("cloudformation", "")
                runbook = result.get("runbook", "")
                OUTPUT_CF.write_text(cloudformation, encoding="utf-8")
                OUTPUT_RUNBOOK.write_text(runbook, encoding="utf-8")
                return {
                    "cloudformation": cloudformation,
                    "runbook": runbook,
                    "critique": result.get("final_critique", {}),
                }

            if state == "failed":
                status.update(label="Pipeline failed", state="error")
                raise RuntimeError(job.get("error", "Unknown backend error"))

            time.sleep(1.5)


def run_pipeline_locally(gcp_terraform: str) -> dict:
    logs: list[str] = []
    with st.status("Running MigrAI pipeline locally", expanded=True) as status:
        log_view = st.empty()

        def cb(message: str) -> None:
            logs.append(message)
            log_view.code("\n".join(logs[-20:]), language="text")

        result = run_pipeline(gcp_terraform, verbose=False, log_callback=cb)
        status.update(label="Local pipeline finished", state="complete")

    cloudformation = result.get("cloudformation", "")
    runbook = result.get("runbook", "")
    OUTPUT_CF.write_text(cloudformation, encoding="utf-8")
    OUTPUT_RUNBOOK.write_text(runbook, encoding="utf-8")
    return {
        "cloudformation": cloudformation,
        "runbook": runbook,
        "critique": result.get("final_critique", {}),
    }


def deploy_latest_template() -> dict:
    logs: list[str] = []

    def logger(message: str) -> None:
        logs.append(message)
        log_view.code("\n".join(logs[-40:]), language="text")

    with st.status("Deploying output_cloudformation.yaml to AWS", expanded=True) as status:
        log_view = st.empty()
        result = deploy(log=logger)
        status.update(label="Deployment finished", state="complete")
    return result


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

        :root {
            --bg: #f5f7fb;
            --ink: #0f172a;
            --muted: #475569;
            --card: #ffffff;
            --line: #d7dee9;
            --brand: #0a84ff;
            --brand-2: #11a36a;
        }

        .stApp {
            background: radial-gradient(circle at 90% 10%, #d7ecff 0%, transparent 35%),
                        radial-gradient(circle at 10% 90%, #dcf9e8 0%, transparent 35%),
                        var(--bg);
            color: var(--ink);
            font-family: 'IBM Plex Sans', sans-serif;
        }

        h1, h2, h3 {
            font-family: 'Space Grotesk', sans-serif !important;
            letter-spacing: -0.02em;
            color: var(--ink);
        }

        .hero {
            border: 1px solid var(--line);
            border-radius: 18px;
            background: linear-gradient(145deg, #ffffff 0%, #f8fbff 100%);
            padding: 1.2rem 1.4rem;
            margin-bottom: 1rem;
            box-shadow: 0 10px 30px rgba(10, 18, 34, 0.06);
        }

        .stepcard {
            border: 1px solid var(--line);
            border-radius: 14px;
            background: var(--card);
            padding: 0.85rem 1rem;
            margin: 0.35rem 0;
        }

        .stepnum {
            font-size: 0.8rem;
            color: var(--brand);
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }

        .steptitle {
            font-size: 1rem;
            color: var(--ink);
            font-weight: 600;
            margin-top: 0.1rem;
        }

        .muted {
            color: var(--muted);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="MigrAI Studio", page_icon="M", layout="wide")
    inject_styles()

    st.markdown(
        """
        <div style="background: blue" class="hero">
            <h1>MigrAI Studio</h1>
            <p class="muted">GCP to AWS migration pipeline visualizer: Planner -> Critic -> Refiner -> Runbook.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.6, 1], gap="large")

    with left:
        default_tf = load_sample_tf()
        backend_url = st.text_input("Backend API URL", value=DEFAULT_BACKEND_URL)
        run_mode = st.radio(
            "Execution Mode",
            options=["Backend API", "Local Fallback"],
            horizontal=True,
            help="Use Backend API for job polling; use Local Fallback if backend env is misconfigured.",
        )
        gcp_terraform = st.text_area(
            "GCP Terraform Input",
            value=default_tf,
            height=380,
            placeholder="Paste your GCP Terraform here...",
        )

        c1, c2 = st.columns([1, 1])
        with c1:
            st.caption("Backend mode requires backend process to have valid OPENAI_API_KEY in its environment.")
        with c2:
            run_clicked = st.button("Run Migration Pipeline", type="primary", use_container_width=True)

        deploy_clicked = st.button("Deploy Latest Template", use_container_width=True)

    with right:
        st.markdown("### Pipeline Flow")
        steps = [
            ("Step 1", "Planner", "Draft AWS CloudFormation from GCP Terraform"),
            ("Step 2", "Critic", "Audit draft against 18-rule security constitution"),
            ("Step 3", "Refiner", "Patch violations and harden infrastructure"),
            ("Step 4", "Runbook", "Generate migration checklist and execution plan"),
        ]
        for label, title, desc in steps:
            st.markdown(
                f"""
                <div class="stepcard">
                    <div class="stepnum">{label}</div>
                    <div class="steptitle">{title}</div>
                    <div class="muted">{desc}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    if run_clicked:
        if not gcp_terraform.strip():
            st.error("Please provide Terraform input before running the pipeline.")
            st.stop()

        try:
            if run_mode == "Backend API":
                check_backend_health(backend_url.rstrip("/"))
                result = run_pipeline_via_backend(gcp_terraform, backend_url.rstrip("/"))
            else:
                result = run_pipeline_locally(gcp_terraform)
        except (HTTPError, URLError, TimeoutError) as exc:
            st.error("Could not reach backend API. Start backend with: uvicorn backend_api:app --reload")
            st.exception(exc)
            st.stop()
        except Exception as exc:  # noqa: BLE001
            if "OPENAI_API_KEY is required in .env" in str(exc):
                st.error(
                    "Backend does not see OPENAI_API_KEY. Restart backend from project folder so it loads MigrAI/.env, "
                    "or use Local Fallback mode in this UI."
                )
            st.exception(exc)
            st.stop()

        st.success("Pipeline completed. Outputs were written to output_cloudformation.yaml and output_runbook.md")

        tab1, tab2, tab3 = st.tabs(["CloudFormation Output", "Migration Runbook", "Critic Summary"])
        with tab1:
            st.code(result["cloudformation"], language="yaml")
            st.download_button(
                "Download CloudFormation",
                data=result["cloudformation"],
                file_name="output_cloudformation.yaml",
                mime="text/yaml",
            )
        with tab2:
            st.markdown(result["runbook"])
            st.download_button(
                "Download Runbook",
                data=result["runbook"],
                file_name="output_runbook.md",
                mime="text/markdown",
            )
        with tab3:
            st.json(result["critique"])

    if deploy_clicked:
        if not OUTPUT_CF.exists():
            st.error("No output_cloudformation.yaml found. Run the migration pipeline first.")
            st.stop()

        try:
            deploy_result = deploy_latest_template()
        except Exception as exc:  # noqa: BLE001
            st.error("Deployment failed. Check logs above for details.")
            st.exception(exc)
            st.stop()

        st.success(
            f"Deployment {deploy_result.get('status')} via {deploy_result.get('action')} for "
            f"stack {deploy_result.get('stack_name')} in {deploy_result.get('region')}."
        )


if __name__ == "__main__":
    main()
