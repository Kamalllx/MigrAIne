import os
import secrets
import re
from pathlib import Path
from typing import Callable, Optional

import boto3
from botocore.exceptions import ClientError
from botocore.exceptions import WaiterError
from dotenv import load_dotenv


STACK_NAME = os.getenv("CFN_STACK_NAME", "migrai-demo-stack")
TEMPLATE_PATH = Path(__file__).resolve().parent / "output_cloudformation.yaml"


def _discover_network_defaults(ec2_client) -> tuple[str, str, list[str]]:
    vpcs = ec2_client.describe_vpcs().get("Vpcs", [])
    if not vpcs:
        raise ValueError("No VPC found in this account/region.")

    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2_client.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
    if not subnets:
        raise ValueError(f"No subnets found in VPC {vpc_id}.")

    subnet_ids = [s["SubnetId"] for s in subnets]
    return vpc_id, subnet_ids[0], subnet_ids


def _ensure_key_pair(ec2_client) -> str:
    explicit = os.getenv("CFN_PARAM_KEY_PAIR_NAME", "").strip()
    if explicit:
        return explicit

    keys = ec2_client.describe_key_pairs().get("KeyPairs", [])
    if keys:
        return keys[0]["KeyName"]

    key_name = f"migrai-auto-key-{secrets.token_hex(4)}"
    key_data = ec2_client.create_key_pair(KeyName=key_name)
    pem_path = Path(__file__).resolve().parent / f"{key_name}.pem"
    pem_path.write_text(key_data["KeyMaterial"], encoding="utf-8")
    return key_name


def _ensure_db_subnet_group(rds_client, subnet_ids: list[str]) -> str:
    explicit = os.getenv("CFN_PARAM_DB_SUBNET_GROUP_NAME", "").strip()
    if explicit:
        return explicit

    existing = rds_client.describe_db_subnet_groups().get("DBSubnetGroups", [])
    if existing:
        return existing[0]["DBSubnetGroupName"]

    name = "migrai-auto-db-subnet-group"
    try:
        rds_client.create_db_subnet_group(
            DBSubnetGroupName=name,
            DBSubnetGroupDescription="MigrAI auto-created DB subnet group",
            SubnetIds=subnet_ids[:2],
        )
    except ClientError as exc:
        if "DBSubnetGroupAlreadyExists" not in str(exc):
            raise
    return name


def _ensure_security_group(ec2_client, vpc_id: str, cidr: str) -> str:
    explicit = os.getenv("CFN_PARAM_VPC_SECURITY_GROUP", "").strip()
    if explicit:
        return explicit

    groups = ec2_client.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get(
        "SecurityGroups", []
    )
    if groups:
        return groups[0]["GroupId"]

    name = f"migrai-auto-sg-{secrets.token_hex(4)}"
    result = ec2_client.create_security_group(
        GroupName=name,
        Description="MigrAI auto-created security group",
        VpcId=vpc_id,
    )
    sg_id = result["GroupId"]
    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "IpRanges": [{"CidrIp": cidr}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "IpRanges": [{"CidrIp": cidr}],
                },
            ],
        )
    except ClientError:
        pass
    return sg_id


def _template_has_parameter(template_body: str, param_name: str) -> bool:
    lines = template_body.splitlines()
    in_parameters = False

    for line in lines:
        stripped = line.strip()

        # Enter Parameters section.
        if line.startswith("Parameters:"):
            in_parameters = True
            continue

        # Exit Parameters when next top-level section starts.
        if in_parameters and line and not line.startswith(" "):
            in_parameters = False

        if not in_parameters:
            continue

        if re.match(rf"^\s{{2}}{re.escape(param_name)}\s*:\s*$", line):
            return True

    return False


def _ensure_debian_ami_parameter(template_body: str) -> str:
    """Normalize AMI references from model output into a guaranteed parameter."""
    updated = template_body

    # Unify problematic model outputs to one deploy-time parameter.
    updated = re.sub(r"!Ref\s+DebianAMI\b", "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+Debian11AMI\b", "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+LatestAmiId\b", "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+LatestAmazonLinuxAMI\b", "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+AWS::SSM::Parameter::ImageId\b", "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+SsmAmiId\b", "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+AMIId\b", "!Ref WebAmiId", updated)
    updated = re.sub(r'!Ref\s+"SsmAmiId"', "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+'SsmAmiId'", "!Ref WebAmiId", updated)
    updated = re.sub(r'!Ref\s+"AMIId"', "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+'AMIId'", "!Ref WebAmiId", updated)
    updated = re.sub(r"!Ref\s+'AWS::SSM::Parameter::Value<String>'", "!Ref WebAmiId", updated)

    # Handle expanded YAML Ref form.
    updated = re.sub(r"(?m)^(\s*)Ref:\s*LatestAmiId\s*$", r"\1Ref: WebAmiId", updated)
    updated = re.sub(r"(?m)^(\s*)Ref:\s*LatestAmazonLinuxAMI\s*$", r"\1Ref: WebAmiId", updated)
    updated = re.sub(r"(?m)^(\s*)Ref:\s*AWS::SSM::Parameter::ImageId\s*$", r"\1Ref: WebAmiId", updated)
    updated = re.sub(r"(?m)^(\s*)Ref:\s*SsmAmiId\s*$", r"\1Ref: WebAmiId", updated)
    updated = re.sub(r"(?m)^(\s*)Ref:\s*AMIId\s*$", r"\1Ref: WebAmiId", updated)
    updated = re.sub(r"\$\{SsmAmiId\}", "${WebAmiId}", updated)
    updated = re.sub(r"\$\{AMIId\}", "${WebAmiId}", updated)

    # Some templates put AMI in Mappings and reference it with FindInMap; replace those too.
    updated = re.sub(r"!FindInMap\s*\[\s*DebianAMI\s*,[^\]]+\]", "!Ref WebAmiId", updated)

    # Ensure required parameter exists.
    if _template_has_parameter(updated, "WebAmiId"):
        return updated
    if "Parameters:" not in updated:
        return updated

    injection = (
        "  WebAmiId:\n"
        "    Type: AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>\n"
        "    Description: SSM parameter path for AMI\n"
        "    Default: /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64\n"
    )
    return updated.replace("Parameters:\n", "Parameters:\n" + injection, 1)


def _ensure_cidr_parameter_defaults(template_body: str) -> str:
    """Ensure common CIDR placeholders exist as Parameters with safe defaults.

    Generated templates often reference `${AppVpcCidrBlock}` in security group rules while
    incorrectly defining it under Outputs. This helper guarantees such parameters are present
    in the Parameters section before other sanitizers run.
    """
    updated = template_body

    if "Parameters:" not in updated:
        return updated

    cidr_defaults = [
        ("AppVpcCidrBlock", "10.0.0.0/16", "CIDR block for application VPC traffic."),
        ("AllowedCidr", "10.0.0.0/16", "CIDR block allowed to access application endpoints."),
    ]

    for param_name, default_value, description in cidr_defaults:
        has_ref = (
            f"${{{param_name}}}" in updated
            or re.search(rf"!Ref\s+{re.escape(param_name)}\b", updated) is not None
            or re.search(rf"(?m)^\s*Ref:\s*{re.escape(param_name)}\s*$", updated) is not None
        )
        if not has_ref:
            continue
        if _template_has_parameter(updated, param_name):
            continue

        injection = (
            f"  {param_name}:\n"
            "    Type: String\n"
            f"    Default: {default_value}\n"
            f"    Description: {description}\n"
        )
        updated = updated.replace("Parameters:\n", "Parameters:\n" + injection, 1)

    return updated


def _sanitize_conflicting_identifiers(template_body: str) -> str:
    """Remove hardcoded names that commonly conflict across redeploys/accounts."""
    updated = template_body

    # S3 bucket names are globally unique; hardcoded values frequently collide.
    updated = re.sub(r"(?m)^\s{6}BucketName:\s+[^\n#]+\s*(?:#.*)?$\n?", "", updated)

    # RDS identifier can also conflict if instance already exists/deletes lag.
    updated = re.sub(r"(?m)^\s{6}DBInstanceIdentifier:\s+[^\n#]+\s*(?:#.*)?$\n?", "", updated)

    return updated


def _sanitize_rds_properties(template_body: str) -> str:
    """Force conservative RDS settings to improve deploy success on constrained accounts."""
    updated = template_body

    # Some accounts reject higher retention values in free-tier/trial plans.
    updated = re.sub(
        r"(?m)^(\s*)BackupRetentionPeriod:\s*\d+\s*(?:#.*)?$",
        r"\1BackupRetentionPeriod: 1",
        updated,
    )

    # Avoid expensive/high-availability defaults that frequently fail in demo environments.
    updated = re.sub(
        r"(?m)^(\s*)MultiAZ:\s*true\s*(?:#.*)?$",
        r"\1MultiAZ: false",
        updated,
    )

    # Ensure stack teardown can succeed in iterative demo runs.
    updated = re.sub(
        r"(?m)^(\s*)DeletionProtection:\s*true\s*(?:#.*)?$",
        r"\1DeletionProtection: false",
        updated,
    )

    return updated


def _sanitize_ec2_instance_types(template_body: str) -> str:
    """Force EC2 instance sizes to a conservative value for demo/free-tier accounts."""
    updated = template_body
    updated = re.sub(
        r"(?m)^(\s*)InstanceType:\s*[^\n#]+\s*(?:#.*)?$",
        r"\1InstanceType: t3.micro",
        updated,
    )
    return updated


def _sanitize_public_ip_association(template_body: str) -> str:
    """Force public IP association for templates that commonly output Instance PublicIp."""
    updated = template_body
    updated = re.sub(
        r"(?mi)^(\s*)AssociatePublicIpAddress:\s*['\"]?false['\"]?\s*(?:#.*)?$",
        r"\1AssociatePublicIpAddress: true",
        updated,
    )
    return updated


def _sanitize_unresolvable_public_ip_outputs(template_body: str) -> str:
    """Replace fragile PublicIp output lookups with stable resource refs."""
    updated = template_body

    # Short-form YAML: Value: !GetAtt ApiServer.PublicIp
    updated = re.sub(
        r"(?m)^\s*Value:\s*!GetAtt\s+([A-Za-z0-9_-]+)\.PublicIp\s*$",
        r"    Value: !Ref \1",
        updated,
    )

    # JSON/YAML intrinsic list form: Fn::GetAtt: [ApiServer, PublicIp]
    updated = re.sub(
        r"(?m)^\s*Fn::GetAtt:\s*\[\s*([A-Za-z0-9_-]+)\s*,\s*PublicIp\s*\]\s*$",
        r"      Ref: \1",
        updated,
    )

    return updated


def _sanitize_invalid_type_lists(template_body: str) -> str:
    """Fix invalid `Type: [AWS::..., AWS::...]` outputs by selecting the first resource type."""

    def _replace(match: re.Match[str]) -> str:
        indent = match.group("indent")
        raw_types = match.group("types")
        parts = [p.strip().strip('"\'') for p in raw_types.split(",") if p.strip()]
        # Prefer a valid CloudFormation resource type (3-part AWS::X::Y form).
        valid_resource_types = [
            p for p in parts if re.match(r"^[A-Za-z0-9]{2,64}::[A-Za-z0-9]{2,64}::[A-Za-z0-9]{2,64}(::MODULE)?$", p)
        ]
        aws_types = valid_resource_types or [p for p in parts if p.startswith("AWS::")]
        if not aws_types:
            return match.group(0)
        return f"{indent}Type: {aws_types[0]}"

    return re.sub(
        r"(?m)^(?P<indent>\s*)Type:\s*[\"']?\[(?P<types>[^\]]+)\][\"']?\s*$",
        _replace,
        template_body,
    )


def _sanitize_invalid_parameter_value_resources(template_body: str) -> str:
    """Remove invalid resources emitted with `Type: AWS::SSM::Parameter::Value<...>`.

    These are parameter *types*, not valid CloudFormation resource types.
    """
    lines = template_body.splitlines()
    out: list[str] = []

    in_resources = False
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("Resources:"):
            in_resources = True
            out.append(line)
            i += 1
            continue

        if in_resources and line and not line.startswith(" "):
            in_resources = False

        if in_resources and re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", line):
            block_start = i
            block_end = i + 1
            while block_end < len(lines) and not re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", lines[block_end]):
                if lines[block_end] and not lines[block_end].startswith(" "):
                    break
                block_end += 1

            block = lines[block_start:block_end]
            joined = "\n".join(block)
            if re.search(r"(?m)^\s{4}Type:\s*AWS::SSM::Parameter::Value<[^>]+>\s*$", joined):
                i = block_end
                continue

            out.extend(block)
            i = block_end
            continue

        out.append(line)
        i += 1

    return "\n".join(out) + ("\n" if template_body.endswith("\n") else "")


def _sanitize_invalid_outputs(template_body: str) -> str:
    """Remove invalid Outputs entries that use parameter-only keys like Type/Default."""
    lines = template_body.splitlines()
    out: list[str] = []

    in_outputs = False
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("Outputs:"):
            in_outputs = True
            out.append(line)
            i += 1
            continue

        if in_outputs and line and not line.startswith(" "):
            in_outputs = False

        if in_outputs and re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", line):
            block_start = i
            block_end = i + 1
            while block_end < len(lines):
                if re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", lines[block_end]):
                    break
                if lines[block_end] and not lines[block_end].startswith(" "):
                    break
                block_end += 1

            block = lines[block_start:block_end]
            joined = "\n".join(block)
            if re.search(r"(?m)^\s{4}(Type|Default):\s*", joined):
                i = block_end
                continue

            out.extend(block)
            i = block_end
            continue

        out.append(line)
        i += 1

    return "\n".join(out) + ("\n" if template_body.endswith("\n") else "")


def _sanitize_malformed_parameter_headers(template_body: str) -> str:
    """Repair parameter declarations accidentally emitted as inline scalar assignments.

    Some model outputs produce lines like `InstanceType: t3.micro` inside the Parameters
    block, which CloudFormation treats as malformed YAML. This normalizes those lines to a
    valid parameter header and gives them a conservative String type when no explicit Type
    is already present.
    """
    lines = template_body.splitlines()
    out: list[str] = []

    in_parameters = False
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("Parameters:"):
            in_parameters = True
            out.append(line)
            i += 1
            continue

        if in_parameters and line and not line.startswith(" "):
            in_parameters = False

        if in_parameters:
            malformed = re.match(r"^(\s{2})([A-Za-z0-9_-]+):\s+[^#\n]+(?:\s*#.*)?$", line)
            if malformed:
                indent = malformed.group(1)
                key = malformed.group(2)
                out.append(f"{indent}{key}:")
                out.append(f"{indent}  Type: String")
                i += 1
                continue

        out.append(line)
        i += 1

    return "\n".join(out) + ("\n" if template_body.endswith("\n") else "")


def _sanitize_s3_bucket_properties(template_body: str) -> str:
    """Remove S3 properties that commonly fail create/update when model output is too eager.

    Empty lifecycle rule lists are not valid for bucket creation in many cases, and the
    explicit `AccessControl: Private` field is redundant when public access block settings
    are already present. Keeping the bucket conservative avoids a class of S3 schema errors.
    """
    lines = template_body.splitlines()
    out: list[str] = []

    in_resources = False
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("Resources:"):
            in_resources = True
            out.append(line)
            i += 1
            continue

        if in_resources and line and not line.startswith(" "):
            in_resources = False

        if in_resources and re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", line):
            block_start = i
            block_end = i + 1
            while block_end < len(lines):
                if re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", lines[block_end]):
                    break
                if lines[block_end] and not lines[block_end].startswith(" "):
                    break
                block_end += 1

            block = lines[block_start:block_end]
            joined = "\n".join(block)

            if re.search(r"(?m)^\s{4}Type:\s*AWS::S3::Bucket\s*$", joined):
                filtered: list[str] = []
                j = 0
                while j < len(block):
                    bline = block[j]
                    if re.match(r"^\s{6}LifecycleConfiguration:\s*$", bline):
                        j += 1
                        while j < len(block) and (block[j].startswith("        ") or block[j].strip() == ""):
                            j += 1
                        continue
                    if re.match(r"^\s{6}AccessControl:\s*Private\s*(?:#.*)?$", bline):
                        j += 1
                        continue
                    filtered.append(bline)
                    j += 1
                out.extend(filtered)
                i = block_end
                continue

            out.extend(block)
            i = block_end
            continue

        out.append(line)
        i += 1

    return "\n".join(out) + ("\n" if template_body.endswith("\n") else "")


def _sanitize_ec2_block_device_mappings(template_body: str) -> str:
    """Replace invalid `Ebs.VolumeId` block-device settings with valid root-volume fields.

    CloudFormation EC2 `BlockDeviceMappings[].Ebs` does not accept `VolumeId`; that property
    belongs to a separate `AWS::EC2::VolumeAttachment` resource. For generated templates that
    accidentally mix the two patterns, we keep the mapping but turn it into a valid root volume
    specification using conservative defaults.
    """
    updated = template_body

    # Exact generated pattern from current templates.
    updated = updated.replace(
        """      BlockDeviceMappings:\n        - DeviceName: /dev/sda1\n          Ebs:\n            VolumeId: !Ref Instance2EBS""",
        """      BlockDeviceMappings:\n        - DeviceName: /dev/sda1\n          Ebs:\n            VolumeSize: 10\n            VolumeType: gp2\n            Encrypted: true\n            DeleteOnTermination: true""",
    )

    # Generic fallback: if any Ebs block uses VolumeId, rewrite it into valid root-volume fields.
    updated = re.sub(
        r"(?ms)^([ \t]{6}BlockDeviceMappings:\s*\n[ \t]{8}- DeviceName: [^\n]+\n[ \t]{10}Ebs:\s*\n)[ \t]{12}VolumeId:\s*[^\n]+\n?",
        r"\1            VolumeSize: 10\n            VolumeType: gp2\n            Encrypted: true\n            DeleteOnTermination: true\n",
        updated,
    )

    return updated


def _ensure_ssm_instance_profile(template_body: str) -> str:
    """Ensure every EC2 instance can run SSM commands via IAM instance profile.

    This is enforced for all templates containing EC2 instances so app-driven post-deploy
    automation (SSM RunCommand) works without manual IAM edits.
    """
    updated = template_body

    if "AWS::EC2::Instance" not in updated:
        return updated

    # Normalize raw role references to the instance profile we manage.
    updated = re.sub(r"!Ref\s+IAMRole\b", "!Ref MigraiSSMInstanceProfile", updated)
    updated = re.sub(r"(?m)^(\s*)Ref:\s*IAMRole\s*$", r"\1Ref: MigraiSSMInstanceProfile", updated)

    has_role = bool(re.search(r"(?m)^\s{2}MigraiSSMInstanceRole:\s*$", updated))
    has_profile = bool(re.search(r"(?m)^\s{2}MigraiSSMInstanceProfile:\s*$", updated))

    if not (has_role and has_profile):
        injection = (
            "  MigraiSSMInstanceRole:\n"
            "    Type: AWS::IAM::Role\n"
            "    Properties:\n"
            "      AssumeRolePolicyDocument:\n"
            "        Version: '2012-10-17'\n"
            "        Statement:\n"
            "          - Effect: Allow\n"
            "            Principal:\n"
            "              Service: ec2.amazonaws.com\n"
            "            Action: sts:AssumeRole\n"
            "      ManagedPolicyArns:\n"
            "        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore\n"
            "\n"
            "  MigraiSSMInstanceProfile:\n"
            "    Type: AWS::IAM::InstanceProfile\n"
            "    Properties:\n"
            "      Roles:\n"
            "        - !Ref MigraiSSMInstanceRole\n"
            "\n"
        )
        if "Resources:\n" in updated:
            updated = updated.replace("Resources:\n", "Resources:\n" + injection, 1)

    # Ensure every EC2 instance has IamInstanceProfile configured.
    lines = updated.splitlines()
    out: list[str] = []
    i = 0
    in_resources = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("Resources:"):
            in_resources = True
            out.append(line)
            i += 1
            continue

        if in_resources and line and not line.startswith(" "):
            in_resources = False

        if in_resources and re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", line):
            block_start = i
            block_end = i + 1
            while block_end < len(lines):
                if re.match(r"^\s{2}[A-Za-z0-9_-]+:\s*$", lines[block_end]):
                    break
                if lines[block_end] and not lines[block_end].startswith(" "):
                    break
                block_end += 1

            block = lines[block_start:block_end]
            joined = "\n".join(block)

            if re.search(r"(?m)^\s{4}Type:\s*AWS::EC2::Instance\s*$", joined):
                has_properties = any(re.match(r"^\s{4}Properties:\s*$", b) for b in block)
                has_profile_ref = any(re.match(r"^\s{6}IamInstanceProfile:\s*", b) for b in block)

                if not has_profile_ref:
                    if has_properties:
                        new_block: list[str] = []
                        injected = False
                        for b in block:
                            new_block.append(b)
                            if not injected and re.match(r"^\s{4}Properties:\s*$", b):
                                new_block.append("      IamInstanceProfile: !Ref MigraiSSMInstanceProfile")
                                injected = True
                        block = new_block
                    else:
                        # Rare malformed EC2 resource without Properties.
                        inserted = False
                        new_block = []
                        for b in block:
                            new_block.append(b)
                            if not inserted and re.match(r"^\s{4}Type:\s*AWS::EC2::Instance\s*$", b):
                                new_block.append("    Properties:")
                                new_block.append("      IamInstanceProfile: !Ref MigraiSSMInstanceProfile")
                                inserted = True
                        block = new_block

            out.extend(block)
            i = block_end
            continue

        out.append(line)
        i += 1

    return "\n".join(out) + ("\n" if updated.endswith("\n") else "")


def build_parameters(template_body: str, region: str, log: Optional[Callable[[str], None]] = None) -> list[dict[str, str]]:
    logger = log or print
    ec2 = boto3.client("ec2", region_name=region)
    rds = boto3.client("rds", region_name=region)

    auto_vpc_id, auto_subnet_id, all_subnets = _discover_network_defaults(ec2)
    auto_allowed_cidr = os.getenv("CFN_PARAM_ALLOWED_CIDR", "10.0.0.0/16").strip() or "10.0.0.0/16"
    auto_key_pair = _ensure_key_pair(ec2)
    auto_db_subnet_group = _ensure_db_subnet_group(rds, all_subnets)
    auto_security_group = _ensure_security_group(ec2, auto_vpc_id, auto_allowed_cidr)

    parameter_aliases = {
        "VpcId": "CFN_PARAM_VPC_ID",
        "VPCId": "CFN_PARAM_VPC_ID",
        "VPC": "CFN_PARAM_VPC_ID",
        "WebSubnetId": "CFN_PARAM_WEB_SUBNET_ID",
        "SubnetId": "CFN_PARAM_WEB_SUBNET_ID",
        "AppSubnet": "CFN_PARAM_WEB_SUBNET_ID",
        "KeyPairName": "CFN_PARAM_KEY_PAIR_NAME",
        "KeyName": "CFN_PARAM_KEY_PAIR_NAME",
        "KeyPair": "CFN_PARAM_KEY_PAIR_NAME",
        "AllowedCidr": "CFN_PARAM_ALLOWED_CIDR",
        "AppVpcCidrBlock": "CFN_PARAM_ALLOWED_CIDR",
        "CIDRBlock": "CFN_PARAM_ALLOWED_CIDR",
        "WebAmiId": "CFN_PARAM_WEB_AMI_ID",
        "DebianAMI": "CFN_PARAM_WEB_AMI_ID",
        "SsmAmiId": "CFN_PARAM_WEB_AMI_ID",
        "AMIId": "CFN_PARAM_WEB_AMI_ID",
        "DBSubnetGroupName": "CFN_PARAM_DB_SUBNET_GROUP_NAME",
        "DBSubnetGroup": "CFN_PARAM_DB_SUBNET_GROUP_NAME",
        "DBUsername": "CFN_PARAM_DB_USERNAME",
        "DbUsername": "CFN_PARAM_DB_USERNAME",
        "MasterUsername": "CFN_PARAM_DB_USERNAME",
        "DBPassword": "CFN_PARAM_DB_PASSWORD",
        "DbPassword": "CFN_PARAM_DB_PASSWORD",
        "MasterUserPassword": "CFN_PARAM_DB_PASSWORD",
        "DBInstanceClass": "CFN_PARAM_DB_INSTANCE_CLASS",
        "VPCSecurityGroup": "CFN_PARAM_VPC_SECURITY_GROUP",
        "SecurityGroup": "CFN_PARAM_VPC_SECURITY_GROUP",
        "SecurityGroupId": "CFN_PARAM_VPC_SECURITY_GROUP",
        "AppSecurityGroup": "CFN_PARAM_VPC_SECURITY_GROUP",
        "DatabaseName": "CFN_PARAM_DATABASE_NAME",
        "DBName": "CFN_PARAM_DATABASE_NAME",
    }

    defaults = {
        "CFN_PARAM_VPC_ID": auto_vpc_id,
        "CFN_PARAM_WEB_SUBNET_ID": auto_subnet_id,
        "CFN_PARAM_KEY_PAIR_NAME": auto_key_pair,
        "CFN_PARAM_DB_SUBNET_GROUP_NAME": auto_db_subnet_group,
        "CFN_PARAM_DB_USERNAME": "migraiadmin",
        "CFN_PARAM_DB_PASSWORD": "Migrai#2026Pass!",
        "CFN_PARAM_ALLOWED_CIDR": auto_allowed_cidr,
        "CFN_PARAM_WEB_AMI_ID": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
        "CFN_PARAM_DB_INSTANCE_CLASS": "db.t3.micro",
        "CFN_PARAM_VPC_SECURITY_GROUP": auto_security_group,
        "CFN_PARAM_DATABASE_NAME": "appdb",
    }

    missing = []
    params = []
    for param_key, env_key in parameter_aliases.items():
        if not _template_has_parameter(template_body, param_key):
            continue
        value = os.getenv(env_key, defaults.get(env_key, "")).strip()
        if not value:
            missing.append(env_key)
            continue
        params.append({"ParameterKey": param_key, "ParameterValue": value})

    if missing:
        raise ValueError(
            "Missing required deployment env vars: " + ", ".join(missing)
        )

    logger(
        "Auto-parameter fallback used: "
        f"VpcId={defaults['CFN_PARAM_VPC_ID']}, "
        f"WebSubnetId={defaults['CFN_PARAM_WEB_SUBNET_ID']}, "
        f"KeyPairName={defaults['CFN_PARAM_KEY_PAIR_NAME']}, "
        f"DBSubnetGroupName={defaults['CFN_PARAM_DB_SUBNET_GROUP_NAME']}, "
        f"VPCSecurityGroup={defaults['CFN_PARAM_VPC_SECURITY_GROUP']}, "
        f"DatabaseName={defaults['CFN_PARAM_DATABASE_NAME']}"
    )

    return params


def deploy(log: Optional[Callable[[str], None]] = None) -> dict:
    load_dotenv()
    logger = log or print

    stack_name = os.getenv("CFN_STACK_NAME", STACK_NAME)

    region = os.getenv("AWS_REGION", "us-east-1")
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Template not found: {TEMPLATE_PATH}")

    template_body = TEMPLATE_PATH.read_text(encoding="utf-8")
    template_body = _ensure_debian_ami_parameter(template_body)
    template_body = _ensure_cidr_parameter_defaults(template_body)
    template_body = _sanitize_conflicting_identifiers(template_body)
    template_body = _sanitize_rds_properties(template_body)
    template_body = _sanitize_ec2_instance_types(template_body)
    template_body = _sanitize_public_ip_association(template_body)
    template_body = _sanitize_unresolvable_public_ip_outputs(template_body)
    template_body = _sanitize_invalid_type_lists(template_body)
    template_body = _sanitize_invalid_parameter_value_resources(template_body)
    template_body = _sanitize_invalid_outputs(template_body)
    template_body = _sanitize_malformed_parameter_headers(template_body)
    template_body = _sanitize_s3_bucket_properties(template_body)
    template_body = _sanitize_ec2_block_device_mappings(template_body)
    template_body = _ensure_ssm_instance_profile(template_body)
    if template_body != TEMPLATE_PATH.read_text(encoding="utf-8"):
        TEMPLATE_PATH.write_text(template_body, encoding="utf-8")
        logger("Patched template for deployability (AMI refs, conflicting identifiers, RDS defaults, and type normalization).")

    parameters = build_parameters(template_body, region, log=logger)

    cfn = boto3.client("cloudformation", region_name=region)

    logger(f"Using region: {region}")
    logger(f"Using stack: {stack_name}")
    logger(f"Using {len(parameters)} CloudFormation parameters from environment.")
    logger("Validating template...")
    try:
        cfn.validate_template(TemplateBody=template_body)
        logger("Template validation passed.")
    except ClientError as exc:
        err = str(exc)
        # Some generated templates can trigger a ValidateTemplate typeNameList parser edge case.
        # CloudFormation create/update still performs full validation, so continue and surface
        # any real template errors there with resource-level context.
        if "typeNameList" in err and "ValidateTemplate" in err:
            logger("ValidateTemplate returned a typeNameList parser error; continuing to deploy path.")
        else:
            raise

    exists = True
    stack_status = None
    try:
        stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
        stack_status = stack.get("StackStatus")
    except ClientError as exc:
        msg = str(exc)
        if "does not exist" in msg:
            exists = False
        else:
            raise

    if not exists:
        logger("Creating stack...")
        cfn.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=parameters,
            Capabilities=["CAPABILITY_NAMED_IAM"],
        )
        waiter = cfn.get_waiter("stack_create_complete")
        try:
            waiter.wait(StackName=stack_name)
        except WaiterError as exc:
            status = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]["StackStatus"]
            logger(f"Create waiter failed. Current stack status: {status}")
            events = cfn.describe_stack_events(StackName=stack_name).get("StackEvents", [])[:20]
            for event in events:
                reason = event.get("ResourceStatusReason", "")
                logger(
                    f"EVENT | {event.get('LogicalResourceId')} | {event.get('ResourceStatus')} | {reason}"
                )
            raise RuntimeError(f"CloudFormation create failed with status {status}") from exc
        logger("Stack create complete.")
        return {"stack_name": stack_name, "region": region, "action": "create", "status": "complete"}

    logger(f"Existing stack status: {stack_status}")
    if stack_status == "ROLLBACK_COMPLETE":
        logger("Stack is ROLLBACK_COMPLETE. Deleting and recreating stack...")
        cfn.delete_stack(StackName=stack_name)
        cfn.get_waiter("stack_delete_complete").wait(StackName=stack_name)
        logger("Stack delete complete. Creating fresh stack...")
        cfn.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=parameters,
            Capabilities=["CAPABILITY_NAMED_IAM"],
        )
        try:
            cfn.get_waiter("stack_create_complete").wait(StackName=stack_name)
        except WaiterError as recreate_exc:
            recreate_status = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]["StackStatus"]
            logger(f"Recreate waiter failed. Current stack status: {recreate_status}")
            recreate_events = cfn.describe_stack_events(StackName=stack_name).get("StackEvents", [])[:20]
            for event in recreate_events:
                reason = event.get("ResourceStatusReason", "")
                logger(
                    f"EVENT | {event.get('LogicalResourceId')} | {event.get('ResourceStatus')} | {reason}"
                )
            raise RuntimeError(f"CloudFormation recreate failed with status {recreate_status}") from recreate_exc
        logger("Fresh stack create complete.")
        return {
            "stack_name": stack_name,
            "region": region,
            "action": "recreate",
            "status": "complete",
        }

    logger("Updating stack...")
    try:
        cfn.update_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Parameters=parameters,
            Capabilities=["CAPABILITY_NAMED_IAM"],
        )
    except ClientError as exc:
        if "No updates are to be performed" in str(exc):
            logger("No updates to perform. Stack is already up to date.")
            return {"stack_name": stack_name, "region": region, "action": "update", "status": "no-op"}
        raise

    waiter = cfn.get_waiter("stack_update_complete")
    try:
        waiter.wait(StackName=stack_name)
        logger("Stack update complete.")
        return {"stack_name": stack_name, "region": region, "action": "update", "status": "complete"}
    except WaiterError as exc:
        status = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]["StackStatus"]
        logger(f"Update waiter failed. Current stack status: {status}")

        events = cfn.describe_stack_events(StackName=stack_name).get("StackEvents", [])[:15]
        for event in events:
            reason = event.get("ResourceStatusReason", "")
            logger(
                f"EVENT | {event.get('LogicalResourceId')} | {event.get('ResourceStatus')} | {reason}"
            )

        # Recovery path: update rollback complete stacks cannot be updated further.
        if status == "UPDATE_ROLLBACK_COMPLETE":
            logger("Stack is UPDATE_ROLLBACK_COMPLETE. Recreating stack from current template...")
            cfn.delete_stack(StackName=stack_name)
            cfn.get_waiter("stack_delete_complete").wait(StackName=stack_name)
            logger("Stack delete complete. Creating fresh stack...")
            cfn.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=parameters,
                Capabilities=["CAPABILITY_NAMED_IAM"],
            )
            try:
                cfn.get_waiter("stack_create_complete").wait(StackName=stack_name)
            except WaiterError as recreate_exc:
                recreate_status = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]["StackStatus"]
                logger(f"Recreate waiter failed. Current stack status: {recreate_status}")
                recreate_events = cfn.describe_stack_events(StackName=stack_name).get("StackEvents", [])[:20]
                for event in recreate_events:
                    reason = event.get("ResourceStatusReason", "")
                    logger(
                        f"EVENT | {event.get('LogicalResourceId')} | {event.get('ResourceStatus')} | {reason}"
                    )
                raise RuntimeError(f"CloudFormation recreate failed with status {recreate_status}") from recreate_exc
            logger("Fresh stack create complete.")
            return {
                "stack_name": stack_name,
                "region": region,
                "action": "recreate",
                "status": "complete",
            }

        raise RuntimeError(f"CloudFormation update failed with status {status}") from exc


def main() -> None:
    deploy()


if __name__ == "__main__":
    main()
