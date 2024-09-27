#!/usr/bin/env python3

import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import util.gh as gh
import util.lp as lp
import util.repo as repo
import util.snapstore as snapstore
import util.util as util
from actions_toolkit import core

USAGE = "./promote_tracks.py"

DESCRIPTION = """
Promote revisions of the Canonical Kubernetes snap through the risk levels of each track.
Expects snapcraft to be logged in with sufficient permissions, if not dry-running.
The script only targets releases. The 'latest' track is ignored.
Each revision is promoted after being at a risk level for a certain amount of days.
The script will only promote a revision to stable if there is already another revision for this track at stable.
The first stable release for each track requires blessing from SolQA and is promoted manually.
"""

SERIES = ["20.04", "22.04", "24.04"]

IGNORE_TRACKS = ["latest"]

# The snap risk levels, used to find the next risk level for a revision.
RISK_LEVELS = ["edge", "beta", "candidate", "stable"]
NEXT_RISK = RISK_LEVELS[1:] + [None]

# Revisions stay at a certain risk level for some days before being promoted.
DAYS_TO_STAY_IN_RISK = {"edge": 1, "beta": 3, "candidate": 5}

# Path to the tox executable.
TOX_PATH = (venv := os.getenv("VIRTUAL_ENV")) and Path(venv) / "bin/tox" or "tox"


def release_revision(args):
    # Note: we cannot use `snapcraft promote` here because it does not allow to promote from edge to beta without manual confirmation.
    revision, channel = args.snap_revision, args.snap_channel
    LOG.info(
        "Promote r%s to %s%s", revision, channel, args.dry_run and " (dry-run)" or ""
    )
    args.dry_run or subprocess.run(
        ["/snap/bin/snapcraft", "release", util.SNAP_NAME, revision, channel]
    )


def execute_proposal_test(args):
    branches = {args.branch, "main"}  # branch choices
    cmd = f"{TOX_PATH} -e integration -- -k test_version_upgrades"

    for branch in branches:
        with repo.clone(util.SNAP_REPO, branch) as dir:
            if repo.ls_tree(dir, "tests/integration/tests/test_version_upgrades.py"):
                LOG.info("Running integration tests for %s", branch)
                subprocess.run(cmd.split(), cwd=dir / "tests/integration", check=True)
                return


def create_proposal(args):
    snap_info = snapstore.info(util.SNAP_NAME)
    channels = {c["channel"]["name"]: c for c in snap_info["channel-map"]}
    proposals = []

    def sorter(info):
        return (info["channel"]["track"], RISK_LEVELS.index(info["channel"]["risk"]))

    for channel_info in sorted(snap_info["channel-map"], key=sorter, reverse=True):
        channel = channel_info["channel"]
        track = channel["track"]
        risk = channel["risk"]
        arch = channel["architecture"]
        next_risk = NEXT_RISK[RISK_LEVELS.index(risk)]
        revision = channel_info["revision"]
        chan_log = logging.getLogger(f"{logger_name} {track:>15}/{risk:<9}")

        start_channel = f"{track}/{risk}"
        final_channel = f"{track}/{next_risk}"

        if not next_risk:
            chan_log.debug("Skipping promoting stable")
            continue

        if track in IGNORE_TRACKS:
            chan_log.debug("Skipping ignored track")
            continue

        now = datetime.datetime.now(datetime.timezone.utc)

        if released_at := channel.get("released-at"):
            released_at_date = datetime.datetime.fromisoformat(released_at)
        else:
            released_at_date = None

        chan_log.debug(
            "Evaluate rev=%-5s arch=%s released at %s",
            revision,
            arch,
            released_at_date,
        )

        purgatory_complete = (
            released_at_date
            and (now - released_at_date).days >= DAYS_TO_STAY_IN_RISK[risk]
            and channels.get(f"{track}/{risk}", {}).get("revision")
            != channels.get(f"{track}/{next_risk}", {}).get("revision")
        )
        new_patch_in_edge = risk == "edge" and channels.get(
            f"{track}/{next_risk}", {}
        ).get("version") != channels.get(f"{track}/{risk}", {}).get("version")

        if purgatory_complete or new_patch_in_edge:
            if next_risk == "stable" and f"{track}/stable" not in channels.keys():
                # The track has not yet a stable release.
                # The first stable release requires blessing from SolQA and needs to be promoted manually.
                # Follow-up patches do not require this.
                chan_log.warning(
                    "Approval rev=%-5s arch=%s to %s needed by SolQA",
                    revision,
                    arch,
                    next_risk,
                )
            else:
                chan_log.info(
                    "Promotes rev=%-5s arch=%s to %s",
                    revision,
                    arch,
                    next_risk,
                )
                proposal = {}
                proposal["branch"] = lp.branch_from_track(util.SNAP_NAME, track)
                proposal["upgrade-channels"] = [[final_channel, start_channel]]
                proposal["revision"] = revision
                proposal["snap-channel"] = final_channel
                proposal["name"] = f"{util.SNAP_NAME}-{track}-{next_risk}-{arch}"
                proposal["runner-labels"] = gh.arch_to_gh_labels(arch, self_hosted=True)
                proposal["lxd-images"] = [f"ubuntu:{series}" for series in SERIES]
                proposals.append(proposal)
    if args.gh_action:
        core.set_output("proposals", json.dumps(proposals))
    return proposals


def main():
    arg_parser = argparse.ArgumentParser(
        Path(__file__).name, usage=USAGE, description=DESCRIPTION
    )
    subparsers = arg_parser.add_subparsers(required=True)
    propose_args = subparsers.add_parser(
        "propose", help="Propose revisions for promotion"
    )
    propose_args.add_argument(
        "--gh-action",
        action="store_true",
        help="Output the proposals to be used in a GitHub Action",
    )
    propose_args.set_defaults(func=create_proposal)

    test_args = subparsers.add_parser("test", help="Run the test for a proposal")
    test_args.add_argument(
        "--branch", required=True, help="The branch from which to test"
    )
    test_args.set_defaults(func=execute_proposal_test)

    promote_args = subparsers.add_parser(
        "promote", help="Promote the proposed revisions"
    )
    promote_args.add_argument(
        "--snap-revision",
        required=True,
        help="The snap revision to promote",
        dest="snap_revision",
    )
    promote_args.add_argument(
        "--snap-channel",
        required=True,
        help="The snap channel to promote to",
        dest="snap_channel",
    )
    promote_args.set_defaults(func=release_revision)

    args = util.setup_arguments(arg_parser)
    args.func(args)


is_main = __name__ == "__main__"
logger_name = Path(sys.argv[0]).stem if is_main else __name__
LOG = logging.getLogger(logger_name)
if is_main:
    main()
else:
    LOG.setLevel(logging.DEBUG)
