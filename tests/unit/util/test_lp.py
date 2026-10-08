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
