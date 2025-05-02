import json
import re
from typing import Dict, List

import requests
from packaging.version import Version

K8S_TAGS_URL = "https://api.github.com/repos/kubernetes/kubernetes/tags"


def _url_get(url: str) -> str:
    """Make a GET request to the given URL and return the response text."""
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.text


def is_stable_release(release: str) -> bool:
    """Check if a Kubernetes release tag is stable (no pre-release suffix).

    Args:
        release: A Kubernetes release tag (e.g. 'v1.30.1', 'v1.30.0-alpha.1').

    Returns:
        True if the release is stable, False otherwise.
    """
    return "-" not in release


def get_k8s_tags() -> List[str]:
    """Retrieve semantically ordered Kubernetes release tags from GitHub.

    Returns:
        A list of release tag strings sorted from newest to oldest.

    Raises:
        ValueError: If no tags are retrieved.
    """
    response = _url_get(K8S_TAGS_URL)
    tags_json = json.loads(response)
    if not tags_json:
        raise ValueError("No k8s tags retrieved.")
    tag_names = [tag["name"] for tag in tags_json]
    tag_names.sort(key=lambda x: Version(x), reverse=True)
    return tag_names


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


def get_latest_releases_by_minor() -> Dict[str, str]:
    """Map each minor Kubernetes version to its latest release tag.

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
        key = f"{major}.{minor}"
        if key not in latest_by_minor:
            latest_by_minor[key] = tag

    return latest_by_minor


def get_all_releases_after(release) -> set[str]:
    """
    Get all releases after the input release.
    If the input release is invalid, the output will be empty.
    """
    releases: set[str] = set()
    version_regex = re.compile(r"^v?(\d+)\.(\d+)\..+")

    match = re.match(r"^(\d+)\.(\d+)$", release)
    if not match:
        return releases
    least_major, least_minor = match.groups()

    for tag in get_k8s_tags():
        if not is_stable_release(tag):
            continue
        match = version_regex.match(tag)
        if not match:
            continue
        major, minor = match.groups()

        if major < least_major:
            continue
        elif major > least_major:
            releases.add(f"{major}.{minor}")
            continue
        elif minor >= least_minor:
            releases.add(f"{major}.{minor}")

    return set(releases)
