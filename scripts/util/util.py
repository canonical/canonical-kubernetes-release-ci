import argparse
import logging
import re
import subprocess
from pathlib import Path

import semver

LOG = logging.getLogger(__name__)
SNAP_NAME: str = "k8s"
SNAP_REPO: str = "https://github.com/canonical/k8s-snap.git/"
LP_OWNER: str = "containers"
TIP_BRANCH = re.compile(r"^(?:main)|^(?:release-\d+\.\d+)$")


def flavors(dir: str) -> list[str]:
    patch_dir = Path("build-scripts/patches")
    output = parse_output(
        ["git", "ls-tree", "--full-tree", "-r", "--name-only", "HEAD", patch_dir],
        cwd=dir,
    )
    patches = set(
        Path(f).relative_to(patch_dir).parents[0] for f in output.splitlines()
    )
    return sorted([p.name for p in patches] + ["classic"])


def recipe_name(flavor: str, ver: semver.Version, tip: bool) -> str:
    if tip:
        return f"{SNAP_NAME}-snap-tip-{flavor}"
    return f"{SNAP_NAME}-snap-{ver.major}.{ver.minor}-{flavor}"


def parse_output(*args, **kwargs) -> str:
    return (
        subprocess.run(*args, capture_output=True, check=True, **kwargs)
        .stdout.decode()
        .strip()
    )


def setup_logging(args: argparse.Namespace):
    FORMAT = "%(name)20s %(asctime)s %(levelname)8s - %(message)s"
    logging.basicConfig(format=FORMAT)
    if args.loglevel:
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
