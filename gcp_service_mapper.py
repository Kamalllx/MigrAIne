import re
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ServiceMapping:
    gcp_resource: str
    aws_resource: str
    aws_secondary: list[str]   # supporting resources always needed alongside
    notes: str
    iam_required: bool = False

# ── GROUND TRUTH MAPPING TABLE ─────────────────────────────────────────────────
# This is your defensible answer to "how does it know what maps to what?"
MAPPING_TABLE: list[ServiceMapping] = [
    # COMPUTE
    ServiceMapping("google_compute_instance",       "AWS::EC2::Instance",               ["AWS::EC2::SecurityGroup", "AWS::IAM::Role", "AWS::IAM::InstanceProfile"],  "GCP VM → EC2. Must include IAM instance profile and SG.",             iam_required=True),
    ServiceMapping("google_compute_instance_group", "AWS::AutoScaling::AutoScalingGroup",["AWS::ElasticLoadBalancingV2::LoadBalancer"],                               "Managed instance group → ASG + ALB.",                                 iam_required=True),
    ServiceMapping("google_compute_disk",           "AWS::EC2::Volume",                 [],                                                                          "Persistent disk → EBS volume. Ensure encryption enabled."),
    ServiceMapping("google_compute_snapshot",       "AWS::EC2::Snapshot",               [],                                                                          "GCP snapshot → EBS snapshot."),

    # STORAGE
    ServiceMapping("google_storage_bucket",         "AWS::S3::Bucket",                  ["AWS::S3::BucketPolicy"],                                                   "GCS bucket → S3. Must add public access block + encryption + versioning."),

    # DATABASE
    ServiceMapping("google_sql_database_instance",  "AWS::RDS::DBInstance",             ["AWS::RDS::DBSubnetGroup", "AWS::EC2::SecurityGroup"],                      "Cloud SQL → RDS. Must enable MultiAZ + encryption.",                  iam_required=True),
    ServiceMapping("google_sql_database",           "AWS::RDS::DBInstance",             [],                                                                          "Logical DB inside Cloud SQL → same RDS instance, separate schema."),
    ServiceMapping("google_spanner_instance",       "AWS::DynamoDB::Table",             [],                                                                          "Spanner → DynamoDB (closest managed, globally distributed option)."),
    ServiceMapping("google_bigtable_instance",      "AWS::DynamoDB::Table",             [],                                                                          "Bigtable → DynamoDB. Wide-column workloads map to DynamoDB GSIs."),
    ServiceMapping("google_firestore_document",     "AWS::DynamoDB::Table",             [],                                                                          "Firestore → DynamoDB. Adjust partition key design."),

    # NETWORKING
    ServiceMapping("google_compute_network",        "AWS::EC2::VPC",                    ["AWS::EC2::InternetGateway", "AWS::EC2::VPCGatewayAttachment"],              "VPC → VPC. Add flow logs per Rule #9."),
    ServiceMapping("google_compute_subnetwork",     "AWS::EC2::Subnet",                 ["AWS::EC2::RouteTable", "AWS::EC2::SubnetRouteTableAssociation"],            "Subnet → Subnet. Attach route table."),
    ServiceMapping("google_compute_firewall",       "AWS::EC2::SecurityGroup",          [],                                                                          "Firewall rules → SG ingress/egress rules. No 0.0.0.0/0 on 22/3389."),
    ServiceMapping("google_compute_global_address", "AWS::EC2::EIP",                    [],                                                                          "Global IP → Elastic IP."),
    ServiceMapping("google_compute_forwarding_rule","AWS::ElasticLoadBalancingV2::LoadBalancer",["AWS::ElasticLoadBalancingV2::Listener", "AWS::ElasticLoadBalancingV2::TargetGroup"], "Forwarding rule → ALB + Listener + Target Group."),
    ServiceMapping("google_dns_managed_zone",       "AWS::Route53::HostedZone",         ["AWS::Route53::RecordSet"],                                                  "Cloud DNS → Route53 hosted zone."),

    # SERVERLESS & EVENTS
    ServiceMapping("google_cloud_function",         "AWS::Lambda::Function",            ["AWS::IAM::Role", "AWS::Lambda::Permission"],                               "Cloud Function → Lambda. Add DLQ per Rule #16.",                      iam_required=True),
    ServiceMapping("google_cloudfunctions2_function","AWS::Lambda::Function",           ["AWS::IAM::Role", "AWS::Lambda::Permission"],                               "Cloud Functions 2nd gen → Lambda.",                                   iam_required=True),
    ServiceMapping("google_pubsub_topic",           "AWS::SNS::Topic",                  [],                                                                          "Pub/Sub topic → SNS topic."),
    ServiceMapping("google_pubsub_subscription",    "AWS::SQS::Queue",                  ["AWS::SNS::Subscription"],                                                  "Pub/Sub subscription → SQS queue + SNS subscription. Enable KMS."),
    ServiceMapping("google_cloud_scheduler_job",    "AWS::Scheduler::Schedule",         ["AWS::IAM::Role"],                                                          "Cloud Scheduler → EventBridge Scheduler.",                            iam_required=True),
    ServiceMapping("google_eventarc_trigger",       "AWS::Events::Rule",                ["AWS::IAM::Role"],                                                          "Eventarc → EventBridge rule.",                                        iam_required=True),

    # CONTAINERS & ORCHESTRATION
    ServiceMapping("google_container_cluster",      "AWS::EKS::Cluster",                ["AWS::IAM::Role", "AWS::EKS::Nodegroup", "AWS::EC2::SecurityGroup"],        "GKE → EKS. Node IAM role required.",                                  iam_required=True),
    ServiceMapping("google_container_node_pool",    "AWS::EKS::Nodegroup",              ["AWS::IAM::Role"],                                                          "Node pool → EKS managed node group.",                                 iam_required=True),
    ServiceMapping("google_cloud_run_service",      "AWS::ECS::Service",                ["AWS::ECS::Cluster", "AWS::ECS::TaskDefinition", "AWS::IAM::Role"],         "Cloud Run → ECS Fargate. Task execution role required.",              iam_required=True),

    # IAM & SECRETS
    ServiceMapping("google_service_account",        "AWS::IAM::Role",                   [],                                                                          "Service account → IAM Role. Constrain trust policy — no sts:AssumeRole for *.", iam_required=True),
    ServiceMapping("google_secret_manager_secret",  "AWS::SecretsManager::Secret",      [],                                                                          "Secret Manager → Secrets Manager. Never use env vars (Rule #13)."),

    # MONITORING & LOGGING
    ServiceMapping("google_logging_sink",           "AWS::Logs::LogGroup",              ["AWS::Logs::SubscriptionFilter"],                                           "Log sink → CloudWatch Log Group + subscription filter."),
    ServiceMapping("google_monitoring_alert_policy","AWS::CloudWatch::Alarm",           [],                                                                          "Alert policy → CloudWatch Alarm. Required per Rule #17."),

    # ARTIFACT & BUILD
    ServiceMapping("google_artifact_registry_repository","AWS::ECR::Repository",        [],                                                                          "Artifact Registry → ECR. Enable image scanning."),
    ServiceMapping("google_cloudbuild_trigger",     "AWS::CodePipeline::Pipeline",      ["AWS::CodeBuild::Project", "AWS::IAM::Role"],                              "Cloud Build → CodePipeline + CodeBuild.",                             iam_required=True),
]

# Fast lookup dict: gcp_resource_type → ServiceMapping
_LOOKUP: dict[str, ServiceMapping] = {m.gcp_resource: m for m in MAPPING_TABLE}


def extract_gcp_resources(tf_content: str) -> list[str]:
    """
    Parse GCP Terraform to extract resource types.
    Returns list of (resource_type, resource_name) tuples.
    Example: [('google_compute_instance', 'web_server'), ...]
    """
    pattern = r'resource\s+"(google_[a-z_]+)"\s+"([^"]+)"'
    return re.findall(pattern, tf_content)


def map_resources(tf_content: str) -> dict:
    """
    Main entry point. Takes raw GCP Terraform string.
    Returns a structured dict ready for:
      - injecting into the Planner prompt
      - rendering as a table in the UI
    """
    found = extract_gcp_resources(tf_content)
    
    mapped, unmapped = [], []

    for gcp_type, resource_name in found:
        if gcp_type in _LOOKUP:
            m = _LOOKUP[gcp_type]
            mapped.append({
                "gcp_resource_type":  gcp_type,
                "resource_name":      resource_name,
                "aws_primary":        m.aws_resource,
                "aws_secondary":      m.aws_secondary,
                "iam_required":       m.iam_required,
                "notes":              m.notes,
            })
        else:
            unmapped.append({"gcp_resource_type": gcp_type, "resource_name": resource_name})

    return {
        "mapped":   mapped,
        "unmapped": unmapped,
        "stats": {
            "total":    len(found),
            "mapped":   len(mapped),
            "unmapped": len(unmapped),
            "iam_required_count": sum(1 for r in mapped if r["iam_required"]),
        }
    }


def build_mapping_context(mapping_result: dict) -> str:
    """
    Converts the mapping dict into a plain-text block
    injected directly into the Planner prompt.
    This is what makes the LLM output reliable.
    """
    lines = ["VERIFIED GCP→AWS SERVICE MAPPINGS (use these exactly — do not substitute):"]
    for r in mapping_result["mapped"]:
        secondary = ", ".join(r["aws_secondary"]) if r["aws_secondary"] else "none"
        iam_note  = " [IAM ROLE REQUIRED]" if r["iam_required"] else ""
        lines.append(
            f"  - {r['gcp_resource_type']} ({r['resource_name']}) "
            f"→ {r['aws_primary']}{iam_note}\n"
            f"    Supporting resources: {secondary}\n"
            f"    Note: {r['notes']}"
        )
    if mapping_result["unmapped"]:
        lines.append("\nUNKNOWN RESOURCES (use your best judgment for these):")
        for r in mapping_result["unmapped"]:
            lines.append(f"  - {r['gcp_resource_type']} ({r['resource_name']})")
    return "\n".join(lines)