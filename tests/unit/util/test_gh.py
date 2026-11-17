import unittest.mock as mock

import pytest
import requests
import util.gh as gh


def test_request_headers_with_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-secret-token")
    assert gh._request_headers() == {"Authorization": "Bearer test-secret-token"}


def test_request_headers_without_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert gh._request_headers() == {}


def test_is_ancestor_identical_shas_skips_network():
    with mock.patch("util.gh.requests.get") as mock_get:
        assert gh.is_ancestor("abc1234", "abc1234") is True
        mock_get.assert_not_called()


@pytest.mark.parametrize(
    "ancestor, descendant",
    [
        ("", "abc1234"),
        ("abc1234", ""),
        (None, "abc1234"),
        ("abc1234", None),
    ],
)
def test_is_ancestor_missing_sha(ancestor, descendant):
    with mock.patch("util.gh.requests.get") as mock_get:
        assert gh.is_ancestor(ancestor, descendant) is False
        mock_get.assert_not_called()


@pytest.mark.parametrize(
    "status, expected",
    [
        ("ahead", True),
        ("identical", True),
        ("behind", False),
        ("diverged", False),
        ("unknown", False),
    ],
)
@mock.patch("util.gh.requests.get")
def test_is_ancestor_compare_status(mock_get, status, expected):
    mock_resp = mock.Mock()
    mock_resp.json.return_value = {"status": status}
    mock_get.return_value = mock_resp

    assert gh.is_ancestor("sha-base", "sha-head") is expected
    mock_get.assert_called_once_with(
        "https://api.github.com/repos/canonical/k8s-snap/compare/sha-base...sha-head",
        headers={},
        timeout=10,
    )


@pytest.mark.parametrize(
    "side_effect",
    [
        requests.HTTPError("404 Client Error"),
        requests.ConnectionError("Connection refused"),
        requests.Timeout("Timeout"),
        ValueError("Malformed JSON"),
    ],
)
@mock.patch("util.gh.requests.get")
def test_is_ancestor_api_error_returns_false(mock_get, side_effect):
    mock_get.side_effect = side_effect
    assert gh.is_ancestor("sha-base", "sha-head") is False


@pytest.mark.parametrize("payload", [None, [], "ahead", 123])
@mock.patch("util.gh.requests.get")
def test_is_ancestor_non_dict_response_returns_false(mock_get, payload):
    mock_resp = mock.Mock()
    mock_resp.json.return_value = payload
    mock_get.return_value = mock_resp
    assert gh.is_ancestor("sha-base", "sha-head") is False


@mock.patch("util.gh.requests.get")
def test_is_ancestor_custom_repo(mock_get):
    mock_resp = mock.Mock()
    mock_resp.json.return_value = {"status": "ahead"}
    mock_get.return_value = mock_resp

    assert gh.is_ancestor("sha-base", "sha-head", repo="custom/my-snap") is True
    mock_get.assert_called_once_with(
        "https://api.github.com/repos/custom/my-snap/compare/sha-base...sha-head",
        headers={},
        timeout=10,
    )
