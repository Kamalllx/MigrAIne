"""
Diagram generator: creates Mermaid diagrams from GCP input JSON and AWS CloudFormation YAML.
"""

import json
import re
from typing import Any, Dict, List, Set, Tuple


class DiagramGenerator:
    """Generate Mermaid architecture diagrams from migration input."""

    # Mapping of GCP resource types to display names and categories
    GCP_RESOURCE_ICONS = {
        "google_compute_network": ("VPC", "🌐"),
        "google_compute_subnetwork": ("Subnet", "🔗"),
        "google_compute_firewall": ("Firewall", "🔥"),
        "google_compute_instance": ("VM", "💻"),
        "google_storage_bucket": ("Storage", "📦"),
        "google_sql_database_instance": ("Cloud SQL", "🗄️"),
        "google_cloudfunctions_function": ("Function", "⚡"),
        "google_pubsub_topic": ("Pub/Sub", "📨"),
        "google_redis_instance": ("Redis Cache", "⚙️"),
    }

    # Mapping of AWS resource types to display names and categories
    AWS_RESOURCE_ICONS = {
        "AWS::EC2::VPC": ("VPC", "🌐"),
        "AWS::EC2::Subnet": ("Subnet", "🔗"),
        "AWS::EC2::SecurityGroup": ("Security Group", "🔥"),
        "AWS::EC2::Instance": ("EC2", "💻"),
        "AWS::S3::Bucket": ("S3", "📦"),
        "AWS::RDS::DBInstance": ("RDS", "🗄️"),
        "AWS::Lambda::Function": ("Lambda", "⚡"),
        "AWS::SNS::Topic": ("SNS", "📨"),
        "AWS::SQS::Queue": ("SQS", "📨"),
        "AWS::ElastiCache::CacheCluster": ("ElastiCache", "⚙️"),
    }

    # Generic equivalence rules (not hardcoded service-to-service mappings).
    TOKEN_GROUPS = [
        {"network", "vpc"},
        {"subnet", "subnetwork"},
        {"firewall", "securitygroup", "security", "sg"},
        {"instance", "vm", "ec2", "compute"},
        {"bucket", "storage", "s3", "object"},
        {"database", "db", "sql", "rds", "postgres", "mysql"},
        {"function", "lambda", "cloudfunction", "serverless"},
        {"topic", "queue", "pubsub", "sns", "sqs", "messaging"},
        {"cache", "redis", "elasticache"},
        {"gateway", "apigateway", "api"},
    ]

    GCP_CATEGORY_CLASS = {
        "network": "networkNode",
        "compute": "computeNode",
        "storage": "storageNode",
        "database": "databaseNode",
        "serverless": "serverlessNode",
        "messaging": "messagingNode",
        "cache": "cacheNode",
        "other": "defaultNode",
    }

    AWS_TYPE_CLASS = {
        "AWS::EC2::VPC": "networkNode",
        "AWS::EC2::Subnet": "networkNode",
        "AWS::EC2::SecurityGroup": "networkNode",
        "AWS::EC2::Instance": "computeNode",
        "AWS::S3::Bucket": "storageNode",
        "AWS::RDS::DBInstance": "databaseNode",
        "AWS::Lambda::Function": "serverlessNode",
        "AWS::SNS::Topic": "messagingNode",
        "AWS::SQS::Queue": "messagingNode",
        "AWS::ElastiCache::CacheCluster": "cacheNode",
    }

    AWS_CLASS_TITLE = {
        "networkNode": "AWS Network",
        "computeNode": "AWS Compute",
        "storageNode": "AWS Storage",
        "databaseNode": "AWS Database",
        "serverlessNode": "AWS Serverless",
        "messagingNode": "AWS Messaging",
        "cacheNode": "AWS Cache",
        "defaultNode": "AWS Other",
    }

    @staticmethod
    def _theme_block() -> str:
        return (
            "  classDef defaultNode fill:#2a1028,stroke:#f9a8d4,color:#ffe4f3,stroke-width:1.8px;\n"
            "  classDef networkNode fill:#31102d,stroke:#fbcfe8,color:#fff1f7,stroke-width:1.8px;\n"
            "  classDef computeNode fill:#3a1333,stroke:#f9a8d4,color:#fff1f7,stroke-width:1.8px;\n"
            "  classDef storageNode fill:#291025,stroke:#fbcfe8,color:#fff1f7,stroke-width:1.8px;\n"
            "  classDef databaseNode fill:#3f1738,stroke:#fda4af,color:#fff1f2,stroke-width:1.8px;\n"
            "  classDef serverlessNode fill:#4a1038,stroke:#f9a8d4,color:#fff1f7,stroke-width:1.8px;\n"
            "  classDef messagingNode fill:#341025,stroke:#fbcfe8,color:#fff1f7,stroke-width:1.8px;\n"
            "  classDef cacheNode fill:#2f1226,stroke:#fda4af,color:#fff1f2,stroke-width:1.8px;\n"
            "  linkStyle default stroke:#f9a8d4,stroke-width:1.6px;\n"
        )

    @staticmethod
    def _extract_gcp_type(resource: Dict[str, Any]) -> str:
        for key in (
            "gcp_resource_type",
            "source_type",
            "provider_resource_type",
            "resource_type",
            "type",
            "kind",
        ):
            value = resource.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "unknown"

    @staticmethod
    def _normalize_tokens(type_name: str) -> Set[str]:
        lowered = type_name.lower()
        raw_tokens = re.split(r"[^a-z0-9]+", lowered)
        tokens = {
            token
            for token in raw_tokens
            if token and token not in {"google", "aws", "cloud", "resource", "services", "service"}
        }
        return tokens

    @classmethod
    def _expand_tokens(cls, tokens: Set[str]) -> Set[str]:
        expanded = set(tokens)
        changed = True
        while changed:
            changed = False
            for group in cls.TOKEN_GROUPS:
                if expanded.intersection(group):
                    new_tokens = group - expanded
                    if new_tokens:
                        expanded.update(new_tokens)
                        changed = True
        return expanded

    @classmethod
    def _infer_class_from_type(cls, type_name: str, provider_category: str = "") -> str:
        category = (provider_category or "").lower()
        if category in cls.GCP_CATEGORY_CLASS:
            return cls.GCP_CATEGORY_CLASS[category]

        tokens = cls._expand_tokens(cls._normalize_tokens(type_name))
        if {"network", "vpc", "subnet", "subnetwork", "firewall", "securitygroup"}.intersection(tokens):
            return "networkNode"
        if {"instance", "vm", "compute", "ec2"}.intersection(tokens):
            return "computeNode"
        if {"bucket", "storage", "s3", "object"}.intersection(tokens):
            return "storageNode"
        if {"database", "db", "sql", "rds", "postgres", "mysql"}.intersection(tokens):
            return "databaseNode"
        if {"function", "lambda", "cloudfunction", "serverless"}.intersection(tokens):
            return "serverlessNode"
        if {"topic", "queue", "pubsub", "sns", "sqs", "messaging"}.intersection(tokens):
            return "messagingNode"
        if {"cache", "redis", "elasticache"}.intersection(tokens):
            return "cacheNode"
        return "defaultNode"

    @classmethod
    def _match_score(cls, gcp_type: str, aws_type: str, gcp_category: str = "") -> float:
        gcp_tokens = cls._expand_tokens(cls._normalize_tokens(gcp_type))
        aws_tokens = cls._expand_tokens(cls._normalize_tokens(aws_type))
        if not gcp_tokens or not aws_tokens:
            return 0.0

        overlap = gcp_tokens.intersection(aws_tokens)
        if not overlap:
            return 0.0

        base = len(overlap) / max(len(gcp_tokens.union(aws_tokens)), 1)
        gcp_class = cls._infer_class_from_type(gcp_type, gcp_category)
        aws_class = cls._infer_class_from_type(aws_type)
        class_bonus = 0.25 if gcp_class == aws_class else 0.0
        return base + class_bonus

    @classmethod
    def _infer_mappings(
        cls,
        gcp_resources: List[Dict[str, Any]],
        aws_resources: Dict[str, Dict[str, Any]],
    ) -> List[Tuple[str, str]]:
        candidates: List[Tuple[float, str, str]] = []
        for resource in gcp_resources:
            gcp_id = resource.get("id", "")
            gcp_type = cls._extract_gcp_type(resource)
            gcp_category = resource.get("category", "")
            if not gcp_id:
                continue

            for aws_name, aws_info in aws_resources.items():
                score = cls._match_score(gcp_type, aws_info.get("type", ""), gcp_category)
                if score > 0.15:
                    candidates.append((score, gcp_id, aws_name))

        # Greedy best-match assignment, one-to-one.
        candidates.sort(key=lambda item: item[0], reverse=True)
        used_gcp: Set[str] = set()
        used_aws: Set[str] = set()
        mappings: List[Tuple[str, str]] = []
        for _score, gcp_id, aws_name in candidates:
            if gcp_id in used_gcp or aws_name in used_aws:
                continue
            used_gcp.add(gcp_id)
            used_aws.add(aws_name)
            mappings.append((gcp_id, aws_name))

        return mappings

    @staticmethod
    def _normalize_input_payload(parsed: Any) -> Dict[str, Any]:
        if isinstance(parsed, list):
            return {"resources": parsed}

        if isinstance(parsed, dict):
            for key in ("resources", "items", "data", "migration_resources"):
                value = parsed.get(key)
                if isinstance(value, list):
                    normalized = dict(parsed)
                    normalized["resources"] = value
                    return normalized
            parsed = dict(parsed)
            parsed.setdefault("resources", [])
            return parsed

        raise ValueError("source_json must be a JSON object or array")

    @classmethod
    def parse_json_input(cls, json_content: str) -> Dict[str, Any]:
        """Parse source JSON and accept multiple payload shapes.

        Also tolerates concatenated JSON blocks by picking the first object/array that
        contains a resources-like list.
        """
        text = json_content.strip()
        try:
            return cls._normalize_input_payload(json.loads(text))
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            values: List[Any] = []
            idx = 0
            while idx < len(text):
                while idx < len(text) and text[idx].isspace():
                    idx += 1
                if idx >= len(text):
                    break
                try:
                    value, next_idx = decoder.raw_decode(text, idx)
                except json.JSONDecodeError:
                    break
                values.append(value)
                idx = next_idx

            if not values:
                raise ValueError("source_json is not valid JSON")

            for value in values:
                normalized = cls._normalize_input_payload(value)
                if normalized.get("resources"):
                    return normalized

            return cls._normalize_input_payload(values[0])

    @staticmethod
    def parse_cloudformation_yaml(yaml_content: str) -> Dict[str, Any]:
        """
        Parse CloudFormation YAML to extract resources.
        Simple regex-based YAML parsing for basic structure.
        """
        resources: Dict[str, Dict[str, Any]] = {}
        
        # Extract Resources section - more flexible pattern
        resources_match = re.search(
            r'Resources:\s*\n(.*?)(?=\nOutputs:|$)', 
            yaml_content, 
            re.DOTALL | re.IGNORECASE
        )
        if not resources_match:
            # Try without requiring Outputs
            resources_match = re.search(
                r'Resources:\s*\n(.*?)$',
                yaml_content,
                re.DOTALL | re.IGNORECASE
            )
        
        if not resources_match:
            return resources

        resource_block = resources_match.group(1)

        # Build resource blocks by scanning top-level resource keys under Resources.
        key_matches = list(re.finditer(r'(?m)^\s{2}(\w+):\s*$', resource_block))
        if not key_matches:
            return resources

        raw_blocks: Dict[str, str] = {}
        for index, match in enumerate(key_matches):
            resource_name = match.group(1)
            start = match.start()
            end = key_matches[index + 1].start() if index + 1 < len(key_matches) else len(resource_block)
            raw_blocks[resource_name] = resource_block[start:end]

        # First pass: collect type and normalized name.
        for resource_name, block in raw_blocks.items():
            type_match = re.search(r'(?m)^\s{4}Type:\s*(.+?)\s*$', block)
            resource_type = type_match.group(1).strip() if type_match else "Unknown"
            resources[resource_name] = {
                "name": resource_name,
                "type": resource_type,
                "depends_on": [],
            }

        # Second pass: collect relationships via DependsOn and Ref/GetAtt usage.
        all_names = set(resources.keys())
        for resource_name, block in raw_blocks.items():
            deps = set()

            # DependsOn: single-line form
            for dep in re.findall(r'(?m)^\s{4}DependsOn:\s*(\w+)\s*$', block):
                if dep in all_names and dep != resource_name:
                    deps.add(dep)

            # DependsOn: list form
            depends_on_list_anchor = re.search(r'(?m)^\s{4}DependsOn:\s*$', block)
            if depends_on_list_anchor:
                anchor_pos = depends_on_list_anchor.end()
                tail = block[anchor_pos:]
                for dep in re.findall(r'(?m)^\s{6}-\s*(\w+)\s*$', tail):
                    if dep in all_names and dep != resource_name:
                        deps.add(dep)

            # !Ref short form and Ref long form
            for dep in re.findall(r'!Ref\s+(\w+)', block):
                if dep in all_names and dep != resource_name:
                    deps.add(dep)
            for dep in re.findall(r'(?m)^\s{6,}Ref:\s*(\w+)\s*$', block):
                if dep in all_names and dep != resource_name:
                    deps.add(dep)

            # !GetAtt forms
            for dep in re.findall(r'!GetAtt\s+(\w+)\.', block):
                if dep in all_names and dep != resource_name:
                    deps.add(dep)
            for dep in re.findall(r'!GetAtt\s*\[\s*(\w+)\s*,', block):
                if dep in all_names and dep != resource_name:
                    deps.add(dep)

            resources[resource_name]["depends_on"] = sorted(deps)

        return resources

    def _build_summary(self, json_input: Dict[str, Any], aws_resources: Dict[str, Any]) -> Dict[str, int]:
        gcp_resources = json_input.get("resources", [])
        gcp_count = len(gcp_resources)
        aws_count = len(aws_resources)

        mapped_count = len(self._infer_mappings(gcp_resources, aws_resources))

        added_count = max(aws_count - mapped_count, 0)

        gcp_edge_count = 0
        known_gcp_ids = {resource.get("id", "") for resource in gcp_resources}
        for resource in gcp_resources:
            for dep in resource.get("depends_on", []):
                if dep in known_gcp_ids:
                    gcp_edge_count += 1

        aws_edge_count = sum(len(resource_info.get("depends_on", [])) for resource_info in aws_resources.values())

        return {
            "gcp_count": gcp_count,
            "aws_count": aws_count,
            "added_count": added_count,
            "gcp_edge_count": gcp_edge_count,
            "aws_edge_count": aws_edge_count,
        }

    def generate_gcp_diagram(self, json_input: Dict[str, Any]) -> str:
        """Generate Mermaid diagram for GCP architecture."""
        if 'resources' not in json_input:
            return ""

        mermaid = "graph TD\n"
        resources = json_input.get('resources', [])
        
        # Group resources by category for subgraphs
        categories: Dict[str, List[Dict]] = {}
        node_classes: Dict[str, str] = {}

        for resource in resources:
            rid = resource.get('id', '')
            category = resource.get('category', 'other')
            if category == 'other':
                inferred_class = self._infer_class_from_type(self._extract_gcp_type(resource))
                category = {
                    'networkNode': 'network',
                    'computeNode': 'compute',
                    'storageNode': 'storage',
                    'databaseNode': 'database',
                    'serverlessNode': 'serverless',
                    'messagingNode': 'messaging',
                    'cacheNode': 'cache',
                }.get(inferred_class, 'other')
            if category not in categories:
                categories[category] = []
            categories[category].append(resource)

        # Build subgraphs by category
        subgraph_id = 0
        node_ids = {}  # Map resource id to node id

        for category, category_resources in categories.items():
            mermaid += f'  subgraph gcp{subgraph_id}["GCP {category.title()}"]\n'
            
            for resource in category_resources:
                rid = resource.get('id', '')
                gcp_type = self._extract_gcp_type(resource)
                config = resource.get('config', {})
                if not isinstance(config, dict):
                    config = {}
                migration_details = resource.get("migration_details", {})
                service_config = migration_details.get("service_configuration", {}) if isinstance(migration_details, dict) else {}
                if not isinstance(service_config, dict):
                    service_config = {}
                display_name = (
                    resource.get("name")
                    or config.get('name')
                    or service_config.get("name")
                    or rid
                )
                
                icon, _ = self.GCP_RESOURCE_ICONS.get(gcp_type, (gcp_type, "📌"))
                safe_node = rid.replace('-', '_')
                node_ids[rid] = safe_node
                node_classes[safe_node] = self._infer_class_from_type(gcp_type, category)

                # Rounded corners via () syntax
                mermaid += f'    {safe_node}("{icon} {display_name}");\n'
            
            mermaid += '  end\n'
            subgraph_id += 1

        # Add edges for dependencies
        for resource in resources:
            rid = resource.get('id', '')
            depends_on = resource.get('depends_on', []) or resource.get('dependencies', [])
            safe_node = node_ids.get(rid, '')
            
            for dep in depends_on:
                dep_node = node_ids.get(dep, '')
                if dep_node and safe_node:
                    mermaid += f'  {dep_node} --> {safe_node}\n'

        for node, class_name in node_classes.items():
            mermaid += f'  class {node} {class_name};\n'

        for i in range(subgraph_id):
            mermaid += f'  style gcp{i} fill:#120911,stroke:#f9a8d4,stroke-width:1.3px,color:#ffe4f3;\n'

        mermaid += self._theme_block()

        return mermaid

    def generate_aws_diagram(self, yaml_content: str) -> str:
        """Generate Mermaid diagram for AWS architecture."""
        resources = self.parse_cloudformation_yaml(yaml_content)
        
        if not resources:
            return ""

        mermaid = "graph TD\n"
        node_classes: Dict[str, str] = {}
        grouped_resources: Dict[str, List[tuple[str, Dict[str, Any]]]] = {}
        node_ids: Dict[str, str] = {}

        for resource_name, resource_info in resources.items():
            resource_type = resource_info.get('type', '')
            class_name = self._infer_class_from_type(resource_type)
            if class_name not in grouped_resources:
                grouped_resources[class_name] = []
            grouped_resources[class_name].append((resource_name, resource_info))

        subgraph_idx = 0
        for class_name, items in grouped_resources.items():
            title = self.AWS_CLASS_TITLE.get(class_name, "AWS Other")
            mermaid += f'  subgraph aws{subgraph_idx}["{title}"]\n'

            for resource_name, resource_info in items:
                resource_type = resource_info.get('type', '')
                display_name, icon = self.AWS_RESOURCE_ICONS.get(resource_type, (resource_name, "📌"))
                safe_node = resource_name.replace('-', '_')
                node_classes[safe_node] = class_name
                node_ids[resource_name] = safe_node

                # Rounded corners via () syntax
                mermaid += f'    {safe_node}("{icon} {display_name}");\n'

            mermaid += '  end\n'
            subgraph_idx += 1

        for resource_name, resource_info in resources.items():
            target_node = node_ids.get(resource_name, "")
            for dep_name in resource_info.get("depends_on", []):
                source_node = node_ids.get(dep_name, "")
                if source_node and target_node:
                    mermaid += f'  {source_node} --> {target_node}\n'

        for node, class_name in node_classes.items():
            mermaid += f'  class {node} {class_name};\n'

        for i in range(subgraph_idx):
            mermaid += f'  style aws{i} fill:#120911,stroke:#f9a8d4,stroke-width:1.3px,color:#ffe4f3;\n'

        mermaid += self._theme_block()

        return mermaid

    def generate_mapping_diagram(self, json_input: Dict[str, Any], aws_resources: Dict[str, Any]) -> str:
        """Generate Mermaid diagram showing GCP to AWS mapping."""
        gcp_resources = json_input.get('resources', [])
        
        if not gcp_resources:
            return ""

        mermaid = "graph LR\n"
        node_classes: Dict[str, str] = {}
        mermaid += '  subgraph gcp_side["GCP Resources"]\n'
        
        # Add GCP resources on left
        for i, resource in enumerate(gcp_resources):
            rid = resource.get('id', '')
            gcp_type = self._extract_gcp_type(resource)
            config = resource.get('config', {})
            if not isinstance(config, dict):
                config = {}
            migration_details = resource.get("migration_details", {})
            service_config = migration_details.get("service_configuration", {}) if isinstance(migration_details, dict) else {}
            if not isinstance(service_config, dict):
                service_config = {}
            display_name = (
                resource.get("name")
                or config.get('name')
                or service_config.get("name")
                or rid
            )
            icon, _ = self.GCP_RESOURCE_ICONS.get(gcp_type, (gcp_type, "📌"))
            safe_node = f"gcp_{rid}".replace('-', '_')
            category = resource.get('category', 'other')
            node_classes[safe_node] = self._infer_class_from_type(gcp_type, category)
            mermaid += f'    {safe_node}("{icon} {display_name}");\n'

        mermaid += '  end\n'
        mermaid += '  subgraph aws_side["AWS Resources"]\n'

        # Add AWS resources on right and create mappings
        for resource_name, resource_info in aws_resources.items():
            resource_type = resource_info.get('type', '')
            display_name, icon = self.AWS_RESOURCE_ICONS.get(resource_type, (resource_name, "📌"))
            safe_node = f"aws_{resource_name}".replace('-', '_')
            node_classes[safe_node] = self._infer_class_from_type(resource_type)
            mermaid += f'    {safe_node}("{icon} {display_name}");\n'

        mermaid += '  end\n'

        # Create mapping arrows using generic token-similarity matching.
        for gcp_id, aws_name in self._infer_mappings(gcp_resources, aws_resources):
            gcp_node = f"gcp_{gcp_id}".replace('-', '_')
            aws_node = f"aws_{aws_name}".replace('-', '_')
            mermaid += f'  {gcp_node} -.->|mapped| {aws_node};\n'

        for node, class_name in node_classes.items():
            mermaid += f'  class {node} {class_name};\n'

        mermaid += self._theme_block()
        mermaid += '  style gcp_side fill:#120911,stroke:#f9a8d4,stroke-width:1.3px,color:#ffe4f3;\n'
        mermaid += '  style aws_side fill:#120911,stroke:#f9a8d4,stroke-width:1.3px,color:#ffe4f3;\n'
        mermaid += '  linkStyle default stroke:#fbcfe8,stroke-width:1.6px,stroke-dasharray:5 3;\n'

        return mermaid

    def generate_all_diagrams(self, json_content: str, yaml_content: str) -> Dict[str, Any]:
        """
        Generate all three diagrams: GCP, AWS, and Mapping.
        Returns dict with keys: gcp_diagram, aws_diagram, mapping_diagram
        """
        json_input = self.parse_json_input(json_content)
        aws_resources = self.parse_cloudformation_yaml(yaml_content)

        return {
            'gcp_diagram': self.generate_gcp_diagram(json_input),
            'aws_diagram': self.generate_aws_diagram(yaml_content),
            'mapping_diagram': self.generate_mapping_diagram(json_input, aws_resources),
            'summary': self._build_summary(json_input, aws_resources),
        }
