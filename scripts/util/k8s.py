"""Utility functions for interacting with Kubernetes release tags."""

import os
import re
from typing import Dict, Iterable, List

import requests
from packaging.version import InvalidVersion, Version
from tenacity import retry, stop_after_attempt, wait_exponential

# GitHub tags API is paginated (default 30 items/page). Requesting up to 100 reduces pages.
# To fetch all tags you need to follow the 'Link' header and request subsequent pages.
K8S_TAGS_URL = "https://api.github.com/repos/kubernetes/kubernetes/tags?per_page=100"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _fetch_page(url: str, headers: dict) -> requests.Response:
    """Fetch a single page from the GitHub API with retry logic.

    Args:
        url: The URL to fetch.
        headers: HTTP headers to include in the request.

    Returns:
        The response object.

    Raises:
        requests.exceptions.RequestException: If the request fails after retries.

    """
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp


def get_k8s_tags() -> Iterable[str]:
    """Retrieve semantically ordered Kubernetes release tags from GitHub, following pagination.

    Yields:
        Release tag strings sorted from newest to oldest.

    Raises:
        ValueError: If no tags are retrieved.

    """
    url = K8S_TAGS_URL
    tag_names: List[str] = []

    # Use GITHUB_TOKEN for authentication if available
    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    while url:
        resp = _fetch_page(url, headers)
        page = resp.json()
        if not page and not tag_names:
            raise ValueError("No k8s tags retrieved.")
        tag_names.extend([tag["name"] for tag in page])

        link = resp.headers.get("Link", "")
        if not link:
            break

        # parse Link header and follow 'next' if present
        parsed = requests.utils.parse_header_links(link.rstrip(","))
        next_url = None
        for item in parsed:
            if item.get("rel") == "next":
                next_url = item.get("url")
                break

        if next_url:
            url = next_url
        else:
            break

    # Sort tags using packaging.version for semantic versioning
    return sorted(tag_names, key=lambda x: Version(x), reverse=True)


def is_stable_release(release: str) -> bool:
    """Check if a Kubernetes release tag is stable (no pre-release suffix).

    Args:
        release: A Kubernetes release tag (e.g. 'v1.30.1', 'v1.30.0-alpha.1').

    Returns:
        True if the release is stable, False otherwise.

    """
    return "-" not in release


def get_latest_stable() -> str:
    """Get the latest stable Kubernetes release tag.

    Returns:
        The latest stable release tag string (e.g., 'v1.30.1').

    Raises:
        ValueError: If no stable release is found.

    """
    for tag in get_k8s_tags():
        if is_stable_release(tag):
            return tag
    raise ValueError("Couldn't find a stable release.")


def get_latest_releases_by_minor(after: Version | None = None) -> Dict[str, str]:
    """Map each minor Kubernetes version to its latest release tag.

    Args:
        after: The least supported track (inclusive).

    Returns:
        A dictionary mapping minor versions (e.g. '1.30') to the
        latest (pre-)release tag (e.g. 'v1.30.1').

    """
    latest_by_minor: Dict[str, str] = {}
    version_regex = re.compile(r"^v?(\d+)\.(\d+)\..+")

    for tag in get_k8s_tags():
        match = version_regex.match(tag)
        if not match:
            continue
        major, minor = match.groups()
        if after and Version(f"{major}.{minor}.0") <= after:
            continue
        key = f"{major}.{minor}"
        if key not in latest_by_minor:
            latest_by_minor[key] = tag

    return latest_by_minor


def get_all_releases_after(release) -> set[str]:
    """Get all releases after the input release.

    If the input release is invalid, the output will be empty.
    """
    releases: set[str] = set()
    try:
        least_version = Version(release)
    except InvalidVersion:
        raise ValueError(f"{release} is not a valid version")

    for tag in get_k8s_tags():
        if not is_stable_release(tag):
            continue
        try:
            version = Version(tag)
        except InvalidVersion:
            continue

        if version.major < least_version.major:
            continue
        elif version.major > least_version.major:
            releases.add(f"{version.major}.{version.minor}")
            continue
        elif version.minor >= least_version.minor:
            releases.add(f"{version.major}.{version.minor}")

    return set(releases)
