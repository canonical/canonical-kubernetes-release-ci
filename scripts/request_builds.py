#!/usr/bin/env python3

import argparse
import logging
import sys
from pathlib import Path
from typing import Generator, Iterable

import semver
import util.lp as lp
import util.repo as repo
import util.util as util

USAGE = f"./{Path(__file__).name} [options]"

DESCRIPTION = """Request all builds with launchpad recipes"""


def rebuild_branches(branches: Iterable[str], args: argparse.Namespace):
    """Prepares all flavour branches to be built.

    * Ensure snap channels are available in the snapstore.
    * Ensure LP recipes are available in the snapstore.
    * Ensure LP recipes are building from correct branches.
    * Ensure LP recipes are pushing to the correct snap channels.
    """
    client = lp.client()
    owner = client.people[util.LP_OWNER]
    for branch in branches:
        LOG.info("Cloning tip branch %s", branch)
        with repo.clone(util.SNAP_REPO, branch) as dir:
            version_file = dir / "build-scripts/components/kubernetes/version"
            branch_ver = version_file.read_text().strip()
            ver = semver.Version.parse(branch_ver.strip("v"))
            flavors = util.flavors(dir)

            LOG.info("  Kubernetes version detected %s", branch_ver)
            tip = branch == "main"

            flavors = util.flavors(dir)
            for flavor in flavors:
                recipe_name = util.recipe_name(flavor, ver, tip)
                LOG.info("  Searching for recipe %s", recipe_name)
                if recipe := client.snaps.getByName(owner=owner, name=recipe_name):
                    archive = recipe.auto_build_archive
                    channels = recipe.auto_build_channels
                    pocket = recipe.auto_build_pocket

                    dry_msg = " (dry-run)" if args.dry_run else ""
                    LOG.info("  Requesting build for %s%s", recipe_name, dry_msg)
                    (not args.dry_run) and recipe.requestBuilds(
                        archive=archive, channels=channels, pocket=pocket
                    )


def tip_branches(branches: Iterable[str]) -> Generator[None, str, None]:
    for branch in branches:
        if not util.TIP_BRANCH.match(branch):
            LOG.warning(
                "Skipping branch '%s' - not a tip branch r/%s/",
                branch,
                util.TIP_BRANCH.pattern,
            )
            continue
        if not repo.is_branch(util.SNAP_REPO, branch):
            LOG.error("Branch %s does not exist", branch)
            continue
        yield branch


def main():
    arg_parser = argparse.ArgumentParser(
        Path(__file__).name, usage=USAGE, description=DESCRIPTION
    )
    arg_parser.add_argument(
        "--branches", nargs="*", type=str, help="Specific branches to confirm"
    )
    args = util.setup_arguments(arg_parser)
    branches = args.branches

    if not branches:
        branches = repo.ls_branches(util.SNAP_REPO)
        LOG.info("No branches specified, checking all branches")
    rebuild_branches(tip_branches(branches), args)


is_main = __name__ == "__main__"
logger_name = Path(sys.argv[0]).stem if is_main else __name__
LOG = logging.getLogger(logger_name)
if is_main:
    main()
else:
    LOG.setLevel(logging.DEBUG)
