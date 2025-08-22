"""
Script to run SQA builds for k8s-operator charms. This script is intended
to generate single builds on SQA platform to provide internal insights to
the team about possible failures on charms before releasing them to candidate.

"""

import argparse
import logging
import random
import re
from typing import Dict

from requests.exceptions import HTTPError
from util import charmhub, k8s, sqa

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def get_state() -> dict[str, dict]:
    pattern = re.compile(r"^(\d+)-([^-]+)-([^-]+)-([^-]+)-([^-]+)$")

    state = {}
    for status in ["Queued", "Running", "Finished"]:
        builds = sqa.list_builds(status=status)
        for build in builds:
            # only the k8s-operator builds from cdkbot pass this check
            match = pattern.match(build.addon_id)
            if not match:
                continue
            revision, arch, base, track, risk = match.groups()
            state[revision] = {
                "uuid": str(build.uuid),
                "arch": arch,
                "base": base,
                "channel": f"{track}/{risk}",
            }

    return state


def get_results(state: dict) -> Dict[str, dict]:
    """Get the results of the builds for a specific track."""

    log.info("Getting results from previous test runs...")
    results: dict[str, dict] = {}

    if not state:
        log.info("No state found, returning empty results.")
        return results

    for revision, build_details in state.items():
        try:
            build = sqa.get_build(build_details.get("uuid"))
            if not build:
                log.warning(f"Build with UUID {build_details.get("uuid")} not found.")
                continue
            results[revision] = {
                "status": build.status,
                "result": build.result,
                "uuid": str(build.uuid),
                "arch": build_details.get("arch"),
                "base": build_details.get("base"),
                "channel": build_details.get("channel")
            }
        except sqa.SQAFailure as e:
            log.error(f"Failed to get build {build_details.get("uuid")}: {e}")

    return results


def create_one_build(
    state: dict[str, dict], track: str, risk_level: str, arch: str, base: str, dry_run: bool
):
    """Process the given channel based on its current state."""
    log.info(f"Current state: {state}")
    channel = f"{track}/{risk_level}"
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
    (base_in_test, arch_in_test) = random.choice(testable_revisions)        #nosec
    log.info(f"Selected base {base_in_test} and arch {arch_in_test} for testing.")

    revisions = k8s_operator_bundle.get_revisions(arch_in_test, base_in_test)
    version = f"{revisions.get("k8s_revision")}-{arch_in_test}-{base_in_test}-{track}-{risk_level}"
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
        build = sqa.create_build(version, variables)
        state[revisions.get("k8s_revision")] = {
            "uuid": str(build.uuid),
            "base": base_in_test,
            "arch": arch_in_test,
            "channel": channel
        }


def main():
    parser = argparse.ArgumentParser(
        description="Run a single SQA build and report the results from previuos runs."
    )
    parser.add_argument(
        "--arch", default="amd64", help="Architecture to run the builds on"
    )
    parser.add_argument("--base", help="Base to run the builds on")
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

    state = get_state()

    results = {}
    for track in tracks:
        create_one_build(
            state,
            track,
            args.risk_level,
            args.arch,
            args.base,
            args.dry_run,
        )
        
    results = get_results(state)
    with open("results.txt", "w") as f:
        for revision, details in results.items():
            f.write(
                f"Revision: {revision}, Status: {details.get("status")}, Result: {details.get("result")}, UUID: {details.get("uuid")}, Arch: {details.get("arch")}, Base: {details.get("base")}, Channel: {details.get("channel")}\n"
            )


if __name__ == "__main__":
    main()
