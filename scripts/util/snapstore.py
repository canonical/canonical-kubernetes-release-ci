import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)
INFO_URL = "https://api.snapcraft.io/v2/snaps/info/"
PROMOTE_URL = "https://dashboard.snapcraft.io/dev/api/snap-release"
# Headers for Snap Store API request
HEADERS = {
    "Snap-Device-Series": "16",
    "User-Agent": "Mozilla/5.0",
}


def info(snap_name):
    req = Request(INFO_URL + snap_name, headers=HEADERS)
    try:
        with urlopen(req) as response:  # nosec
            return json.loads(response.read().decode())
    except HTTPError as e:
        LOG.exception("HTTPError ({%s}): {%s} {%s}", req.full_url, e.code, e.reason)
        raise
    except URLError as e:
        LOG.exception("URLError ({%s}): {%s}", req.full_url, e.reason)
        raise


def ensure_track(snap_name: str, track_name: str):
    pass
