from pathlib import Path
from unittest.mock import patch

import util.repo as repo

THIS_REPO = "https://github.com/canonical/canonical-kubernetes-release-ci.git"
DEFAULT_BRANCH = "main"
MOCK_SHA1 = "b62834117fda42ab47079d32ad62d8bdb7533132"
MOCK_SHA1_SHORT = "b628341"

MOCK_LS_REMOTE_SYMREF = f"ref: refs/heads/{DEFAULT_BRANCH}\tHEAD\n{MOCK_SHA1}\tHEAD"
MOCK_LS_REMOTE_HEADS_MAIN = f"{MOCK_SHA1}\trefs/heads/{DEFAULT_BRANCH}"
MOCK_LS_REMOTE_HEADS_ALL = (
    f"{MOCK_SHA1}\trefs/heads/{DEFAULT_BRANCH}\n"
    f"a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\trefs/heads/other-branch"
)


def test_is_branch():
    with patch("util.repo._parse_output") as mock_parse:
        mock_parse.side_effect = [MOCK_LS_REMOTE_SYMREF, MOCK_LS_REMOTE_HEADS_MAIN]
        default = repo.default_branch(THIS_REPO)
        assert repo.is_branch(THIS_REPO, default), "Default branch should undoubtedly exist"


def test_clone():
    default = repo.default_branch(THIS_REPO)
    with repo.clone(THIS_REPO, default, True) as dir:
        branch_sha1 = repo.commit_sha1(dir)
        assert branch_sha1, "Expected a commit SHA1"
    with repo.clone(THIS_REPO) as dir:
        assert branch_sha1 == repo.commit_sha1(dir), "Expected same commit SHA1"
        assert repo.commit_sha1(dir, short=True) in branch_sha1, "Expected short SHA1"


def test_ls_branches():
    with patch("util.repo._parse_output") as mock_parse:
        mock_parse.side_effect = [MOCK_LS_REMOTE_SYMREF, MOCK_LS_REMOTE_HEADS_ALL]
        default = repo.default_branch(THIS_REPO)
        branches = list(repo.ls_branches(THIS_REPO))
        assert default in branches, "Expected default branch in branches"


def test_ls_tree():
    this_path = Path(__file__).parent
    paths = repo.ls_tree(this_path, "tests")
    assert paths, "Expected some paths"
