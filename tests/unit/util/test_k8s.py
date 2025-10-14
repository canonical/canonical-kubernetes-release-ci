import json
from unittest.mock import patch

import pytest
from util.k8s import (
    get_k8s_tags,
    get_latest_releases_by_minor,
    get_latest_stable,
    is_stable_release,
)

SAMPLE_TAGS = [
    {"name": "v1.33.0-alpha.0"},
    {"name": "v1.32.0-rc.0"},
    {"name": "v1.31.6"},
    {"name": "v1.31.5"},
    {"name": "v1.30.9"},
    {"name": "v1.29.10"},
]


@patch("util.k8s._url_get")
def test_get_k8s_tags(mock_url_get):
    mock_url_get.return_value = json.dumps(SAMPLE_TAGS)
    tags = get_k8s_tags()
    assert tags == [
        "v1.33.0-alpha.0",
        "v1.32.0-rc.0",
        "v1.31.6",
        "v1.31.5",
        "v1.30.9",
        "v1.29.10",
    ]


@patch("util.k8s._url_get")
def test_get_latest_stable(mock_url_get):
    mock_url_get.return_value = json.dumps(SAMPLE_TAGS)
    latest_stable = get_latest_stable()
    assert latest_stable == "v1.31.6"


@patch("util.k8s._url_get")
def test_get_latest_releases_by_minor(mock_url_get):
    mock_url_get.return_value = json.dumps(SAMPLE_TAGS)
    by_minor = get_latest_releases_by_minor()
    assert by_minor == {
        "1.33": "v1.33.0-alpha.0",
        "1.32": "v1.32.0-rc.0",
        "1.31": "v1.31.6",
        "1.30": "v1.30.9",
        "1.29": "v1.29.10",
    }


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.31.6", True),
        ("v1.31.0-beta.1", False),
        ("v1.32.0-rc.0", False),
        ("v1.33.0-alpha.0", False),
    ],
)
def test_is_stable_release(tag, expected):
    assert is_stable_release(tag) == expected
