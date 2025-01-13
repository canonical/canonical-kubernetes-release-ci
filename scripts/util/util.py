import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

import requests
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


def download_file(url: str, dest: str, timeout: int = 10):
    with requests.get(url, stream=True, timeout=timeout) as r:
        with open(dest, "wb") as f:
            shutil.copyfileobj(r.raw, f)


def get_k8s_snap_bom(url: str):
    tmpdir = tempfile.mkdtemp()
    try:
        snap_path = os.path.join(tmpdir, "k8s.snap")
        download_file(url, snap_path)
        execute(
            ["unsquashfs", "-q", "-n", snap_path, "-extract-file", "bom.json"],
            cwd=tmpdir,
        )
        bom_path = os.path.join(tmpdir, "squashfs-root", "bom.json")
        with open(bom_path, "r") as f:
            return json.load(f)
    finally:
        shutil.rmtree(tmpdir)


def get_k8s_snap_version(url: str) -> str:
    """Retrieve the Kubernetes component version for a given snap download url."""
    bom = get_k8s_snap_bom(url)
    return bom["components"]["kubernetes"]["version"]


def execute(cmd: List[str], check=True, timeout=EXEC_TIMEOUT, cwd=None):
    """Run the specified command and return the stdout/stderr output as a tuple."""
    LOG.debug("Executing: %s, cwd: %s.", cmd, cwd)
    proc = subprocess.run(
        cmd, check=check, timeout=timeout, cwd=cwd, capture_output=True, text=True
    )
    return proc.stdout, proc.stderr
