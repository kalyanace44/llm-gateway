"""Prism Cloud — paid features (eval, security scanning, cost optimization, compliance).

This module is proprietary. The OSS core works without it.
Cloud features activate when PRISM_CLOUD_KEY is set.
"""
__version__ = "0.1.0"

import os


def is_cloud_enabled() -> bool:
    """Check if Prism Cloud features are activated."""
    return bool(os.environ.get("PRISM_CLOUD_KEY"))
