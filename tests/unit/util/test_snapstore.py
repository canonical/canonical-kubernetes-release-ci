import base64
import json
from unittest.mock import MagicMock, patch

import pytest
import requests
import util.snapstore as snapstore


@patch("util.snapstore.requests.get")
def test_info_success(mock_get):
    # Mock the response from requests.get
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps({"name": "test-snap"})
    mock_get.return_value = mock_response

    result = snapstore.info("test-snap")
    assert result == {"name": "test-snap"}
    mock_get.assert_called_once_with(
        "https://api.snapcraft.io/v2/snaps/info/test-snap",
        headers=snapstore.HEADERS,
        timeout=snapstore.TIMEOUT,
    )


@patch("util.snapstore.requests.get")
def test_info_http_error(mock_get):
    # Mock an HTTPError
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("Not Found")
    mock_get.return_value = mock_response

    with pytest.raises(requests.HTTPError):
        snapstore.info("non-existent-snap")


@patch("util.snapstore.requests.get")
def test_info_url_error(mock_get):
    # Mock a ConnectionError (similar to URLError in urllib)
    mock_get.side_effect = requests.ConnectionError("Failed to connect")

    with pytest.raises(requests.ConnectionError):
        snapstore.info("non-existent-snap")


@patch("util.snapstore.create_track")
def test_ensure_track_create(mock_create_track):
    snapstore.ensure_track("test-snap", "test-track")
    mock_create_track.assert_called_once_with("test-snap", "test-track")


@patch("util.snapstore.requests.post")
@patch("util.snapstore.get_charmhub_auth_macaroon", return_value="mock-macaroon")
def test_create_track(mock_get_auth, mock_post):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    snapstore.create_track("test-snap", "test-track")

    mock_get_auth.assert_called_once()
    mock_post.assert_called_once_with(
        "https://api.charmhub.io/v1/snap/test-snap/tracks",
        headers={
            "Authorization": "Macaroon mock-macaroon",
            "Content-Type": "application/json",
        },
        json=[{"name": "test-track"}],
        timeout=snapstore.TIMEOUT,
    )


@patch(
    "util.snapstore.os.getenv",
    return_value=base64.b64encode(b'{"v": "mock-macaroon"}').decode(),
)
def test_get_charmhub_auth_macaroon(mock_getenv):
    result = snapstore.get_charmhub_auth_macaroon()
    assert result == "mock-macaroon"
    mock_getenv.assert_called_once_with("CHARMCRAFT_AUTH")


@patch("util.snapstore.os.getenv", return_value=None)
def test_get_charmhub_auth_macaroon_missing(mock_getenv):
    with pytest.raises(ValueError, match="Missing charmhub credentials"):
        snapstore.get_charmhub_auth_macaroon()
