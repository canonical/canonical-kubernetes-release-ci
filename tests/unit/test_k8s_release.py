# test_prerelease_tool.py

from unittest.mock import patch

import pytest

import scripts.k8s_release as k8s_release


@patch("util.k8s.get_latest_releases_by_minor")
def test_get_outstanding_prereleases(mock_latest):
    mock_latest.return_value = {
        "1.31": "v1.31.6",
        "1.32": "v1.32.0-rc.0",
        "1.33": "v1.33.0-alpha.0",
    }

    result = k8s_release.get_outstanding_prereleases()
    assert result == ["v1.32.0-rc.0", "v1.33.0-alpha.0"]


@patch("util.k8s.get_k8s_tags")
def test_get_obsolete_prereleases_single_valid_prerelease(mock_tags):
    # Simulate a leading pre-release followed by stable and more tags
    mock_tags.return_value = [
        "v1.33.0-alpha.0",  # valid pre-release
        "v1.32.0",  # first stable
        "v1.32.0-rc.0",
        "v1.31.5",
        "v1.31.0-beta.1",
    ]

    result = k8s_release.get_obsolete_prereleases()
    assert result == ["v1.32.0-rc.0", "v1.31.0-beta.1"]


@patch("util.k8s.get_k8s_tags")
def test_get_obsolete_prereleases_multiple_valid_prerelease(mock_tags):
    # Simulate two valid pre-releases
    mock_tags.return_value = [
        "v1.33.0-alpha.0",  # valid pre-release
        "v1.32.0-rc.0",  # valid pre-release
        "v1.31.5",  # first stable
        "v1.31.0-beta.1",
    ]

    result = k8s_release.get_obsolete_prereleases()
    assert result == ["v1.31.0-beta.1"]


@patch("util.k8s.get_k8s_tags")
@patch("util.k8s.is_stable_release")
def test_get_obsolete_prereleases_starts_with_stable(mock_is_stable, mock_tags):
    mock_tags.return_value = [
        "v1.32.0",
        "v1.32.0-rc.0",
        "v1.31.5",
        "v1.31.0-alpha.1",
    ]
    mock_is_stable.side_effect = lambda tag: "-" not in tag

    result = k8s_release.get_obsolete_prereleases()
    assert result == ["v1.32.0-rc.0", "v1.31.0-alpha.1"]


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.33.0-alpha.0", "autoupdate/v1.33.0-alpha"),
        ("v1.32.1-beta.3", "autoupdate/v1.32.1-beta"),
        ("v1.30.2-rc.1", "autoupdate/v1.30.2-rc"),
        ("v1.30.2-rc.1", "autoupdate/v1.30.2-rc"),
    ],
)
def test_get_prereleases_git_branch_valid(tag, expected):
    assert k8s_release.get_prerelease_git_branch(tag) == expected


@pytest.mark.parametrize(
    "invalid_tag",
    [
        "v1.33.0",
        "v1.33.0alpha.0",
        "v1.33.0-stable.1",
        "1.33.0-alpha.0",
    ],
)
def test_get_prereleases_git_branch_invalid(invalid_tag):
    with pytest.raises(ValueError):
        k8s_release.get_prerelease_git_branch(invalid_tag)
