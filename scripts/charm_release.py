"""
Script to automate the k8s-operator charms release process.

The implementation works as a state machine that queries the current state of each track and then decides what to do next.
The script is designed to be idempotent, meaning that it can be run multiple times without causing any harm.

for each track:
    get data for current track state:
        - get current release runs based on the product version (which is <track_name>-<ubuntu_charm_revision>)
        - get current charm revisions in <track>/beta

    track is in one of the following states:
        - no release runs yet -> NO_RELEASE_RUN
        - release run in progress -> RELEASE_RUN_IN_PROGRESS
        - release run with old charm revisions -> OUTDATED_RELEASE_RUN
        - release run success -> RELEASE_RUN_SUCCESS
        - release run failed/aborted -> RELEASE_RUN_FAILED

    Actions:
        - NO_RELEASE_RUN: start a new release run
        - RELEASE_RUN_IN_PROGRESS: just print that a log message
        - OUTDATED_RELEASE_RUN: abort the release run and start a new one
        - RELEASE_RUN_SUCCESS: promote the charm revisions to the next channel
        - RELEASE_RUN_FAILED: manual intervention with SQA required


Question:
* Long-term, will there be different testplans for Canonical Kubernetes and Charmed Kubernetes or are we
  just replacing the product under test via addons?
* Why can I set the status of a testplaninstance when adding it? Isn't that the responsibility of the test scheduler?


Current SQA limitations:
* Productversions API contains a bug that it cannot be filtered for "product" name. Fetching all productversions just times out...
  That means, right now there is no way of getting the productversion after it was created.
  We need to store it somewhere ugly.
* I cannot filter testplaninstances for "product_under_test" field. Because of that, I need to:
    1. Get the testplans for Canonical Kubernetes (hardcoded for now to save this API call - ugh)
    2. For each of them, get the testplaninstances

"""
import argparse

from util import charmhub, sqa

# Define possible states for a track
class TrackState:
    NO_RELEASE_RUN = "NO_RELEASE_RUN"
    RELEASE_RUN_IN_PROGRESS = "RELEASE_RUN_IN_PROGRESS"
    OUTDATED_RELEASE_RUN = "OUTDATED_RELEASE_RUN"
    RELEASE_RUN_SUCCESS = "RELEASE_RUN_SUCCESS"
    RELEASE_RUN_FAILED = "RELEASE_RUN_FAILED"
    UNKNOWN_STATE = "UNKNOWN_STATE"

def get_tracks():
    """Retrieve a list of available tracks."""
    return ["1.32"]  # TODO: Should the supported tracks come from here or in a separate step of the Github Action and injected as an argument?

def get_track_state(track, arch) -> TrackState:
    """Determine the current state of the given track."""
    current_release_run = sqa.current_release_run(track)
    if not current_release_run:
        return TrackState.NO_RELEASE_RUN

    channel_version_string = charmhub.get_charm_revision("k8s", f"{track}/candidate", arch)
    print(f"Current release run version: {current_release_run.version}")
    print(f"Channel version string: {channel_version_string}")
    if current_release_run.version != channel_version_string:
        return TrackState.OUTDATED_RELEASE_RUN

    if current_release_run.in_progress:
        return TrackState.RELEASE_RUN_IN_PROGRESS
    elif current_release_run.succeeded:
        return TrackState.RELEASE_RUN_SUCCESS
    elif current_release_run.failed:
        return TrackState.RELEASE_RUN_FAILED

    return TrackState.UNKNOWN_STATE

def process_track(track, arch):
    """Process the given track based on its current state."""
    state = get_track_state(track, arch)
    print(f"Track {track} on {arch} is in state: {state}")

    if state == TrackState.NO_RELEASE_RUN:
        print(f"No release run for {track} yet. Starting a new one...")
        sqa.start_release_test(track)
    elif state == TrackState.RELEASE_RUN_IN_PROGRESS:
        print(f"Release run for {track} is still in progress. No action needed.")
    elif state == TrackState.OUTDATED_RELEASE_RUN:
        print(f"Release run for {track} is outdated. Aborting and starting a new one...")
        sqa.abort_release_test(track)
        sqa.start_release_test(track)
    elif state == TrackState.RELEASE_RUN_SUCCESS:
        print(f"Release run for {track} succeeded. Promoting charm revisions...")
        charmhub.promote_charm_revisions("k8s", f"{track}/candidate", f"{track}/stable")
        charmhub.promote_charm_revisions("k8s-worker", f"{track}/candidate", f"{track}/stable")
    elif state == TrackState.RELEASE_RUN_FAILED:
        print(f"Release run for {track} failed. Manual intervention required.")
    else:
        print(f"Unknown state for {track}. Skipping...")

def main():
    parser = argparse.ArgumentParser(description="Automate k8s-operator charm release process.")
    parser.add_argument("--ignored-tracks", nargs="*", default=[], help="List of tracks to ignore")
    parser.add_argument("--ignored-archs", nargs="*", default=[], help="List of archs to ignore")
    args = parser.parse_args()

    tracks = get_tracks()
    archs = charmhub.get_supported_archs()
    for track in tracks:
        if track in args.ignored_tracks:
            print(f"Skipping ignored track: {track}")
            continue

        for arch in archs:
            if arch in args.ignored_archs:
                print(f"Skipping ignored arch: {arch}")
                continue

            process_track(track, arch)

if __name__ == "__main__":
    main()
