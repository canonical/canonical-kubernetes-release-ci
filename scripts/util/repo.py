import contextlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator

LOG = logging.getLogger(__name__)


def _parse_output(*args, **kwargs) -> str:
    return subprocess.check_output(*args, text=True, **kwargs).strip()


@contextlib.contextmanager
def clone(
    repo_url: str,
    repo_tag: str | None = None,
    shallow: bool = True,
    base_dir: str | None = None,
) -> Generator[Path, Any, Any]:
    """
    Clone a git repository on a temporary directory and return the directory.

    Example usage:

    ```
    with repo.git("https://github.com/canonical/k8s-snap", "main") as dir:
        print("Repo cloned at", dir)
    ```
    """

    with tempfile.TemporaryDirectory(dir=base_dir) as tmpdir:
        cmd = ["git", "clone", repo_url, tmpdir]
        if repo_tag:
            cmd.extend(["-b", repo_tag])
        if shallow:
            cmd.extend(["--depth", "1"])
        LOG.info("Cloning %s @ %s (shallow=%s)", repo_url, repo_tag, shallow)
        _parse_output(cmd)
        yield Path(tmpdir)


def is_branch(repo: str, branch_name: str) -> bool:
    commits = _commit_sha1_per_branch(repo, branch_name)
    return f"refs/heads/{branch_name}" in commits


def _commit_sha1_per_branch(
    repo: str, branch_name: None | str = None
) -> Dict[str, str]:
    out = _parse_output(
        ["git", "ls-remote", "--heads", repo] + ([branch_name] if branch_name else [])
    )
    vals = dict(line.split(maxsplit=1) for line in out.splitlines())
    return {v: k for k, v in vals.items()}


def default_branch(repo: str) -> str:
    out = _parse_output(["git", "ls-remote", "--symref", repo, "HEAD"])
    default = next(line for line in out.splitlines() if "ref:" in line)
    return default.split()[1].split("refs/heads/")[1]


def commit_sha1(dir: os.PathLike, short: bool = False) -> str:
    cmd = ["git", "rev-parse"] + (short and ["--short"] or []) + ["HEAD"]
    return _parse_output(cmd, cwd=dir).strip()


def ls_branches(repo: str) -> Generator[str, None, None]:
    for ref in _commit_sha1_per_branch(repo):
        yield "/".join(ref.split("/")[2:])


def ls_tree(dir: os.PathLike, patch_dir: None | os.PathLike = None) -> list[str]:
    return sorted(
        subprocess.check_output(
            ["git", "ls-tree", "--full-tree", "-r", "--name-only", "HEAD"] + [patch_dir]
            if patch_dir
            else [],
            text=True,
            cwd=dir,
        )
        .strip()
        .splitlines()
    )
