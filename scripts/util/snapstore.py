import base64
import json
import logging
import os

import requests

LOG = logging.getLogger(__name__)
INFO_URL = "https://api.snapcraft.io/v2/snaps/info/"
PROMOTE_URL = "https://dashboard.snapcraft.io/dev/api/snap-release"
# Headers for Snap Store API request
HEADERS = {
    "Snap-Device-Series": "16",
    "User-Agent": "Mozilla/5.0",
}
TIMEOUT = 10


def info(snap_name):
    r = requests.get(INFO_URL + snap_name, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return json.loads(r.text)


def track_exists(snap_name: str, track_name: str) -> bool:
    """Check if a track exists for a snap."""
    snap_info = info(snap_name)
    for channel_data in snap_info.get("channel-map", {}):
        track = channel_data.get("channel", {}).get("track")
        if track and track == track_name:
            return True
    return False


def ensure_track(snap_name: str, track_name: str) -> None:
    """Ensure a track exists for a snap. If it does not exist, create it."""
    LOG.info("Ensuring track: %s %s", snap_name, track_name)
    if not track_exists(snap_name, track_name):
        create_track(snap_name, track_name)
    else:
        LOG.info("Track already exists: %s %s", snap_name, track_name)


def create_track(snap_name: str, track_name: str) -> None:
    """Create a track for a snap. Throws an exception if the track already exists."""
    LOG.info("Creating track: %s %s", snap_name, track_name)

    # Yes, the snap creation API is really at charmhub.io.
    # See https://juju.is/docs/sdk/create-a-track-for-your-charm#heading--self-service
    # For obvious reasons, we will keep this function in the snapstore module regardless.
    url = f"https://api.charmhub.io/v1/snap/{snap_name}/tracks"
    auth_macaroon = get_charmhub_auth_macaroon()
    headers = {
        "Authorization": f"Macaroon {auth_macaroon}",
        "Content-Type": "application/json",
    }
    data = [{"name": track_name}]
    r = requests.post(url, headers=headers, json=data, timeout=TIMEOUT)
    r.raise_for_status()


def get_charmhub_auth_macaroon() -> str:
    """Get the charmhub macaroon from the environment.

    This is used to authenticate with the charmhub API.
    Will raise a ValueError if CHARMCRAFT_AUTH is not set or the credentials are malformed.
    """
    # Auth credentials provided by "charmcraft login --export $outfile"
    creds_export_data = os.getenv("CHARMCRAFT_AUTH")
    if not creds_export_data:
        raise ValueError("Missing charmhub credentials,")

    str_data = base64.b64decode(creds_export_data).decode()
    auth = json.loads(str(str_data))
    v = auth.get("v")
    if not v:
        raise ValueError("Malformed charmhub credentials")
    return v
