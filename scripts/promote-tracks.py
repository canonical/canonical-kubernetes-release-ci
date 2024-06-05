#!/usr/bin/env python3

import argparse
import logging
import sys
import datetime
import json
import subprocess
import urllib.request
import urllib.error

USAGE = "Promote revisions for Canonical Kubernetes tracks"

DESCRIPTION = """
Promote revisions of the Canonical Kubernetes snap through the risk levels of each track.
Expects snapcraft to be logged in with sufficient permissions, if not dry-running.
The script only targets releases. The 'latest' track is ignored.
Each revision is promoted after being at a risk level for a certain amount of days.
The script will only promote a revision to stable if there is already another revision for this track at stable.
The first stable release for each track requires blessing from SolQA and is promoted manually.
"""

SNAPSTORE_API = "https://api.snapcraft.io/v2/snaps/info/"
PROMOTE_API_URL = "https://dashboard.snapcraft.io/dev/api/snap-release"
SNAP_NAME = "k8s"
IGNORE_TRACKS = ["latest"]

# The snap risk levels, used to find the next risk level for a revision.
RISK_LEVELS = ["edge", "beta", "candidate", "stable"]

# Revisions stay at a certain risk level for some days before being promoted.
DAYS_TO_STAY_IN_RISK = {"edge": 1, "beta": 3, "candidate": 5}

# Headers for Snap Store API request
HEADERS = {
    "Snap-Device-Series": "16",
    "User-Agent": "Mozilla/5.0",
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("promote-tracks")


def get_snap_info(snap_name):
    req = urllib.request.Request(SNAPSTORE_API + snap_name, headers=HEADERS)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        log.exception("HTTPError ({%s}): {%s} {%s}", req.full_url, e.code, e.reason)
        sys.exit(1)
    except urllib.error.URLError as e:
        log.exception("URLError ({%s}): {%s}", req.full_url, e.reason)
        sys.exit(1)


def release_revision(revision, next_channel):
    # Note: we cannot use `snapcraft promote` here because it does not allow to promote from edge to beta without manual confirmation.
    subprocess.run(["snapcraft", "release", "k8s", str(revision), next_channel])


def check_and_promote(snap_info, dry_run: bool):
    channels = {c["channel"]["name"]: c for c in snap_info["channel-map"]}

    for channel_info in snap_info["channel-map"]:
        channel = channel_info["channel"]
        track = channel["track"]
        risk = channel["risk"]
        arch = channel["architecture"]
        next_risk = (
            RISK_LEVELS[RISK_LEVELS.index(risk) + 1]
            if RISK_LEVELS.index(risk) < len(RISK_LEVELS) - 1
            else None
        )
        revision = channel_info["revision"]

        if track in IGNORE_TRACKS or not next_risk:
            log.debug("Skipping track {%s}/{%s}", track, risk)
            continue

        now = datetime.datetime.now(datetime.timezone.utc)

        released_at = channel.get("released-at")
        if released_at:
            released_at_date = datetime.datetime.fromisoformat(released_at)
        else:
            released_at_date = None
        log.info("Evaluate %15s/%-10s rev=%s for arch=%s released at %s", track, risk, revision, arch, released_at_date.isoformat())

        if (
            released_at_date
            and (now - released_at_date).days > DAYS_TO_STAY_IN_RISK[risk]
            and channels.get(f"{track}/{risk}", {}).get("revision")
            != channels.get(f"{track}/{next_risk}", {}).get("revision")
        ):
            if next_risk == "stable" and not f"{track}/stable" in channels.keys():
                # The track has not yet a stable release.
                # The first stable release requires blessing from SolQA and needs to be promoted manually.
                # Follow-up patches do not require this.
                log.info(
                    "SolQA blessing required to promote first stable release for %s. Skipping...", track
                )
            else:
                log.info(
                    "Promoting revision %s from %s to %s for track %s", revision, risk, next_risk, track
                )
                if not dry_run:
                    release_revision(revision, f"{track}/{next_risk}")


def main():
    arg_parser = argparse.ArgumentParser(
        "promote-tracks.py", usage=USAGE, description=DESCRIPTION
    )
    arg_parser.add_argument("--dry-run", default=False, action="store_true")
    args = arg_parser.parse_args(sys.argv[1:])

    snap_info = get_snap_info(SNAP_NAME)
    check_and_promote(snap_info, args.dry_run)


if __name__ == "__main__":
    main()
