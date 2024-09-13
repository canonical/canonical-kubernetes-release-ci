#!/usr/bin/env python3

import argparse
import logging
import re
import semver
import sys
import util

from pathlib import Path

from lazr.restfulclient.errors import NotFound

USAGE = f"./{Path(__file__).name} [options]"

DESCRIPTION = """
Ensure snap channels and LP recipes for the specified branch.
"""

LP_REPO = "~cdk8s/k8s/+git/k8s-snap"
LP_OWNER = "containers"
LP_PROJECT = "https://launchpad.net/k8s"
SNAP_NAME = "k8s"
SNAP_REPO = "https://github.com/canonical/k8s-snap.git/"
SRC_BRANCH = re.compile(r"^(?:main)|^(?:release-\d+\.\d+)$")


def ensure_snap_channels(
    flavour: str, ver: semver.Version, tip: bool, dry_run: bool
) -> list[str]:
    """Ensure snap channels for the specified version."""
    channels = []
    if tip:
        channels += [f"latest/edge/{flavour}"]
        if flavour == "classic":
            channels += ["latest/edge"]
    else:
        name = f"{ver.major}.{ver.minor}"
        name += f"-{flavour}" if flavour != "strict" else ""
        channels += [f"{name}/edge"]

    LOG.info("Ensure snap channels %s for ver %s in snapstore", ",".join(channels), ver)
    if not dry_run:
        for channel in channels:
            util.ensure_track(SNAP_NAME, channel)
    return channels


def ensure_lp_recipe(
    flavour: str, ver: semver.Version, channels: list[str], tip: bool, dry_run: bool
) -> str:
    """Confirm LP Snap Recipe settings.

    * Ensure LP recipes are available in the snapstore.
    * Ensure LP recipes are building from correct branches.
    * Ensure LP recipes are pushing to the correct snap channels.
    """

    if tip:
        recipe_name = f"k8s-snap-tip-{flavour}"
    else:
        recipe_name = f"k8s-snap-{ver.major}.{ver.minor}-{flavour}"

    if tip:
        flavor_branch = "main" if flavour == "classic" else f"autoupdate/{flavour}"
    elif flavour == "classic":
        flavor_branch = f"release-{ver.major}.{ver.minor}"
    else:
        flavor_branch = f"autoupdate/release-{ver.major}.{ver.minor}-{flavour}"

    if tip:
        # Launchpad channels ignore the latest fields
        channels = [c[7:] for c in channels if c.startswith("latest/")]

    LOG.info(
        "Ensure LP recipe %s from %s pushes to %s",
        recipe_name,
        flavor_branch,
        ",".join(channels),
    )
    lp = util.lp_client()
    lp_project = lp.projects[SNAP_NAME]
    lp_owner = lp.people[LP_OWNER]
    lp_repo = lp.git_repositories.getDefaultRepository(target=lp_project)
    lp_ref = lp_repo.getRefByPath(path=flavor_branch)
    manifest = dict(
        auto_build=True,
        auto_build_pocket="Updates",
        auto_build_channels={"snapcraft": "8.x/stable"},
        description=f"Recipe for {SNAP_NAME} {flavor_branch}",
        git_ref=lp_ref,
        git_repository=lp_repo,
        information_type="Public",
        name=recipe_name,
        owner=lp_owner,
        store_channels=channels,
        store_upload=True,
    )
    try:
        recipe = lp.snaps.getByName(name=recipe_name, owner=lp_owner)
    except NotFound:
        LOG.info(" Creating LP recipe %s", recipe_name)
        (not dry_run) and lp.snaps.new(project=lp_project, **manifest)
    else:
        LOG.info(" Confirming LP recipe %s", recipe_name)
        updated = set()
        for key, value in manifest.items():
            lp_value = getattr(recipe, key)
            diff = lp_value != value
            updated |= {key} if diff else set()
            if diff:
                LOG.info("  Update %s: %s -> %s", key, lp_value, value)
                (not dry_run) and setattr(recipe, key, value)

        if updated and not dry_run:
            recipe.lp_save()

    return recipe_name


def prepare_track_builds(branch: str, args: argparse.Namespace):
    """Prepares all flavour branches to be built.

    * Ensure snap channels are available in the snapstore.
    * Ensure LP recipes are available in the snapstore.
    * Ensure LP recipes are building from correct branches.
    * Ensure LP recipes are pushing to the correct snap channels.
    """
    with util.git_repo(SNAP_REPO, branch) as dir:
        branch_ver = util.read_file(dir / "build-scripts/components/kubernetes/version")
        ver = semver.Version.parse(branch_ver.strip("v"))
        flavours = util.branch_flavours(dir)

        LOG.info("Current version detected %s", branch_ver)
        tip = branch == "main"
        for flavour in flavours:
            channels = ensure_snap_channels(flavour, ver, tip, args.dry_run)
            ensure_lp_recipe(flavour, ver, channels, tip, args.dry_run)


def setup_logging(args: argparse.Namespace):
    FORMAT = "%(name)20s %(asctime)s %(levelname)8s - %(message)s"
    logging.basicConfig(format=FORMAT)
    if args.loglevel:
        LOG.root.setLevel(level=args.loglevel.upper())


def main():
    arg_parser = argparse.ArgumentParser(
        Path(__file__).name, usage=USAGE, description=DESCRIPTION
    )
    arg_parser.add_argument("--branch", default="main", type=str, help="Tip branch ")
    arg_parser.add_argument(
        "--dry-run",
        default=False,
        help="Print what would be done without taking action",
        action="store_true",
    )
    arg_parser.add_argument(
        "-l",
        "--log",
        dest="loglevel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )
    args = arg_parser.parse_args()
    setup_logging(args)

    if not util.is_git_branch(SNAP_REPO, args.branch):
        LOG.error("Branch %s does not exist", args.branch)
        sys.exit(1)
    if not SRC_BRANCH.match(args.branch):
        LOG.warning(
            "Branch '%s' is not a supported branch r/%s/",
            args.branch,
            SRC_BRANCH.pattern,
        )
        sys.exit(1)
    prepare_track_builds(args.branch, args)


execd = __name__ == "__main__"
logger_name = Path(sys.argv[0]).stem if execd else __name__
LOG = logging.getLogger(logger_name)
if execd:
    main()
else:
    LOG.setLevel(logging.DEBUG)
