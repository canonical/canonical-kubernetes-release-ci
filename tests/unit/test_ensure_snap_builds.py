import unittest.mock as mock

import ensure_snap_builds
import semver
from lazr.restfulclient.errors import NotFound


@mock.patch("ensure_snap_builds.snapstore.ensure_track")
def test_ensure_snap_channels_strict_release_uses_hidden_edge_branch(mock_ensure_track):
    ver = semver.Version.parse("1.38.0")
    channels = ensure_snap_builds.ensure_snap_channels("strict", ver, tip=False, dry_run=False)
    assert channels == ["1.38-classic/edge/strict"]
    mock_ensure_track.assert_called_once_with("k8s", "1.38-classic")


@mock.patch("ensure_snap_builds.snapstore.ensure_track")
def test_ensure_snap_channels_strict_tip_uses_latest_edge_branch(mock_ensure_track):
    ver = semver.Version.parse("1.38.0")
    channels = ensure_snap_builds.ensure_snap_channels("strict", ver, tip=True, dry_run=False)
    assert channels == ["latest/edge/strict"]
    mock_ensure_track.assert_called_once_with("k8s", "latest")


@mock.patch("ensure_snap_builds.snapstore.ensure_track")
def test_ensure_snap_channels_classic_release(mock_ensure_track):
    ver = semver.Version.parse("1.38.0")
    channels = ensure_snap_builds.ensure_snap_channels("classic", ver, tip=False, dry_run=False)
    assert channels == ["1.38-classic/edge"]
    mock_ensure_track.assert_called_once_with("k8s", "1.38-classic")


@mock.patch("ensure_snap_builds.lp.client")
def test_ensure_lp_recipe_strict_enables_upload_to_hidden_edge_branch(mock_lp_client):
    client = mock.MagicMock()
    mock_lp_client.return_value = client
    client.snaps.getByName.side_effect = NotFound(mock.MagicMock(), mock.MagicMock())
    created_recipe = mock.MagicMock()
    created_recipe.processors = []
    client.snaps.new.return_value = created_recipe

    ver = semver.Version.parse("1.38.0")
    recipe_name = ensure_snap_builds.ensure_lp_recipe(
        "strict", ver, ["1.38-classic/edge/strict"], tip=False, dry_run=False
    )

    assert recipe_name == "k8s-snap-1.38-strict"
    lp_repo = client.git_repositories.getDefaultRepository.return_value
    lp_repo.getRefByPath.assert_called_once_with(path="autoupdate/release-1.38-strict")
    _, kwargs = client.snaps.new.call_args
    assert kwargs["auto_build"] is True
    assert kwargs["store_upload"] is True
    assert kwargs["store_channels"] == ["1.38-classic/edge/strict"]
    assert kwargs["git_ref"] == lp_repo.getRefByPath.return_value


@mock.patch("ensure_snap_builds.lp.client")
def test_ensure_lp_recipe_strict_tip_strips_latest_prefix(mock_lp_client):
    client = mock.MagicMock()
    mock_lp_client.return_value = client
    client.snaps.getByName.side_effect = NotFound(mock.MagicMock(), mock.MagicMock())
    created_recipe = mock.MagicMock()
    created_recipe.processors = []
    client.snaps.new.return_value = created_recipe

    ver = semver.Version.parse("1.38.0")
    recipe_name = ensure_snap_builds.ensure_lp_recipe(
        "strict", ver, ["latest/edge/strict"], tip=True, dry_run=False
    )

    assert recipe_name == "k8s-snap-tip-strict"
    lp_repo = client.git_repositories.getDefaultRepository.return_value
    lp_repo.getRefByPath.assert_called_once_with(path="autoupdate/strict")
    _, kwargs = client.snaps.new.call_args
    assert kwargs["auto_build"] is True
    assert kwargs["store_upload"] is True
    assert kwargs["store_channels"] == ["edge/strict"]
    assert kwargs["git_ref"] == lp_repo.getRefByPath.return_value
