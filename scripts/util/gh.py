"""Utility functions for interacting with GitHub Actions runners."""

REPO_RUNNER_LABEL_MAP = {
    "amd64": "X64",
    "arm64": "ARM64",
}


def arch_to_gh_labels(arch: str, self_hosted: bool = False) -> list[str]:
    labels = []
    if label := REPO_RUNNER_LABEL_MAP.get(arch):
        labels.append(label)
    if self_hosted:
        labels.append("self-hosted")
    return labels
