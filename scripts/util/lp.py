"""Utility functions for interacting with Launchpad and Snap recipes."""

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


def _recipe_arches(recipe) -> set[str]:
    processors = getattr(recipe, "processors", None)
    if processors is None:
        return set()
    try:
        return {name for p in processors if isinstance((name := getattr(p, "name", None)), str)}
    except TypeError:
        return set()


def retry_failed_builds(recipe, arch: str | None = None, dry_run: bool = False) -> list[str]:
    """Retry architectures whose most recent Launchpad build or store upload failed."""
    retried: list[str] = []
    seen_arches: set[str] = set()
    target_arches: set[str] | None = (
        {arch} if arch is not None else (_recipe_arches(recipe) or None)
    )

    for build in recipe.builds:
        build_arch = getattr(build, "arch_tag", None)
        if not build_arch or (arch is not None and build_arch != arch):
            continue
        if build_arch in seen_arches:
            continue
        seen_arches.add(build_arch)

        recipe_name = getattr(recipe, "name", "recipe")
        dry_suffix = " (dry-run)" if dry_run else ""
        if build.buildstate in FAILED_BUILD_STATES and getattr(build, "can_be_retried", False):
            LOG.info("Retrying failed %s build for %s%s", build_arch, recipe_name, dry_suffix)
            if not dry_run:
                try:
                    build.retry()
                except HTTPError as e:
                    LOG.warning("Failed to retry %s build: %s", build_arch, e)
                else:
                    retried.append(build_arch)
            else:
                retried.append(build_arch)
        elif (
            build.buildstate == "Successfully built"
            and getattr(build, "store_upload_status", None) == "Failed to upload"
        ):
            LOG.info(
                "Retrying failed %s store upload for %s%s", build_arch, recipe_name, dry_suffix
            )
            if not dry_run:
                try:
                    build.scheduleStoreUpload()
                except HTTPError as e:
                    LOG.warning("Failed to schedule %s store upload: %s", build_arch, e)
                else:
                    retried.append(build_arch)
            else:
                retried.append(build_arch)

        if target_arches is not None and seen_arches >= target_arches:
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
    for build in recipe.builds:
        if getattr(build, "arch_tag", None) != arch:
            continue
        if latest_build is None:
            latest_build = build
            if getattr(latest_build, "store_upload_revision", None) != edge_revision:
                if latest_build.buildstate in (FAILED_BUILD_STATES | PENDING_BUILD_STATES):
                    return True
                if getattr(latest_build, "store_upload_status", None) in PENDING_UPLOAD_STATES:
                    return True
            if not head_sha:
                return False
        if edge_build is None and getattr(build, "store_upload_revision", None) == edge_revision:
            edge_build = build
        if latest_build is not None and edge_build is not None:
            break

    if head_sha:
        if edge_build is None:
            return True
        edge_sha = getattr(edge_build, "revision_id", None)
        return not edge_sha or edge_sha != head_sha

    return False


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)
