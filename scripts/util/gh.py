"""Utility functions for interacting with GitHub Actions runners."""

REPO_RUNNER_LABEL_MAP = {
    "amd64": "X64",
    "arm64": "ARM64",
}

# Self-hosted runner base image to pin our jobs to.
#
# Our promotion/upgrade tests run LXD containers directly on the runner's
# host kernel (no nested virtualization), so the container's AppArmor
# tooling must be compatible with the host kernel's AppArmor ABI. Ubuntu
# 20.04 LXD containers ship an old apparmor_parser (2.13.3) that cannot
# parse the AppArmor profile protocol used by Noble (24.04) or Resolute
# (26.04) host kernels, which causes container creation to fail with:
#   "apparmor_parser: Unable to replace ... Profile doesn't conform to
#   protocol": exit status 185
# This has been observed to reliably break `cilium-operator` (and other
# pods) on Noble/Resolute hosts, while Jammy (22.04) and Focal (20.04)
# hosts run all of our supported LXD images (20.04/22.04/24.04) without
# issue. Pin to Jammy until the AppArmor incompatibility is resolved
# upstream or we no longer test ubuntu:20.04 LXD images.
RUNNER_OS_LABEL = "jammy"


def arch_to_gh_labels(arch: str, self_hosted: bool = False) -> list[str]:
    labels = []
    if label := REPO_RUNNER_LABEL_MAP.get(arch):
        labels.append(label)
    if self_hosted:
        labels.extend(["self-hosted", RUNNER_OS_LABEL])
    return labels
