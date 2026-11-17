import argparse
import unittest.mock as mock
from pathlib import Path

import request_builds


@mock.patch("request_builds.lp.snap_recipe")
@mock.patch("request_builds.lp.retry_failed_builds")
@mock.patch("request_builds.util.flavors", return_value=["classic"])
@mock.patch("request_builds.repo.clone")
@mock.patch("request_builds.lp.client")
def test_rebuild_branches_retry_failed_mode(
    mock_client, mock_clone, _mock_flavors, mock_retry_failed, mock_snap_recipe, tmp_path: Path
):
    ver_dir = tmp_path / "build-scripts" / "components" / "kubernetes"
    ver_dir.mkdir(parents=True)
    (ver_dir / "version").write_text("v1.35.10\n")
    mock_clone.return_value.__enter__.return_value = tmp_path

    recipe = mock.Mock()
    mock_snap_recipe.return_value = recipe

    retry_args = argparse.Namespace(dry_run=False, retry_failed=True)
    request_builds.rebuild_branches(["release-1.35"], retry_args)

    mock_retry_failed.assert_called_once_with(recipe, dry_run=False)
    recipe.requestBuilds.assert_not_called()

    mock_retry_failed.reset_mock()
    full_args = argparse.Namespace(dry_run=False, retry_failed=False)
    request_builds.rebuild_branches(["release-1.35"], full_args)

    mock_retry_failed.assert_not_called()
    recipe.requestBuilds.assert_called_once_with(
        archive=recipe.auto_build_archive,
        channels=recipe.auto_build_channels,
        pocket=recipe.auto_build_pocket,
    )
