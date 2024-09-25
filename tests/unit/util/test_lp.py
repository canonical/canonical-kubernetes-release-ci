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
