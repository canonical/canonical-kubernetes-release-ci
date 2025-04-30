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
        - skip if the latest revsion on <track>/candidate is already published in <track>/stable
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

from util import charmhub, sqa
from requests.exceptions import HTTPError
from typing import List

class ProcessState:
    PROCESS_SUCCESS = "PROCESS_SUCCESS"
    PROCESS_IN_PROGRESS = "PROCESS_IN_PROGRESS"
    PROCESS_FAILED = "PROCESS_FAILED"
    PROCESS_CI_FAILED = "PROCESS_CI_FAILED"
    PROCESS_UNCHANGED = "PROCESS_UNCHANGED"

# Define possible states for a track
class TrackState:
    NO_TEST = "NO_TEST"
    TEST_IN_PROGRESS = "TEST_IN_PROGRESS"
    TEST_SUCCESS = "TEST_SUCCESS"
    TEST_FAILED = "TEST_FAILED"
    UNKNOWN_STATE = "UNKNOWN_STATE"

def get_tracks():
    """Retrieve a list of supported tracks."""
    # TODO: Should the supported tracks come from here or in a separate step of the Github Action and injected as an argument?
    return ["1.32", "1.33"]  

def get_supported_archs() -> List[str]:
    """Get the list of supported architectures."""
    # Note(Reza): Currently SQA only supports the test for the amd64 architecture
    # we should differentiate the TPIs for different architectures once arm64 is 
    # also supported.
    return ["amd64"]

def get_track_state(channel, revision) -> TrackState:
    """Determine the current state of the given (channel, revision)."""
    
    current_release_run = sqa.current_release_run(channel, revision)
    if not current_release_run:
        return TrackState.NO_TEST
    if current_release_run.in_progress:
        return TrackState.TEST_IN_PROGRESS
    elif current_release_run.succeeded:
        return TrackState.TEST_SUCCESS
    elif current_release_run.failed:
        return TrackState.TEST_FAILED

    return TrackState.UNKNOWN_STATE

def process_track(track, arch) -> ProcessState:
    """Process the given (track, arch) based on its current state."""

    channel = f"{track}/candidate"
    stable_channel = f"{track}/stable"

    try:
        latest_charm_revision = charmhub.get_latest_charm_revision("k8s", channel , arch)
        print(f"Channel {channel} latest reversion: {latest_charm_revision}")

        latest_stable_charm_revision = charmhub.get_latest_charm_revision("k8s", stable_channel , arch)
        print(f"Channel {channel} latest reversion: {latest_charm_revision}")
    except HTTPError as e:
        print(f"failed to get charm revisions: {e}")
        return ProcessState.PROCESS_CI_FAILED

    if latest_charm_revision == latest_stable_charm_revision:
        print(f"The channel {channel} latest revision {latest_charm_revision} is already published in {stable_channel}. Skipping...")
        return ProcessState.PROCESS_UNCHANGED

    try:
        state = get_track_state(channel, latest_charm_revision)
        print(f"Track {track} on {arch} is in state: {state}")

        if state == TrackState.NO_TEST:
            print(f"No release run for {track} yet. Starting a new one...")
            sqa.start_release_test(channel, latest_charm_revision)
            return ProcessState.PROCESS_IN_PROGRESS
        elif state == TrackState.TEST_IN_PROGRESS:
            print(f"Release run for {track} is still in progress. No action needed.")
            return ProcessState.PROCESS_IN_PROGRESS
        elif state == TrackState.TEST_SUCCESS:
            print(f"Release run for {track} succeeded. Promoting charm revisions...")
            charmhub.promote_charm("k8s", channel, stable_channel)
            charmhub.promote_charm("k8s-worker", channel, stable_channel)
            return ProcessState.PROCESS_SUCCESS
        elif state == TrackState.TEST_FAILED:
            print(f"Release run for {track} failed. Manual intervention required.")
            return ProcessState.PROCESS_FAILED
        else:
            print(f"Unknown state for {track}. Skipping...")
            return ProcessState.PROCESS_CI_FAILED
    except Exception as e:
        print(f"process track {track} on {arch} faile: {e}")
        return ProcessState.PROCESS_CI_FAILED

def main():
    parser = argparse.ArgumentParser(description="Automate k8s-operator charm release process.")
    parser.add_argument("--ignored-tracks", nargs="*", default=[], help="List of tracks to ignore")
    parser.add_argument("--ignored-archs", nargs="*", default=[], help="List of archs to ignore")
    args = parser.parse_args()

    tracks = get_tracks()
    archs = get_supported_archs()
    
    results = {}
    for track in tracks:
        if track in args.ignored_tracks:
            print(f"Skipping ignored track: {track}")
            continue

        for arch in archs:
            if arch in args.ignored_archs:
                print(f"Skipping ignored arch: {arch}")
                continue
              
            
            process_state = process_track(track, arch)
            if process_state in [ProcessState.PROCESS_IN_PROGRESS, ProcessState.PROCESS_UNCHANGED]:
                continue    
            results[f"{track}-{arch}"] = str(process_state)

    with open("results.txt", "w") as f:
        for key, value in results.items():
            f.write(f"{key}={value}\n") 

if __name__ == "__main__":
    main()
