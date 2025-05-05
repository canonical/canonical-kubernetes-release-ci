"""
Script to automate the k8s-operator charms release process.

The implementation works as a state machine that queries the current state of each track
and then decides what to do next.The script is designed to be idempotent, meaning that it
can be run multiple times without causing any harm.

For each track to be published on stable from candidate, there are more than one revisions 
that need to be tested, each corresponding to a unique (arch, base) for which a revision has
been published. We call the set of all such revisions a revision matrix of a track. For example 
track 1.32 can have the following matrix of revisions on candidate risk level:

        20.04   22.04   24.04
amd64   741     742     743
arm64   736     748     750

And the following matrix of revisions published on stable:

        20.04   22.04   24.04
amd64   456     457     458
arm64   459     460     461

The goal is to promote all 6 revisions of the 1.32/candidate to 1.32/stable

for each track:
    get data for current track state:
        - extract all the revisions corresponding to each (arch, base) published on channel=<track>/candidate
        - extract all the revisions corresponding to each (arch, base) published on stable_channel=<track>/stable
        - skip if the revisions on <track>/candidate are already published in <track>/stable
        - for each (arch, base) revision on channel=<track>/candidate try the following reconcililation pattern:

    revision is in one of the following states:
        - no TPIs yet -> NO_TEST
        - at least one TPI succeeded -> TEST_SUCCESS
        - at least one TPI in progress -> TEST_IN_PROGRESS
        - there are only failed/(in-)error TPIs -> TEST_FAILED

    Actions:
        - NO_TEST: start a new TPI
        - TEST_IN_PROGRESS: just print that a log message
        - TEST_SUCCESS: promote the charm revisions to the next channel
        - TEST_FAILED: manual intervention with SQA required

    aggregate the results for all the revisions checked for each track:
        - If all revisions have TEST_SUCCESS then report the track state as succeeded 
        - If any of the revisions have TEST_FAILED then report the track state as failed 
        - If some of the tests are still in TEST_IN_PROGRESS report the track as in progress

TODOs:
* Support testing different architectures once SQA provides the feature
* Cleaning up outdated and aborted TPIs in a separate cronjob
"""

import argparse
from enum import StrEnum, auto
from typing import Dict

from requests.exceptions import HTTPError
from util import charmhub, k8s, sqa


class TrackState:
    def __init__(self):
        self._state_map: Dict[str, sqa.TestPlanInstanceStatus] = {}

    def set_state(self, revision, state: sqa.TestPlanInstanceStatus):
        if not isinstance(state, sqa.TestPlanInstanceStatus):
            raise ValueError("State must be an instance of TestPlanInstanceStatus")
        self._state_map[revision] = state

    def __str__(self):
        return str([(key, str(value)) for key, value in self._state_map.items()])

    @property
    def failed(self) -> bool:
        return any(s.failed for s in self._state_map.values())

    @property
    def succeeded(self) -> bool:
        return all(s.succeeded for s in self._state_map.values())

    @property
    def in_progress(self) -> bool:
        if self.failed:
            return False
        return any(s.in_progress for s in self._state_map.values())


class ProcessState(StrEnum):
    PROCESS_SUCCESS = auto()
    PROCESS_IN_PROGRESS = auto()
    PROCESS_FAILED = auto()
    PROCESS_CI_FAILED = auto()
    PROCESS_UNCHANGED = auto()


def ensure_track_state(
    charm_name, channel, revision_matrix: charmhub.RevisionMatrix, dry_run: bool
) -> TrackState:
    for arch in revision_matrix.get_archs():
        # Note(Reza): Currently SQA only supports the test for the amd64 architecture
        # we should differentiate the TPIs for different architectures once arm64 is
        # also supported. I have not put that in a file to avoid creating a perception 
        # that more than one architecture could be tested. Having more than one arch
        # would break the pipeline by creating duplicates as there are no ways to 
        # distinguish test environments for architectures on SQA side. 
        if arch != "amd64":
            continue

        track_state = TrackState()
        for base in revision_matrix.get_bases():
            revision = revision_matrix.get(arch, base)
            if not revision:
                continue

            current_test_plan_instance_status = sqa.current_test_plan_instance_status(
                charm_name, channel, revision
            )
            if not current_test_plan_instance_status:
                if not dry_run:
                    sqa.start_release_test(charm_name, channel, revision)
                track_state.set_state(revision, sqa.TestPlanInstanceStatus.IN_PROGRESS)
                continue

            track_state.set_state(revision, current_test_plan_instance_status)

    return track_state


def process_track(track: str, dry_run: bool) -> ProcessState:
    """Process the given track based on its current state."""

    candidate_channel = f"{track}/candidate"
    stable_channel = f"{track}/stable"

    try:
        candidate_revision_matrix = charmhub.get_revision_matrix(
            "k8s", candidate_channel
        )
        print(f"Channel {candidate_channel} revisions:")
        print(candidate_revision_matrix)

        stable_revision_matrix = charmhub.get_revision_matrix("k8s", stable_channel)
        print(f"Channel {stable_channel} reversions:")
        print(stable_revision_matrix)
    except HTTPError as e:
        print(f"failed to get charm revisions: {e}")
        return ProcessState.PROCESS_CI_FAILED

    if not candidate_revision_matrix:
        print(f"The channel {candidate_channel} has no revisions. Skipping...")
        return ProcessState.PROCESS_UNCHANGED

    if candidate_revision_matrix == stable_revision_matrix:
        print(
            f"The channel {candidate_channel} is already published in {stable_channel}. Skipping..."
        )
        return ProcessState.PROCESS_UNCHANGED

    try:
        state = ensure_track_state(
            "k8s", candidate_channel, candidate_revision_matrix, dry_run
        )
        print(f"Track {track} is in state: {state}")

        if state.succeeded:
            print(f"Release run for {track} succeeded. Promoting charm revisions...")
            if not dry_run:
                charmhub.promote_charm("k8s", candidate_channel, stable_channel)
                charmhub.promote_charm("k8s-worker", candidate_channel, stable_channel)
            return ProcessState.PROCESS_SUCCESS
        elif state.in_progress:
            print(f"Release run for {track} is still in progress. No action needed.")
            return ProcessState.PROCESS_IN_PROGRESS
        elif state.failed:
            print(f"Release run for {track} failed. Manual intervention required.")
            return ProcessState.PROCESS_FAILED
        else:
            print(f"Unknown state for {track}. Skipping...")
            return ProcessState.PROCESS_CI_FAILED
    except Exception as e:
        print(f"process track {track} failed: {e}")
        return ProcessState.PROCESS_CI_FAILED


def main():
    parser = argparse.ArgumentParser(
        description="Automate k8s-operator charm release process."
    )
    parser.add_argument(
        "--dry-run", action="store_true", required=False, help="Dry run the charm release process"
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--supported-tracks", nargs="+", default=[], help="List of tracks to check for"
    )
    group.add_argument("--after", nargs=1, default="1.32", help="Least supported track")

    args = parser.parse_args()
    print(args)
    if args.supported_tracks:
        tracks = args.supported_tracks
    else:
        print(f"Getting all Kubernetes releases after {args.after} inclusive.")
        tracks = k8s.get_all_releases_after(args.after)

    if not tracks:
        print("No tracks found for charm release process. Skipping...")
        return

    print(f"Starting the charm release process for: {tracks}")

    results = {}
    for track in tracks:
        process_state = process_track(track, args.dry_run)
        if process_state in [
            ProcessState.PROCESS_IN_PROGRESS,
            ProcessState.PROCESS_UNCHANGED,
        ]:
            continue
        results[f"{track}"] = str(process_state)

    with open("results.txt", "w") as f:
        for key, value in results.items():
            f.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
