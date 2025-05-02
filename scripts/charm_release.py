"""
Script to automate the k8s-operator charms release process.

The implementation works as a state machine that queries the current state of each track
and then decides what to do next.The script is designed to be idempotent, meaning that it 
can be run multiple times without causing any harm. Note than the script is only checking 
the state of the last revision published on each track. That means if revisions n and n+1 
are published on a track, the automation only tries to reconcile the n+1 revision. Whatever 
state the revision n is in will be untouched.

for each (track, arch):
    get data for current (track, arch) state:
        - extract latest revision on channel=<track>/candidate from (track, arch)
        - extract latest revision on stable_channel=<track>/stable from (track, arch)  
        - skip if the latest revision on <track>/candidate is already published in <track>/stable
        - get the (track, arch) state with corresponding (channel, revision) from SQA API

    track with corresponding (channel, revision) is in one of the following states:
        - no TPIs yet -> NO_TEST
        - at least one TPI succeeded -> TEST_SUCCESS
        - at least one TPI in progress -> TEST_IN_PROGRESS
        - there are only failed/(in-)error TPIs -> TEST_FAILED

    Actions:
        - NO_TEST: start a new TPI
        - TEST_IN_PROGRESS: just print that a log message
        - TEST_SUCCESS: promote the charm revisions to the next channel
        - TEST_FAILED: manual intervention with SQA required


Question:
* Long-term, will there be different testplans for Canonical Kubernetes and Charmed Kubernetes or are we
  just replacing the product under test via addons?
* Why can I set the status of a testplaninstance when adding it? Isn't that the responsibility of the test scheduler?

TODOs:
* Support testing different architectures once SQA provides the feature
* Cleaning up outdated and aborted TPIs in a separate cronjob
"""
import argparse
import os
from enum import StrEnum, auto

from requests.exceptions import HTTPError
from util import charmhub, sqa


class TrackState:
    def __init__(self):
        self._state_map = {}

    def set_state(self, revision, state: sqa.TestPlanInstanceStatus):
        if not isinstance(state, sqa.TestPlanInstanceStatus):
            raise ValueError("State must be an instance of TestPlanInstanceStatus")
        self._state_map[revision] = state

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




def get_tracks():
    """Retrieve a list of supported tracks."""
    with open(os.path.join(os.path.dirname(__file__), 'supported_tracks.txt'), 'r') as file:
        keys = [line.strip() for line in file if line.strip()]
    return keys

def ensure_track_state(charm_name, channel, revision_matrix: charmhub.RevisionMatrix) -> TrackState:

    for arch in revision_matrix.get_archs():
        
        # Note(Reza): Currently SQA only supports the test for the amd64 architecture
        # we should differentiate the TPIs for different architectures once arm64 is 
        # also supported.
        if arch != "amd64":
            continue
        
        track_state = TrackState()
        for base in revision_matrix.get_bases():

            revision = revision_matrix.get(arch, base)
            if not revision:
                continue

            current_test_plan_instance = sqa.current_test_plan_instance(charm_name, channel, revision)
            if not current_test_plan_instance:
                sqa.start_release_test(charm_name, channel, revision)
                track_state.set_state(revision, sqa.TestPlanInstanceStatus.IN_PROGRESS)
                continue
            
            track_state.set_state(revision, current_test_plan_instance.status)

    return track_state

def process_track(track) -> ProcessState:
    """Process the given track based on its current state."""

    candidate_channel = f"{track}/candidate"
    stable_channel = f"{track}/stable"

    try:
        candidate_revision_matrix = charmhub.get_revision_matrix("k8s", candidate_channel)
        print(f"Channel {candidate_channel} revisions:")
        print(candidate_revision_matrix)

        stable_revision_matrix = charmhub.get_revision_matrix("k8s", stable_channel)
        print(f"Channel {stable_channel} reversions:")
        print(stable_revision_matrix)
    except HTTPError as e:
        print(f"failed to get charm revisions: {e}")
        return ProcessState.PROCESS_CI_FAILED

    if candidate_revision_matrix == stable_revision_matrix:
        print(f"The channel {candidate_channel} is already published in {stable_channel}. Skipping...")
        return ProcessState.PROCESS_UNCHANGED

    try:
        state = ensure_track_state("k8s", candidate_channel, candidate_revision_matrix)
        print(f"Track {track} is in state: {state}")

        if state.succeeded:
            print(f"Release run for {track} succeeded. Promoting charm revisions...")
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
    parser = argparse.ArgumentParser(description="Automate k8s-operator charm release process.")
    parser.add_argument("--ignored-tracks", nargs="*", default=[], help="List of tracks to ignore")
    args = parser.parse_args()

    tracks = get_tracks()
    
    results = {}
    for track in tracks:
        if track in args.ignored_tracks:
            print(f"Skipping ignored track: {track}")
            continue

        process_state = process_track(track)
        if process_state in [ProcessState.PROCESS_IN_PROGRESS, ProcessState.PROCESS_UNCHANGED]:
            continue    
        results[f"{track}"] = str(process_state)

    with open("results.txt", "w") as f:
        for key, value in results.items():
            f.write(f"{key}={value}\n") 

if __name__ == "__main__":
    main()
