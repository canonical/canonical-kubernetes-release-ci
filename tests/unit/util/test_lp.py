import tempfile
import unittest.mock as mock

import pytest
import requests
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


def test_retry_failed_builds_schedules_store_upload_on_upload_failure():
    upload_failed = _mock_build(
        "amd64",
        "Successfully built",
        can_be_retried=False,
        revision_id="b5634b2f" * 5,
        store_upload_status="Failed to upload",
    )
    recipe = mock.Mock(builds=[upload_failed], processors=[])

    assert lp.retry_failed_builds(recipe, dry_run=True) == ["amd64"]
    upload_failed.scheduleStoreUpload.assert_not_called()
    upload_failed.retry.assert_not_called()

    assert lp.retry_failed_builds(recipe, dry_run=False) == ["amd64"]
    upload_failed.scheduleStoreUpload.assert_called_once_with()
    upload_failed.retry.assert_not_called()


def test_retry_failed_builds_scans_past_other_arch_history_and_stops_at_processors():
    amd64_builds = [
        _mock_build("amd64", "Successfully built", store_upload_revision=6000 + i)
        for i in range(25)
    ]
    arm64_failed = _mock_build("arm64", "Failed to build", can_be_retried=True)
    unreachable_trailing = mock.Mock(
        side_effect=AssertionError("should stop once processors seen")
    )

    def build_stream():
        yield from amd64_builds
        yield arm64_failed
        unreachable_trailing()

    p_amd64 = mock.Mock()
    p_amd64.name = "amd64"
    p_arm64 = mock.Mock()
    p_arm64.name = "arm64"
    recipe = mock.Mock(builds=build_stream(), processors=[p_amd64, p_arm64])

    retried = lp.retry_failed_builds(recipe, dry_run=False)

    assert retried == ["arm64"]
    arm64_failed.retry.assert_called_once_with()


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
        # Up-to-date even when >20 builds from another arch precede amd64's edge build.
        (
            "b5634b2f" * 5,
            [
                *[
                    _mock_build("arm64", "Successfully built", store_upload_revision=7000 + i)
                    for i in range(25)
                ],
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="b5634b2f" * 5,
                    store_upload_revision=5890,
                ),
            ],
            5890,
            False,
        ),
        # Stale when >20 builds from another arch precede amd64's outdated edge build.
        (
            "b5634b2f" * 5,
            [
                *[
                    _mock_build("arm64", "Successfully built", store_upload_revision=7000 + i)
                    for i in range(25)
                ],
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
        # Conservative hold: edge revision's build cannot be found while git_ref HEAD is known.
        (
            "b5634b2f" * 5,
            [
                _mock_build(
                    "amd64",
                    "Successfully built",
                    revision_id="b5634b2f" * 5,
                    store_upload_revision=5800,
                )
            ],
            5890,
            True,
        ),
    ],
)
@mock.patch("util.gh.requests.get")
def test_edge_is_stale(mock_get, head_sha, builds, edge_rev, expected):
    mock_resp = mock.Mock()
    mock_resp.json.return_value = {"status": "behind"}
    mock_get.return_value = mock_resp
    recipe = mock.Mock(git_ref=mock.Mock(commit_sha1=head_sha), builds=builds)
    assert lp.edge_is_stale(recipe, arch="amd64", edge_revision=edge_rev) is expected


@pytest.mark.parametrize(
    "status, expected",
    [
        ("ahead", False),
        ("identical", False),
        ("behind", True),
        ("diverged", True),
        ("unknown", True),
    ],
)
@mock.patch("util.gh.requests.get")
def test_edge_is_stale_ancestry_status(mock_get, status, expected):
    head_sha = "6434d09971" * 4
    edge_sha = "cd66c8a238" * 4
    mock_resp = mock.Mock()
    mock_resp.json.return_value = {"status": status}
    mock_get.return_value = mock_resp

    recipe = mock.Mock(
        git_ref=mock.Mock(commit_sha1=head_sha),
        builds=[
            _mock_build(
                "amd64",
                "Successfully built",
                revision_id=edge_sha,
                store_upload_revision=5901,
            )
        ],
    )

    assert lp.edge_is_stale(recipe, arch="amd64", edge_revision=5901) is expected
    mock_get.assert_called_once_with(
        f"https://api.github.com/repos/canonical/k8s-snap/compare/{head_sha}...{edge_sha}",
        headers=mock.ANY,
        timeout=10,
    )


@pytest.mark.parametrize(
    "side_effect",
    [
        requests.HTTPError("404 Client Error: Not Found"),
        requests.ConnectionError("Connection refused"),
        requests.Timeout("Request timed out"),
        ValueError("No JSON object could be decoded"),
    ],
)
@mock.patch("util.gh.requests.get")
def test_edge_is_stale_compare_api_error_holds_stale(mock_get, side_effect):
    mock_get.side_effect = side_effect
    head_sha = "6434d09971" * 4
    edge_sha = "cd66c8a238" * 4

    recipe = mock.Mock(
        git_ref=mock.Mock(commit_sha1=head_sha),
        builds=[
            _mock_build(
                "amd64",
                "Successfully built",
                revision_id=edge_sha,
                store_upload_revision=5901,
            )
        ],
    )

    assert lp.edge_is_stale(recipe, arch="amd64", edge_revision=5901) is True


@mock.patch("util.gh.requests.get")
def test_edge_is_stale_identical_sha_does_not_call_api(mock_get):
    head_sha = "6434d09971" * 4

    recipe = mock.Mock(
        git_ref=mock.Mock(commit_sha1=head_sha),
        builds=[
            _mock_build(
                "amd64",
                "Successfully built",
                revision_id=head_sha,
                store_upload_revision=5901,
            )
        ],
    )

    assert lp.edge_is_stale(recipe, arch="amd64", edge_revision=5901) is False
    mock_get.assert_not_called()


@mock.patch("util.gh.requests.get")
def test_edge_is_stale_missing_edge_sha(mock_get):
    head_sha = "6434d09971" * 4

    recipe = mock.Mock(
        git_ref=mock.Mock(commit_sha1=head_sha),
        builds=[
            _mock_build(
                "amd64",
                "Successfully built",
                revision_id=None,
                store_upload_revision=5901,
            )
        ],
    )

    assert lp.edge_is_stale(recipe, arch="amd64", edge_revision=5901) is True
    mock_get.assert_not_called()
