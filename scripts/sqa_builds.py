"""
Script to run SQA builds for k8s-operator charms. This script is intended
to generate single builds on SQA platform to provide internal insights to
the team about possible failures on charms before releasing them to candidate.

"""

import argparse
import json
import logging
import os
import random
import re
from typing import Dict

from requests.exceptions import HTTPError
from util import charmhub, k8s, sqa

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def get_state(state_file: str):
    if not state_file or not os.path.exists(state_file):
        log.info("no state file found.")
        return {}
    else:
        if os.path.getsize(state_file) == 0:
            log.info("state file is empty.")
            return {}
        else:
            with open(state_file, "r") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    print("File contains invalid JSON, defaulting to empty.")
                    data = {}

                return data


def get_track_results(state_file: str) -> Dict[str, str]:
    """Get the results of the builds for a specific track."""

    log.info("Getting results from previous test runs...")
    results = {}

    state = get_state(state_file)
    if not state:
        log.info("No state found, returning empty results.")
        return results

    for revision, build_uuid in state.items():
        try:
            build = sqa.get_build(build_uuid)
            if not build:
                log.warning(f"Build with UUID {build_uuid} not found.")
                continue
            results[revision] = (
                f"status: {build.status} result: {build.result} uuid: {build.uuid}"
            )
        except sqa.SQAFailure as e:
            log.error(f"Failed to get build {build_uuid}: {e}")

    return results


def create_one_build(
    channel: str, state_file: json, arch: str, base: str, dry_run: bool
):
    """Process the given channel based on its current state."""

    state = get_state(state_file)
    log.info(f"Current state: {state}")

    k8s_operator_bundle = charmhub.Bundle("k8s-operator")
    for charm in ["k8s", "k8s-worker"]:
        log.info(f"Getting revisions for {charm} charm on channel {channel}")
        try:
            revision_matrix = charmhub.get_revision_matrix(charm, channel)
        except HTTPError:
            log.exception(
                f"failed to get revision matrix for charm {charm} channel {channel}"
            )
            return

        if not revision_matrix:
            log.exception(f"charm {charm} has no revisions on channel {channel}")
            return

        log.info(
            f"Revision matrix for {charm} on channel {channel} \n: {revision_matrix}"
        )
        k8s_operator_bundle.set(charm, revision_matrix)

    k8s_revision_matrix = k8s_operator_bundle.get("k8s")
    testable_revisions = []
    for matrix_base in k8s_revision_matrix.get_bases():
        for matrix_arch in k8s_revision_matrix.get_archs():
            if arch and arch != matrix_arch:
                continue

            if base and base != matrix_base:
                continue

            revision = k8s_revision_matrix.get(matrix_arch, matrix_base)
            if revision and not state.get(str(revision)):
                testable_revisions.append((matrix_base, matrix_arch))

    if not testable_revisions:
        log.info(
            "The constraints resulted in no testable revisions or they are already tested. Skipping..."
        )
        return

    log.info(
        f"Found {len(testable_revisions)} testable revision(s) for channel {channel}: {testable_revisions}"
    )
    (base_in_test, arch_in_test) = random.choice(testable_revisions)
    log.info(f"Selected base {base_in_test} and arch {arch_in_test} for testing.")

    revisions = k8s_operator_bundle.get_revisions(arch_in_test, base_in_test)
    track = channel.split("/")[0]
    variables = {
        "app": lambda name: name,
        "base": base_in_test,
        "arch": arch_in_test,
        "channel": channel,
        "branch": f"release-{track}",
        **revisions,
    }

    if m := re.match(r"^(\d+)\.(\d+)", track):
        if tuple(map(int, m.groups())) <= (1, 32):
            # For channels <= 1.32 we use underscore names
            variables["app"] = lambda name: name.replace("-", "_")

    log.info(f"Creating SQA build for {channel} for revisions: {revisions}")
    if not dry_run:
        build = sqa.create_build(variables)
        state[revisions.get("k8s_revision")] = str(build.uuid)
        with open(state_file, "w") as f:
            json.dump(state, f, indent=4)


def main():
    parser = argparse.ArgumentParser(
        description="Run a single SQA build and report the results from previuos runs."
    )
    parser.add_argument(
        "--arch", default="amd64", help="Architecture to run the builds on"
    )
    parser.add_argument("--base", help="Base to run the builds on")
    parser.add_argument(
        "--state-file",
        default="sqa_builds_state.json",
        help="File to store the state of the builds",
    )
    parser.add_argument(
        "--risk-level", default="beta", help="Risk level to run the builds for"
    )
    parser.add_argument(
        "--dry-run", action="store_true", required=False, help="Dry run the  process"
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--supported-tracks", nargs="+", default=[], help="List of tracks to check for"
    )
    group.add_argument("--after", nargs=1, default="1.32", help="Least supported track")

    args = parser.parse_args()

    if args.supported_tracks:
        tracks = args.supported_tracks
    else:
        log.info(f"Getting all Kubernetes releases after {args.after} inclusive.")
        tracks = k8s.get_all_releases_after(args.after)

    if not tracks:
        log.info("No tracks to create the SQA builds for. Skipping...")
        return

    log.info(f"Starting the test build process for: {tracks}")

    results = {}
    for track in tracks:
        create_one_build(
            f"{track}/{args.risk_level}",
            args.state_file,
            args.arch,
            args.base,
            args.dry_run,
        )

        track_results = get_track_results(
            args.state_file,
        )
        results[f"{track}"] = str(track_results)

    with open("results.txt", "w") as f:
        for key, value in results.items():
            f.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
