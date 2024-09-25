from pathlib import Path

import util.repo as repo

THIS_REPO = "https://github.com/canonical/canonical-kubernetes-release-ci.git"
DEFAULT_BRANCH = "main"


def test_is_branch():
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
    default = repo.default_branch(THIS_REPO)
    branches = repo.ls_branches(THIS_REPO)
    assert default in branches, "Expected default branch in branches"


def test_ls_tree():
    this_path = Path(__file__).parent
    paths = repo.ls_tree(this_path, "tests")
    assert paths, "Expected some paths"
