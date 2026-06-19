def planner_prompt(gcp_terraform: str, mapping_context: str = "") -> str:
    mapping_block = f"\n{mapping_context}\n" if mapping_context else ""
    return f"""
You are an expert cloud architect specializing in GCP-to-AWS migrations.

Convert the following GCP Terraform configuration into equivalent AWS CloudFormation (YAML).
Produce ONLY the CloudFormation YAML. No explanations, no markdown fences.
{mapping_block}
If DB-BACKED GCP->AWS MAPPING HINTS are provided, prioritize them over free-form assumptions.

GCP TERRAFORM INPUT:
{gcp_terraform}
""".strip()


def critic_prompt(aws_cloudformation: str, constitution: str) -> str:
    return f"""
{constitution}

Review the following AWS CloudFormation template against the 18 rules above.

You MUST respond with a single valid JSON object and nothing else. No markdown, no explanation.
Schema:
{{
  "violations": [
    {{
      "rule": <int>,        
      "resource": "<resource logical name>",
      "issue": "<one sentence description>",
      "severity": "<HIGH|MEDIUM|LOW>"
    }}
  ],
  "passed_rules": [<list of rule numbers with no violations>],
  "summary": "<one sentence overall assessment>"
}}

If there are no violations, return an empty list for "violations".

CLOUDFORMATION TO REVIEW:
{aws_cloudformation}
""".strip()

def refiner_prompt(aws_cloudformation: str, critique: str) -> str:
    return f"""
You are an expert AWS CloudFormation engineer.

Below is a CloudFormation template with identified security violations.
Fix ALL violations listed in the critique. Output ONLY the corrected CloudFormation YAML.
Do not explain your changes. Do not use markdown fences.

ORIGINAL CLOUDFORMATION:
{aws_cloudformation}

CRITIQUE / VIOLATIONS TO FIX:
{critique}
""".strip()


def runbook_prompt(gcp_terraform: str, final_cloudformation: str) -> str:
    return f"""
You are a senior cloud migration engineer. 
Given the original GCP infrastructure and the final AWS CloudFormation, 
produce a concise step-by-step migration runbook in Markdown.

Structure it as:
# Migration Runbook
## Pre-Migration Checklist
## Migration Steps (numbered, ordered by dependency)
## Rollback Plan
## Post-Migration Validation

GCP SOURCE:
{gcp_terraform}

TARGET AWS CLOUDFORMATION:
{final_cloudformation}
""".strip()


def deployability_refiner_prompt(gcp_terraform: str, aws_cloudformation: str, aws_region: str) -> str:
  return f"""
You are an AWS CloudFormation deployment hardening expert.

Rewrite the template below into a deployment-ready CloudFormation YAML that can be executed directly.

Strict requirements:
1. Output ONLY valid CloudFormation YAML (no markdown, no explanation).
2. Preserve the same infrastructure intent and logical resources as much as possible.
3. Eliminate placeholders like "your-...", "replace with ...", fake CIDRs, and invalid regions/AZs.
4. Add a robust Parameters section for environment-specific values (VPC/Subnet/KeyPair/DB credentials/CIDR/etc).
5. Never hardcode secrets; use NoEcho parameters for passwords.
6. Use valid resource property types and CloudFormation intrinsic functions.
7. Ensure EC2 AMI is deployable by default using SSM public parameter type/value where practical.
8. Include AWSTemplateFormatVersion and Description.
9. Keep template deployable in region: {aws_region}.
10. Keep security defaults sane (private defaults, encrypted storage where applicable).

GCP SOURCE CONTEXT:
{gcp_terraform}

TEMPLATE TO HARDEN:
{aws_cloudformation}
""".strip()