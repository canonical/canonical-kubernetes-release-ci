"""Utility functions for interacting with Launchpad and Snap recipes."""

import itertools
import logging
import os
from configparser import ConfigParser
from functools import cache

from launchpadlib.launchpad import Launchpad, PersonSet
from lazr.restfulclient.errors import HTTPError, NotFound

OWNER: str = "containers"
FAILED_BUILD_STATES: frozenset[str] = frozenset({"Failed to build", "Chroot problem"})
PENDING_BUILD_STATES: frozenset[str] = frozenset(
    {"Needs building", "Currently building", "Uploading build"}
)
PENDING_UPLOAD_STATES: frozenset[str] = frozenset({"Pending", "Failed to upload"})
MAX_RECENT_BUILDS: int = 20


@cache
def client():
    """Use launchpad credentials to interact with launchpad."""
    cred_file = os.getenv("LPCREDS")
    creds_local = os.getenv("LPLOCAL")
    anon_login = os.getenv("LPANON", "").lower() in ("1", "true")
    if cred_file:
        parser = ConfigParser()
        parser.read(cred_file)
        return Launchpad.login_with(
            application_name=parser["1"]["consumer_key"],
            service_root="production",
            version="devel",
            credentials_file=cred_file,
        )
    elif creds_local:
        return Launchpad.login_with(
            "localhost",
            "production",
            version="devel",
        )
    elif anon_login:
        # Anonymous login, can be used for dry-runs.
        return Launchpad.login_anonymously(
            "Canonical K8s promotion dry-run", "production", version="devel"
        )
    else:
        raise ValueError("No launchpad credentials found")


def snap_recipe(owner: PersonSet, name: str):
    """Return the recipe object for a given owner and name."""
    lp_client = client()
    try:
        return lp_client.snaps.getByName(owner=owner, name=name)
    except NotFound:
        LOG.info("    Recipe %s not found for owner %s", name, owner.name)
        return None


def snap_by_owner(snap: str):
    """Return the owner object for a given owner name."""
    lp_client = client()
    return lp_client.snaps.findByStoreName(owner=lp_client.people[OWNER], store_name=snap)


def recipe_for_track(snap: str, track: str):
    """Return the Launchpad snap recipe publishing to the given track, if any."""
    for recipe in snap_by_owner(snap):
        if any(chan.split("/")[0] == track for chan in recipe.store_channels):
            return recipe
    return None


def branch_from_track(snap, track):
    """Return the branch name for a given track."""
    if recipe := recipe_for_track(snap, track):
        return recipe.git_ref_link.split("+ref/")[1]
    return None


def retry_failed_builds(recipe, arch: str | None = None, dry_run: bool = False) -> list[str]:
    """Retry architectures whose most recent Launchpad build failed."""
    retried: list[str] = []
    seen_arches: set[str] = set()
    for build in itertools.islice(recipe.builds, MAX_RECENT_BUILDS):
        build_arch = getattr(build, "arch_tag", None)
        if not build_arch or (arch is not None and build_arch != arch):
            continue
        if build_arch in seen_arches:
            continue
        seen_arches.add(build_arch)
        if build.buildstate in FAILED_BUILD_STATES and getattr(build, "can_be_retried", False):
            LOG.info(
                "Retrying failed %s build for %s%s",
                build_arch,
                getattr(recipe, "name", "recipe"),
                " (dry-run)" if dry_run else "",
            )
            if not dry_run:
                try:
                    build.retry()
                except HTTPError as e:
                    LOG.warning("Failed to retry %s build: %s", build_arch, e)
                    if arch is not None:
                        break
                    continue
            retried.append(build_arch)
        if arch is not None:
            break
    return retried


def edge_is_stale(recipe, arch: str, edge_revision: int | None) -> bool:
    """Return True if git_ref is ahead of edge_revision or a newer build failed/is pending."""
    if edge_revision is None:
        return False
    git_ref = getattr(recipe, "git_ref", None)
    head_sha = getattr(git_ref, "commit_sha1", None)

    latest_build = None
    edge_build = None
    for build in itertools.islice(recipe.builds, MAX_RECENT_BUILDS):
        if getattr(build, "arch_tag", None) != arch:
            continue
        if latest_build is None:
            latest_build = build
        if edge_build is None and getattr(build, "store_upload_revision", None) == edge_revision:
            edge_build = build
        if latest_build is not None and edge_build is not None:
            break

    if (
        latest_build is not None
        and getattr(latest_build, "store_upload_revision", None) != edge_revision
    ):
        if latest_build.buildstate in (FAILED_BUILD_STATES | PENDING_BUILD_STATES):
            return True
        if getattr(latest_build, "store_upload_status", None) in PENDING_UPLOAD_STATES:
            return True

    if head_sha and edge_build is not None:
        edge_sha = getattr(edge_build, "revision_id", None)
        if edge_sha and edge_sha != head_sha:
            return True

    return False


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)
