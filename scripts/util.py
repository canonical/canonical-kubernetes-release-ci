import contextlib
import logging
import os
import subprocess
import tempfile

from configparser import ConfigParser
from functools import cache
from pathlib import Path
from typing import Any, Generator
from urllib.request import urlopen

from launchpadlib.launchpad import Launchpad


LOG = logging.getLogger(__name__)


@cache
def lp_client():
    """Use launchpad credentials to interact with launchpad."""
    cred_file = os.environ.get("LPCREDS", None)
    creds_local = os.environ.get("LPLOCAL", None)
    if cred_file:
        parser = ConfigParser()
        parser.read(cred_file)
        return Launchpad.login_with(
            application_name=parser["1"]["consumer_key"],
            service_root="production",
            version="devel",
            credentials_file=cred_file,
        )
    elif creds_local:
        return Launchpad.login_with(
            "localhost",
            "production",
            version="devel",
        )
    else:
        raise ValueError("No launchpad credentials found")


def branch_flavours(dir: str) -> list[str]:
    patch_dir = Path("build-scripts/patches")
    output = parse_output(
        ["git", "ls-tree", "--full-tree", "-r", "--name-only", "HEAD", patch_dir],
        cwd=dir,
    )
    patches = set(
        Path(f).relative_to(patch_dir).parents[0] for f in output.splitlines()
    )
    return [p.name for p in patches] + ["classic"]


def ensure_track(snap_name: str, track_name: str):
    pass


@contextlib.contextmanager
def git_repo(
    repo_url: str, repo_tag: str, shallow: bool = True
) -> Generator[Path, Any, Any]:
    """
    Clone a git repository on a temporary directory and return the directory.

    Example usage:

    ```
    with git_repo("https://github.com/canonical/k8s-snap", "master") as dir:
        print("Repo cloned at", dir)
    ```

    """

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = ["git", "clone", repo_url, tmpdir, "-b", repo_tag]
        if shallow:
            cmd.extend(["--depth", "1"])
        LOG.info("Cloning %s @ %s (shallow=%s)", repo_url, repo_tag, shallow)
        parse_output(cmd)
        yield Path(tmpdir)


def is_git_branch(repo: str, branch_name: str) -> bool:
    return branch_name in parse_output(
        ["git", "ls-remote", "--head", repo, branch_name]
    )


def git_branch(dir: str) -> str:
    return parse_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dir)


def parse_output(*args, **kwargs) -> str:
    return (
        subprocess.run(*args, capture_output=True, check=True, **kwargs)
        .stdout.decode()
        .strip()
    )


def read_file(path: Path) -> str:
    return path.read_text().strip()


def read_url(url: str) -> str:
    return urlopen(url).read().decode().strip()
