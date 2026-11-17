import tempfile
import unittest.mock as mock

import pytest
import util.lp as lp


@pytest.fixture(autouse=True)
def clear_lp_client_cache():
    lp.client.cache_clear()


@mock.patch("launchpadlib.launchpad.Launchpad.login_with")
def test_create_client_with_file(mock_login):
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(b"[1]\nconsumer_key = some-key\n")
        temp_file.flush()
        with mock.patch.dict("os.environ", {"LPCREDS": temp_file.name}):
            client = lp.client()
            assert client, "Expected a client"
            mock_login.assert_called_once_with(
                application_name="some-key",
                service_root="production",
                version="devel",
                credentials_file=temp_file.name,
            )
    assert lp.client.cache_info().misses == 1, "Expected a cache miss"
    lp.client()
    assert lp.client.cache_info().hits == 1, "Expected a cache hit"


@mock.patch.dict("os.environ", {"LPLOCAL": "True"})
@mock.patch("launchpadlib.launchpad.Launchpad.login_with")
def test_create_client_with_local(mock_login):
    client = lp.client()
    assert client, "Expected a client"
    mock_login.assert_called_once_with(
        "localhost",
        "production",
        version="devel",
    )

    assert lp.client.cache_info().misses == 1, "Expected a cache miss"
    lp.client()
    assert lp.client.cache_info().hits == 1, "Expected a cache hit"


def test_create_client_no_creds():
    with pytest.raises(ValueError, match="No launchpad credentials found"):
        lp.client()
    with pytest.raises(ValueError, match="No launchpad credentials found"):
        lp.client()
    assert lp.client.cache_info().misses == 2, "Expected a cache miss"


@mock.patch("util.lp.client")
def test_snap_recipe_found(mock_client):
    # Regression test: snap_recipe() previously called getByName() without
    # returning its result, so callers (e.g. request_builds.py) always saw
    # None even when the recipe existed, silently skipping rebuild requests.
    mock_recipe = mock.Mock()
    mock_client.return_value.snaps.getByName.return_value = mock_recipe
    owner = mock.Mock()

    result = lp.snap_recipe(owner, "k8s-snap-1.38-classic")

    assert result is mock_recipe
    mock_client.return_value.snaps.getByName.assert_called_once_with(
        owner=owner, name="k8s-snap-1.38-classic"
    )


@mock.patch("util.lp.client")
def test_snap_recipe_not_found(mock_client):
    mock_client.return_value.snaps.getByName.side_effect = lp.NotFound(None, b"")
    owner = mock.Mock(name="owner")

    result = lp.snap_recipe(owner, "k8s-snap-1.38-classic")

    assert result is None


def _mock_build(
    arch: str,
    state: str,
    *,
    can_be_retried: bool = False,
    revision_id: str | None = None,
    store_upload_revision: int | None = None,
    store_upload_status: str = "Uploaded",
) -> mock.Mock:
    build = mock.Mock()
    build.arch_tag = arch
    build.buildstate = state
    build.can_be_retried = can_be_retried
    build.revision_id = revision_id
    build.store_upload_revision = store_upload_revision
    build.store_upload_status = store_upload_status
    return build


@mock.patch("util.lp.snap_by_owner")
def test_recipe_for_track_and_branch(mock_snap_by_owner):
    r135 = mock.Mock(
        store_channels=["1.35-classic/edge"],
        git_ref_link="https://api.launchpad.net/devel/~cdk8s/k8s/+git/k8s-snap/+ref/release-1.35",
    )
    r136 = mock.Mock(
        store_channels=["1.36-classic/edge"],
        git_ref_link="https://api.launchpad.net/devel/~cdk8s/k8s/+git/k8s-snap/+ref/release-1.36",
    )
    mock_snap_by_owner.return_value = [r135, r136]

    assert lp.recipe_for_track("k8s", "1.36-classic") is r136
    assert lp.recipe_for_track("k8s", "1.99-classic") is None
    assert lp.branch_from_track("k8s", "1.35-classic") == "release-1.35"


def test_retry_failed_builds_only_retries_latest_failed_per_arch():
    amd64_failed_latest = _mock_build("amd64", "Failed to build", can_be_retried=True)
    amd64_older_ok = _mock_build("amd64", "Successfully built", store_upload_revision=5877)
    arm64_latest_ok = _mock_build("arm64", "Successfully built", store_upload_revision=5897)
    arm64_older_failed = _mock_build("arm64", "Failed to build", can_be_retried=True)
    s390x_building = _mock_build("s390x", "Currently building")
    s390x_older_failed = _mock_build("s390x", "Failed to build", can_be_retried=True)

    recipe = mock.Mock(
        name="k8s-snap-1.35-classic",
        builds=[
            amd64_failed_latest,
            arm64_latest_ok,
            s390x_building,
            amd64_older_ok,
            arm64_older_failed,
            s390x_older_failed,
        ],
    )

    retried = lp.retry_failed_builds(recipe, dry_run=False)

    assert retried == ["amd64"]
    amd64_failed_latest.retry.assert_called_once_with()
    arm64_older_failed.retry.assert_not_called()
    s390x_older_failed.retry.assert_not_called()


def test_retry_failed_builds_respects_dry_run_and_arch_filter():
    amd64_failed = _mock_build("amd64", "Failed to build", can_be_retried=True)
    arm64_failed = _mock_build("arm64", "Failed to build", can_be_retried=True)
    recipe = mock.Mock(builds=[amd64_failed, arm64_failed])

    assert lp.retry_failed_builds(recipe, arch="arm64", dry_run=True) == ["arm64"]
    amd64_failed.retry.assert_not_called()
    arm64_failed.retry.assert_not_called()


@pytest.mark.parametrize(
    "head_sha, builds, edge_rev, expected",
    [
        # Up-to-date: edge revision matches git_ref HEAD and latest build succeeded.
        (
            "b5634b2f" * 5,
            [
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="b5634b2f" * 5,
                    store_upload_revision=5890,
                )
            ],
            5890,
            False,
        ),
        # Stale: post-merge build failed before git clone (revision_id=None), edge holds older SHA.
        (
            "b5634b2f" * 5,
            [
                _mock_build("amd64", "Failed to build", can_be_retried=True, revision_id=None),
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="e5aec773" * 5,
                    store_upload_revision=5877,
                ),
            ],
            5877,
            True,
        ),
        # Stale: git_ref moved ahead before Launchpad even created the new build record.
        (
            "b5634b2f" * 5,
            [
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="e5aec773" * 5,
                    store_upload_revision=5877,
                )
            ],
            5877,
            True,
        ),
        # Stale: newer build is currently in progress.
        (
            "b5634b2f" * 5,
            [
                _mock_build("amd64", "Currently building", revision_id=None),
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="e5aec773" * 5,
                    store_upload_revision=5877,
                ),
            ],
            5877,
            True,
        ),
        # Stale: latest build finished compiling head_sha but store upload is still pending.
        (
            "b5634b2f" * 5,
            [
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="b5634b2f" * 5,
                    store_upload_revision=None,
                    store_upload_status="Pending",
                ),
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="e5aec773" * 5,
                    store_upload_revision=5877,
                ),
            ],
            5877,
            True,
        ),
    ],
)
def test_edge_is_stale(head_sha, builds, edge_rev, expected):
    recipe = mock.Mock(git_ref=mock.Mock(commit_sha1=head_sha), builds=builds)
    assert lp.edge_is_stale(recipe, arch="amd64", edge_revision=edge_rev) is expected
