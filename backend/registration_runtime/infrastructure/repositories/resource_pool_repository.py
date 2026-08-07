from __future__ import annotations

import sys
from pathlib import Path


project_root = Path(__file__).resolve().parents[4]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from backend.resource_pool import ResourceLease, ResourcePoolRepository  # noqa: E402


__all__ = ["ResourceLease", "ResourcePoolRepository"]
