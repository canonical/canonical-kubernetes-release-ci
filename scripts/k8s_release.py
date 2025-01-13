#!/usr/bin/env python3

import argparse
import json
import logging
import re
from typing import List, Optional

import requests
import util.util as util
from packaging.version import Version

K8S_TAGS_URL = "https://api.github.com/repos/kubernetes/kubernetes/tags"

LOG = logging.getLogger(__name__)


def _url_get(url: str) -> str:
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    return r.text


def get_k8s_tags() -> List[str]:
    """Retrieve semantically ordered k8s releases, newest to oldest."""
    response = _url_get(K8S_TAGS_URL)
    tags_json = json.loads(response)
    if len(tags_json) == 0:
        raise ValueError("No k8s tags retrieved.")
    tag_names = [tag["name"] for tag in tags_json]
    # Github already sorts the tags semantically but let's not rely on that.
    tag_names.sort(key=lambda x: Version(x), reverse=True)
    return tag_names


# k8s release naming:
# * alpha:  v{major}.{minor}.{patch}-alpha.{version}
# * beta:   v{major}.{minor}.{patch}-beta.{version}
# * rc:     v{major}.{minor}.{patch}-rc.{version}
# * stable: v{major}.{minor}.{patch}
def is_stable_release(release: str):
    return "-" not in release


def get_latest_stable() -> str:
    k8s_tags = get_k8s_tags()
    for tag in k8s_tags:
        if is_stable_release(tag):
            return tag
    raise ValueError("Couldn't find stable release, received tags: %s" % k8s_tags)


def get_latest_release() -> str:
    k8s_tags = get_k8s_tags()
    return k8s_tags[0]


def get_outstanding_prerelease() -> Optional[str]:
    latest_release = get_latest_release()
    if not is_stable_release(latest_release):
        return latest_release
    # The latest release is a stable release, no outstanding pre-release.
    return None


def get_obsolete_prereleases() -> List[str]:
    """Return obsolete K8s pre-releases.

    We only keep the latest pre-release if there is no corresponding stable
    release. All previous pre-releases are discarded.
    """
    k8s_tags = get_k8s_tags()
    if not is_stable_release(k8s_tags[0]):
        # Valid pre-release
        k8s_tags = k8s_tags[1:]
    # Discard all other pre-releases.
    return [tag for tag in k8s_tags if not is_stable_release(tag)]


def _branch_exists(
    branch_name: str, remote=True, project_basedir: Optional[str] = None
):
    cmd = ["git", "branch"]
    if remote:
        cmd += ["-r"]

    stdout, stderr = util.execute(cmd, cwd=project_basedir)
    return branch_name in stdout


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

    subparsers.add_parser("get_outstanding_prerelease")
    subparsers.add_parser("remove_obsolete_prereleases")

    kwargs = vars(parser.parse_args())
    f = locals()[kwargs.pop("subparser")]
    out = f(**kwargs)
    if isinstance(out, (list, tuple)):
        for item in out:
            print(item)
    else:
        print(out or "")
