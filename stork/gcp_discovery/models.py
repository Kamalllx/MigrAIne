from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime


@dataclass
class GCPResource:
    """Normalized GCP resource with full details."""
    id: str
    name: str
    asset_type: str
    category: str
    project: str
    location: Optional[str]
    labels: Dict[str, str]
    tags: List[str]
    config: Dict[str, Any]
    iam_policy: Optional[Dict]
    dependencies: List[str]
    raw: Dict[str, Any]
    discovered_at: datetime = field(default_factory=datetime.utcnow)