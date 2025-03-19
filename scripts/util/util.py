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


def flavors(dir: str) -> list[str]:
    patch_dir = Path("build-scripts/patches")
    output = repo.ls_tree(dir, patch_dir)
    patches = set(Path(f).relative_to(patch_dir).parents[0] for f in output)
    return sorted([p.name for p in patches] + ["classic"])


def recipe_name(flavor: str, ver: semver.Version, tip: bool) -> str:
    if tip:
        return f"{SNAP_NAME}-snap-tip-{flavor}"
    return f"{SNAP_NAME}-snap-{ver.major}.{ver.minor}-{flavor}"


def setup_logging(args: argparse.Namespace):
    FORMAT = "%(name)20s %(asctime)s %(levelname)8s - %(message)s"
    logging.basicConfig(format=FORMAT)
    if args.loglevel != logging.getLevelName(LOG.root.level):
        LOG.root.setLevel(level=args.loglevel.upper())


def setup_arguments(arg_parser: argparse.ArgumentParser):
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


def execute(
    cmd: List[str], check=True, timeout: Optional[int] = EXEC_TIMEOUT, cwd=None
):
    """Run the specified command and return the stdout/stderr output as a tuple."""
    LOG.debug("Executing: %s, cwd: %s.", cmd, cwd)
    proc = subprocess.run(
        cmd, check=check, timeout=timeout, cwd=cwd, capture_output=True, text=True
    )
    return proc.stdout, proc.stderr


def upstream_prerelease_to_snap_track(prerelease: str) -> str:
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
