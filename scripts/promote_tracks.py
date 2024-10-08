#!/usr/bin/env python3

import argparse
import dataclasses
import datetime
import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import Optional

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
DAYS_TO_STAY_IN_EDGE = 1
DAYS_TO_STAY_IN_BETA = 3
DAYS_TO_STAY_IN_CANDIDATE = 5

# Path to the tox executable.
TOX_PATH = (venv := os.getenv("VIRTUAL_ENV")) and Path(venv) / "bin/tox" or "tox"

TRACK_RE = re.compile(r"^(\d+)\.(\d+)(\S*)$")


class Hyphenized:
    @classmethod
    def bake(cls, *args, **kwargs):
        return cls(*args, **{s.replace("-", "_").lower(): v for s, v in kwargs.items()})


@dataclasses.dataclass
class ChannelMetadata(Hyphenized):
    name: Optional[str] = None
    track: Optional[str] = None
    risk: Optional[str] = None
    architecture: Optional[str] = None
    released_at: Optional[str] = None


@dataclasses.dataclass
class Channel(Hyphenized):
    channel: ChannelMetadata
    created_at: Optional[str] = None
    revision: Optional[int] = None
    version: Optional[str] = None
    type: Optional[str] = None
    download: Optional[dict] = dataclasses.field(default_factory=dict)

    @cached_property
    def next_risk(self):
        return NEXT_RISK[RISK_LEVELS.index(self.risk)]

    def __getattr__(self, name):
        return getattr(self.channel, name)


EMPTY_CHANNEL = Channel(channel=ChannelMetadata())


def _build_upgrade_channels(
    channel: Channel, channels: dict[str, Channel]
) -> list[list[str]]:
    """Build the upgrade channels for a proposal within this architecture

    At most, there will be three validation tests:
    - Upgrade from the next risk to this risk within the channel
      (simulates a snap refresh)
    - Upgrade from the highest risk in this track to this risk
      (confirms this can replace the highest risk'd snap)
    - Upgrade from the highest risk in prior track to this risk
      (confirms this can replace the highest prior risk'd snap)

    If the starting revision doesn't exist, the test is skipped.

    Args:
        channel:  The current snap revision to promote
        channels: All channels of the snap with the same arch

    Returns:
        A valid list of upgrade proposal stages.
    """

    track = channel.track
    next_risk = channel.next_risk

    # The next risk on this track
    next_channel = f"{track}/{next_risk}"
    source_channels = set()
    if next_channel in channels:
        source_channels |= {next_channel}

    # First highest risk on this track (excluding next-risk)
    same_track_channels = [
        f"{track}/{r}"
        for idx, r in enumerate(RISK_LEVELS)
        if idx > RISK_LEVELS.index(next_risk)
    ]
    for source in reversed(same_track_channels):
        if source in channels:
            source_channels |= {source}
            break

    # First highest risk on the previous track
    if match := TRACK_RE.match(track):
        maj, min, tail = match.groups()
    else:
        raise ValueError(f"Invalid track name: {track}")
    prior_track = f"{maj}.{int(min)-1}{tail}"
    prior_track_channels = [f"{prior_track}/{r}" for r in RISK_LEVELS]
    for source in reversed(prior_track_channels):
        if source in channels:
            source_channels |= {source}
            break

    # Only run tests on revision changes
    return [
        [source, channel.name]
        for source in sorted(source_channels)
        if channel.revision != channels[source].revision
    ]


def _create_channel_map():
    snap_info = snapstore.info(util.SNAP_NAME)
    channel_map: dict[str, dict[str, Channel]] = defaultdict(dict)

    for c in snap_info["channel-map"]:
        channel_data = ChannelMetadata.bake(**c.pop("channel"))
        revision_data = Channel.bake(channel=channel_data, **c)
        channel_map[channel_data.architecture][channel_data.name] = revision_data

    return channel_map


def create_proposal(args):
    channel_map = _create_channel_map()
    proposals = []

    for arch, channels in channel_map.items():
        proposals.extend(_create_arch_proposals(arch, channels, args))

    if args.gh_action:
        core.set_output("proposals", json.dumps(proposals))
    return proposals


def _create_arch_proposals(arch, channels: dict[str, Channel], args):
    proposals = []
    ignored_tracks = IGNORE_TRACKS + getattr(args, "ignore_tracks", [])
    ignored_arches = getattr(args, "ignore_arches", [])
    days_to_stay_in_risk = {
        "edge": args.days_in_edge_risk,
        "beta": args.days_in_beta_risk,
        "candidate": args.days_in_candidate_risk,
    }

    def sorter(info: Channel):
        return (info.name, RISK_LEVELS.index(info.risk))

    for channel_info in sorted(channels.values(), key=sorter, reverse=True):
        track = channel_info.channel.track
        risk = channel_info.risk
        next_risk = channel_info.next_risk
        revision = channel_info.revision
        chan_log = logging.getLogger(f"{logger_name} {track:>15}/{risk:<9}")

        final_channel = f"{track}/{next_risk}"

        if not next_risk:
            chan_log.debug("Skipping promoting stable")
            continue

        if track in ignored_tracks:
            chan_log.debug("Skipping ignored track")
            continue

        if arch in ignored_arches:
            chan_log.debug("Skipping ignored architecture")
            continue

        now = datetime.datetime.now(datetime.timezone.utc)

        if released_at := channel_info.channel.released_at:
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
            and (now - released_at_date).days >= days_to_stay_in_risk[risk]
            and channels.get(f"{track}/{risk}", EMPTY_CHANNEL).revision
            != channels.get(f"{track}/{next_risk}", EMPTY_CHANNEL).revision
        )
        new_patch_in_edge = (
            risk == "edge"
            and channels.get(f"{track}/{next_risk}", EMPTY_CHANNEL).version
            != channels.get(f"{track}/{risk}", EMPTY_CHANNEL).version
        )

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
                proposal["upgrade-channels"] = _build_upgrade_channels(
                    channel_info, channels
                )
                proposal["revision"] = revision
                proposal["snap-channel"] = final_channel
                proposal["name"] = f"{util.SNAP_NAME}-{track}-{next_risk}-{arch}"
                proposal["runner-labels"] = gh.arch_to_gh_labels(arch, self_hosted=True)
                proposal["lxd-images"] = [f"ubuntu:{series}" for series in SERIES]
                proposals.append(proposal)
    return proposals


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
    propose_args.add_argument(
        "--days-in-edge-risk",
        type=int,
        help="The number of days a revision stays in edge risk",
        default=DAYS_TO_STAY_IN_EDGE,
    )
    propose_args.add_argument(
        "--days-in-beta-risk",
        type=int,
        help="The number of days a revision stays in beta risk",
        default=DAYS_TO_STAY_IN_BETA,
    )
    propose_args.add_argument(
        "--days-in-candidate-risk",
        type=int,
        help="The number of days a revision stays in candidate risk",
        default=DAYS_TO_STAY_IN_CANDIDATE,
    )
    propose_args.add_argument(
        "--ignore-tracks",
        nargs="+",
        help="Tracks to ignore when proposing revisions",
        default=[],
    )
    propose_args.add_argument(
        "--ignore-arches",
        nargs="+",
        help="Architectures to ignore when proposing revisions",
        default=[],
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
