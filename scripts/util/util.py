"""Utility functions for snap build and release scripts."""

import argparse
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional

import semver
import util.repo as repo

LOG = logging.getLogger(__name__)
SNAP_NAME: str = "k8s"
SNAP_REPO: str = "https://github.com/canonical/k8s-snap.git/"
TIP_BRANCH = re.compile(
    r"^(?:main)|^(?:release-\d+\.\d+)$|^(?:autoupdate\/v\d+\.\d+\.\d+-(?:alpha|beta|rc))$"
)
EXEC_TIMEOUT = 60


def flavors(path: str) -> list[str]:
    """Return a sorted list of available flavors in the given directory."""
    patch_dir = Path("build-scripts/patches")
    output = repo.ls_tree(path, patch_dir)
    patches = {Path(f).relative_to(patch_dir).parents[0] for f in output}
    return sorted([p.name for p in patches] + ["classic"])


def recipe_name(flavor: str, ver: semver.Version, tip: bool) -> str:
    """Return the snap recipe name for the given flavor and version."""
    if tip:
        return f"{SNAP_NAME}-snap-tip-{flavor}"
    return f"{SNAP_NAME}-snap-{ver.major}.{ver.minor}-{flavor}"


def setup_logging(args: argparse.Namespace):
    """Set up logging based on command line arguments."""
    format = "%(name)20s %(asctime)s %(levelname)8s - %(message)s"
    logging.basicConfig(format=format)
    if args.loglevel != logging.getLevelName(LOG.root.level):
        LOG.root.setLevel(level=args.loglevel.upper())


def setup_arguments(arg_parser: argparse.ArgumentParser):
    """Set up common command line arguments."""
    arg_parser.add_argument(
        "--dry-run",
        default=False,
        help="Print what would be done without taking action",
        action="store_true",
    )
    arg_parser.add_argument(
        "-l",
        "--log",
        dest="loglevel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )
    args = arg_parser.parse_args()
    setup_logging(args)
    return args


def execute(cmd: List[str], check=True, timeout: Optional[int] = EXEC_TIMEOUT, cwd=None):
    """Run the specified command and return the stdout/stderr output as a tuple."""
    LOG.debug("Executing: %s, cwd: %s.", cmd, cwd)
    proc = subprocess.run(
        cmd, check=check, timeout=timeout, cwd=cwd, capture_output=True, text=True
    )
    return proc.stdout, proc.stderr


def upstream_prerelease_to_snap_track(prerelease: str) -> str:
    """Convert an upstream prerelease string to a snap track."""
    prerelease_map = {
        "alpha": "edge",
        "beta": "beta",
        "rc": "candidate",
    }
    track = prerelease_map.get(prerelease.split(".")[0])
    if not track:
        raise ValueError(
            "Could not determine snap track for upstream pre-release: %s" % prerelease
        )
    return track


def patch_sqa_variables(track: str, variables):
    """Patch the SQA variables for the given snap track."""
    variables = {
        "app": lambda name: name,
        "model": lambda name, cloud: f'{{ name = "{name}", cloud = "{cloud}" }}',
        **variables
    }

    if m := re.match(r"^(\d+)\.(\d+)", track):
        if tuple(map(int, m.groups())) <= (1, 32):
            # For channels <= 1.32 we use underscore names
            variables["app"] = lambda name: name.replace("-", "_")
            variables["model"] = lambda name, _: f"{name}"

    return variables
