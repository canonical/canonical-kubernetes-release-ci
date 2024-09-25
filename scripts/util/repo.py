import contextlib
import logging
import tempfile
from pathlib import Path
from typing import Any, Generator, List

from util.util import parse_output

LOG = logging.getLogger(__name__)


@contextlib.contextmanager
def clone(
    repo_url: str, repo_tag: str, shallow: bool = True
) -> Generator[Path, Any, Any]:
    """
    Clone a git repository on a temporary directory and return the directory.

    Example usage:

    ```
    with repo.git("https://github.com/canonical/k8s-snap", "main") as dir:
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


def is_branch(repo: str, branch_name: str) -> bool:
    commits = _commit_sha1_per_branch(repo, branch_name)
    return f"refs/heads/{branch_name}" in commits.keys()


def _commit_sha1_per_branch(repo: str, branch_name: None | str = None) -> List[str]:
    out = parse_output(
        ["git", "ls-remote", "--heads", repo] + ([branch_name] if branch_name else [])
    )
    return dict(reversed(line.split()) for line in out.splitlines())


def which_branch(dir: Path) -> str:
    return parse_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dir)


def commit_sha1(dir: Path) -> str:
    return parse_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dir)


def ls_branches(repo: str) -> Generator[None, str, None]:
    for ref in _commit_sha1_per_branch(repo).keys():
        yield "/".join(ref.split("/")[2:])
