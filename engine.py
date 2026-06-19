from bedrock_client import invoke
from prompts import (
    planner_prompt,
    critic_prompt,
    refiner_prompt,
    runbook_prompt,
    deployability_refiner_prompt,
)
from constitution import CONSTITUTION
from db_mapping_context import build_db_mapping_context
import os
import re
from typing import Callable, Optional

MAX_REFINEMENT_CYCLES = 1  # guardrail against infinite loops


def _looks_non_deployable(template_text: str) -> bool:
    patterns = [
        r"your-[a-z0-9-]+",
        r"replace with",
        r"us-central-1[a-z]?",
        r"\bTODO\b",
    ]
    lowered = template_text.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)

def run_pipeline(
    gcp_terraform: str,
    verbose: bool = True,
    log_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    def log(msg):
        if verbose:
            print(msg)
        if log_callback:
            log_callback(msg)

    # ── STEP 1: PLANNER ──────────────────────────────────────────────
    log("\n[0/4] 🗺️  DB Mapper: Querying GCP/AWS graph hints...")
    mapping_context = build_db_mapping_context(gcp_terraform, log=log)

    log("\n[1/4] 🧠 Planner: Generating initial AWS CloudFormation...")
    aws_cf = invoke(planner_prompt(gcp_terraform, mapping_context))
    log(f"      ✅ Draft generated ({len(aws_cf)} chars)")

    # ── STEP 2-3: CRITIC → REFINER LOOP ──────────────────────────────
    for cycle in range(1, MAX_REFINEMENT_CYCLES + 1):
        log(f"\n[2/4] 🔍 Critic (cycle {cycle}/{MAX_REFINEMENT_CYCLES}): Auditing against 18-Rule Constitution...")
        critique = invoke(critic_prompt(aws_cf, CONSTITUTION))
        log(f"      Critique:\n      {critique[:500]}{'...' if len(critique) > 500 else ''}")

        if "NO_VIOLATIONS" in critique:
            log(f"      ✅ No violations found. Skipping refinement.")
            break

        log(f"\n[3/4] 🔧 Refiner (cycle {cycle}): Self-healing violations...")
        aws_cf = invoke(refiner_prompt(aws_cf, critique))
        log(f"      ✅ Refined CloudFormation generated ({len(aws_cf)} chars)")

    # ── STEP 3.5: DEPLOYABILITY HARDENING ───────────────────────────
    region = os.getenv("AWS_REGION", "us-east-1")
    log("\n[3/4] 🚚 Deployability Hardener: Converting template to deployment-ready CloudFormation...")
    aws_cf = invoke(deployability_refiner_prompt(gcp_terraform, aws_cf, region), max_tokens=2600)
    if _looks_non_deployable(aws_cf):
        log("      ⚠️  Placeholder-like values still detected, re-running hardener once...")
        aws_cf = invoke(deployability_refiner_prompt(gcp_terraform, aws_cf, region), max_tokens=2600)
    log(f"      ✅ Deployment-ready hardening complete ({len(aws_cf)} chars)")

    # ── STEP 4: RUNBOOK GENERATION ────────────────────────────────────
    log("\n[4/4] 📋 Generating Migration Runbook...")
    runbook = invoke(runbook_prompt(gcp_terraform, aws_cf))
    log("      ✅ Runbook complete.")

    return {
        "cloudformation": aws_cf,
        "final_critique": critique,
        "runbook": runbook,
        "cycles_used": cycle,
    }