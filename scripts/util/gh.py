"""Utility functions for interacting with GitHub."""

import logging
import os
from typing import Dict

import requests

LOG = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
DEFAULT_REPO = "canonical/k8s-snap"
REQUEST_TIMEOUT = 10


def _request_headers() -> Dict[str, str]:
    """Authenticate via GITHUB_TOKEN if set (avoids the 60 req/hr anonymous limit)."""
    token = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def is_ancestor(
    ancestor_sha: str,
    descendant_sha: str,
    repo: str = DEFAULT_REPO,
    timeout: int = REQUEST_TIMEOUT,
) -> bool:
    """Return True if ancestor_sha is an ancestor of descendant_sha in GitHub repo.

    Queries GitHub Compare API:
        GET /repos/{repo}/compare/{ancestor_sha}...{descendant_sha}

    Returns:
        True if descendant contains ancestor ('ahead' or 'identical').
        False if descendant does not contain ancestor ('behind' or 'diverged'),
        or on API error / missing SHA.

    """
    if not ancestor_sha or not descendant_sha:
        LOG.warning(
            "Missing commit SHA for comparison: ancestor=%s, descendant=%s",
            ancestor_sha,
            descendant_sha,
        )
        return False

    if ancestor_sha == descendant_sha:
        return True

    url = f"{GITHUB_API_URL}/repos/{repo}/compare/{ancestor_sha}...{descendant_sha}"
    try:
        resp = requests.get(url, headers=_request_headers(), timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status in ("ahead", "identical"):
            return True
        if status in ("behind", "diverged"):
            return False
        LOG.warning(
            "Unexpected GitHub compare status %r between %s and %s in %s",
            status,
            ancestor_sha,
            descendant_sha,
            repo,
        )
        return False
    except (requests.RequestException, ValueError) as e:
        LOG.warning(
            "GitHub compare API request failed for %s...%s in %s: %s",
            ancestor_sha,
            descendant_sha,
            repo,
            e,
        )
        return False
