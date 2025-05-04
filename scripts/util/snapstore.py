import json
import logging

from . import charmhub
import requests

LOG = logging.getLogger(__name__)
INFO_URL = "https://api.snapcraft.io/v2/snaps/info/"
PROMOTE_URL = "https://dashboard.snapcraft.io/dev/api/snap-release"
# Headers for Snap Store API request
HEADERS = {
    "Snap-Device-Series": "16",
    "User-Agent": "Mozilla/5.0",
}
# Timeout for Store API request in seconds
TIMEOUT = 10


def info(snap_name):
    r = requests.get(INFO_URL + snap_name, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return json.loads(r.text)


def ensure_track(snap_name: str, track_name: str) -> None:
    """Ensure a track exists for a snap.

    The snap info does not contain non-populated tracks, so we need to
    just try to create the track. If it already exists, we will get a
    409 Conflict error, which we will ignore.
    """
    LOG.info("Ensuring track: %s %s", snap_name, track_name)
    try:
        create_track(snap_name, track_name)
        LOG.info("Track created: %s %s", snap_name, track_name)
    except requests.HTTPError as e:
        if e.response.status_code == 409:
            # Track already exists
            LOG.info("Track %s already exists for snap %s", track_name, snap_name)
        else:
            raise


def create_track(snap_name: str, track_name: str) -> None:
    """Create a track for a snap. Throws an exception if the track already exists."""
    # Yes, the snap creation API is really at charmhub.io.
    # See https://juju.is/docs/sdk/create-a-track-for-your-charm#heading--self-service
    # For obvious reasons, we will keep this function in the snapstore module regardless.
    url = f"https://api.charmhub.io/v1/snap/{snap_name}/tracks"
    auth_macaroon = charmhub.get_charmhub_auth_macaroon()
    headers = {
        "Authorization": f"Macaroon {auth_macaroon}",
        "Content-Type": "application/json",
    }
    data = [{"name": track_name}]
    r = requests.post(url, headers=headers, json=data, timeout=TIMEOUT)
    r.raise_for_status()
