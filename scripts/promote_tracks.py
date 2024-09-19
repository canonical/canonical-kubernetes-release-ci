#!/usr/bin/env python3

import argparse
import datetime
import logging
import subprocess
import sys
from pathlib import Path

import util.snapstore as snapstore
import util.util as util

USAGE = "Promote revisions for Canonical Kubernetes tracks"

DESCRIPTION = """
Promote revisions of the Canonical Kubernetes snap through the risk levels of each track.
Expects snapcraft to be logged in with sufficient permissions, if not dry-running.
The script only targets releases. The 'latest' track is ignored.
Each revision is promoted after being at a risk level for a certain amount of days.
The script will only promote a revision to stable if there is already another revision for this track at stable.
The first stable release for each track requires blessing from SolQA and is promoted manually.
"""

IGNORE_TRACKS = ["latest"]

# The snap risk levels, used to find the next risk level for a revision.
RISK_LEVELS = ["edge", "beta", "candidate", "stable"]
NEXT_RISK = RISK_LEVELS[1:] + [None]

# Revisions stay at a certain risk level for some days before being promoted.
DAYS_TO_STAY_IN_RISK = {"edge": 1, "beta": 3, "candidate": 5}


def release_revision(revision, channel):
    # Note: we cannot use `snapcraft promote` here because it does not allow to promote from edge to beta without manual confirmation.
    subprocess.run(["/snap/bin/snapcraft", "release", "k8s", str(revision), channel])


def check_and_promote(snap_info, dry_run: bool):
    channels = {c["channel"]["name"]: c for c in snap_info["channel-map"]}

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

        if not next_risk:
            chan_log.debug("Skipping promoting stable")
            continue

        if track in IGNORE_TRACKS:
            chan_log.debug("Skipping ignored track")
            continue

        now = datetime.datetime.now(datetime.timezone.utc)

        if created_at := channel_info["created-at"]:
            created_at_date = datetime.datetime.fromisoformat(created_at)
        else:
            created_at_date = None
        chan_log.debug(
            "Evaluate rev=%-5s arch=%s created at %s",
            revision,
            arch,
            created_at,
        )

        if (
            created_at_date
            and (now - created_at_date).days >= DAYS_TO_STAY_IN_RISK[risk]
            and channels.get(f"{track}/{risk}", {}).get("revision")
            != channels.get(f"{track}/{next_risk}", {}).get("revision")
        ):
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
                    "Promotes rev=%-5s arch=%s to %s%s",
                    revision,
                    arch,
                    next_risk,
                    " (dry-run)" if dry_run else "",
                )
                (not dry_run) and release_revision(revision, f"{track}/{next_risk}")


def main():
    arg_parser = argparse.ArgumentParser(
        Path(__file__).name, usage=USAGE, description=DESCRIPTION
    )
    args = util.setup_arguments(arg_parser)

    snap_info = snapstore.info(util.SNAP_NAME)
    check_and_promote(snap_info, args.dry_run)


is_main = __name__ == "__main__"
logger_name = Path(sys.argv[0]).stem if is_main else __name__
LOG = logging.getLogger(logger_name)
if is_main:
    main()
else:
    LOG.setLevel(logging.DEBUG)
