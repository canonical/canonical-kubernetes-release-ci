#!/usr/bin/env python3

import argparse
import logging
import re
from typing import List

import util.k8s as k8s
from packaging.version import Version

LOG = logging.getLogger(__name__)


def get_outstanding_prereleases(as_git_branch: bool = False) -> List[str]:
    """Return outstanding K8s pre-releases.

    Args:
        as_git_branch: If True, return the git branch name for the pre-release.
    """
    latest_release = k8s.get_latest_releases_by_minor()
    prereleases = []
    for tag in latest_release.values():
        if not k8s.is_stable_release(tag):
            prereleases.append(tag)

    if as_git_branch:
        return [get_prerelease_git_branch(tag) for tag in prereleases]

    return prereleases


def get_obsolete_prereleases() -> List[str]:
    """Return obsolete K8s pre-releases.

    We only keep the latest pre-release(s) if there is no corresponding stable
    release. All previous pre-releases are discarded.
    """
    k8s_tags = k8s.get_k8s_tags()
    seen_stable_minors = set()
    obsolete = []

    for tag in k8s_tags:
        if k8s.is_stable_release(tag):
            version = Version(tag.lstrip("v"))
            seen_stable_minors.add((version.major, version.minor))
        else:
            version = Version(tag.lstrip("v").split("-")[0])
            if (version.major, version.minor) in seen_stable_minors:
                obsolete.append(tag)

    return obsolete


def get_prerelease_git_branch(prerelease: str):
    """Retrieve the name of the k8s-snap git branch for a given k8s pre-release."""
    prerelease_re = r"v\d+\.\d+\.\d-(?:alpha|beta|rc)\.\d+"
    if not re.match(prerelease_re, prerelease):
        raise ValueError("Unexpected k8s pre-release name: %s", prerelease)

    # Use a single branch for all pre-releases of a given risk level,
    # e.g. v1.33.0-alpha.0 -> autoupdate/v1.33.0-alpha
    branch = f"autoupdate/{prerelease}"
    return re.sub(r"(-[a-zA-Z]+)\.[0-9]+", r"\1", branch)


def remove_obsolete_prereleases():
    LOG.warning("TODO: not implemented.")


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s %(message)s", level=logging.DEBUG)

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subparser", required=True)

    cmd = subparsers.add_parser("get_prerelease_git_branch")
    cmd.add_argument(
        "--prerelease",
        dest="prerelease",
        help="The upstream k8s pre-release.",
    )

    cmd = subparsers.add_parser("get_outstanding_prereleases")
    cmd.add_argument(
        "--as-git-branch",
        dest="as_git_branch",
        help="If set, returns the git branch name of the pre-release instead of the tag.",
        action="store_true",
    )
    subparsers.add_parser("remove_obsolete_prereleases")

    kwargs = vars(parser.parse_args())
    f = locals()[kwargs.pop("subparser")]
    out = f(**kwargs)
    if isinstance(out, (list, tuple)):
        print(",".join(out))
    else:
        print(out or "")
