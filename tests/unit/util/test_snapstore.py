import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
import util.snapstore as snapstore


@patch("util.snapstore.urlopen")
def test_info_success(mock_urlopen):
    # Mock the response from urlopen
    context = mock_urlopen.return_value.__enter__.return_value
    context.read.return_value = json.dumps({"name": "test-snap"}).encode("utf-8")

    result = snapstore.info("test-snap")
    assert result == {"name": "test-snap"}
    mock_urlopen.assert_called_once()


@patch("util.snapstore.urlopen")
def test_info_http_error(mock_urlopen):
    # Mock an HTTPError
    mock_urlopen.side_effect = HTTPError(
        url="http://test-url",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore
        fp=None,
    )

    with pytest.raises(HTTPError):
        snapstore.info("non-existent-snap")


@patch("util.snapstore.urlopen")
def test_info_url_error(mock_urlopen):
    # Mock a URLError
    mock_urlopen.side_effect = URLError("Reason")

    with pytest.raises(URLError):
        snapstore.info("non-existent-snap")


def test_ensure_track():
    # Placeholder for ensure_track tests
    snapstore.ensure_track("test-snap", "test-track")
