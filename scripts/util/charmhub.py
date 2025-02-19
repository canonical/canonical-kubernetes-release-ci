import base64
import json
import logging
import os
import subprocess
import requests
import hashlib
from typing import List, Dict, Optional

LOG = logging.getLogger(__name__)
# TODO: Do not
INFO_URL = "https://api.charmhub.io/v1/charm/k8s/releases"
PROMOTE_URL = "https://dashboard.snapcraft.io/dev/api/snap-release"
# Headers for Snap Store API request
HEADERS = {
    "Snap-Device-Series": "16",
    "User-Agent": "Mozilla/5.0",
}
# Timeout for Store API request in seconds
TIMEOUT = 10

def get_channel_version_string(channel: str) -> str:
    """Get the version string for a given channel."""

    k8s_version = get_charm_channel_hashes("k8s", channel)
    k8s_worker_version = get_charm_channel_hashes("k8s-worker", channel)

    return f"k8s-operator-{channel}-{k8s_version}-{k8s_worker_version}"


def get_charm_channel_hashes(charm_name: str, channel: str) -> dict:
    """
    Queries Charmhub for the current state of all tracks of a given charm and returns
    a dictionary mapping tracks to their SHA256 hash.

    :param charm_name: The name of the charm to query.
    :param track: The track to query.
    :return: A dictionary mapping tracks to their SHA256 hash.
    """
    auth_macaroon = get_charmhub_auth_macaroon()
    headers = {
        "Authorization": f"Macaroon {auth_macaroon}",
        "Content-Type": "application/json",
    }
    print(f"Querying Charmhub for track hashes of {charm_name}...")
    r = requests.get(INFO_URL, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()

    data = json.loads(r.text)
    channel_state = []

    print("Calculating track hashes...")
    for channel_map in data.get("channel-map", []):
        if channel != channel_map["channel"]:
            continue

        channel_state.append(channel_map)

    return calculate_channel_sha256(channel_state)

def calculate_channel_sha256(channel_revisions: List[Dict[str, any]]) -> str:
    """
    Calculates the SHA256 hash of a charm channel.

    :param charm_revisions: A list of revisions, where each revision is a dictionary
                       containing 'architecture', 'bases', and 'sha256'.
    :return: The SHA256 hash of the entire track.
    """
    # Sort revisions to ensure consistent hashing
    sorted_revisions = sorted(channel_revisions, key=lambda rev: rev["when"])

    # Create a normalized representation
    channel_data = json.dumps(sorted_revisions, sort_keys=True).encode()

    return hashlib.sha256(channel_data).hexdigest()

def get_charmhub_auth_macaroon() -> str:
    """Get the charmhub macaroon from the environment.

    This is used to authenticate with the charmhub API.
    Will raise a ValueError if CHARMCRAFT_AUTH is not set or the credentials are malformed.
    """
    # Auth credentials provided by "charmcraft login --export $outfile"
    # TODO: Remove hardcoded credentials
    creds_export_data = "eyJ0IjogIm1hY2Fyb29uIiwgInYiOiAiQWdFUVlYQnBMbk51WVhCamNtRm1kQzVwYndJbUF3b1F4ZW1hS0EzVTd5N3NFR0RQUTNBaXd4SUJNQm9PQ2dWc2IyZHBiaElGYkc5bmFXNEFBaWQwYVcxbExXSmxabTl5WlNBeU1ESTFMVEF5TFRBMVZERTRPakkzT2pBMExqWXpNakUyT1ZvQUFpWjBhVzFsTFhOcGJtTmxJREl3TWpVdE1ESXRNRFJVTVRJNk1qYzZNRFF1TmpNeU1UWTVXZ0FDTDNObGMzTnBiMjR0YVdRZ05UQTVORFF5TlRjdE16WTRaaTAwWm1Nd0xUZzJZVEF0WXpnd1lqWTBOMlUzT0dKakFBSTVaR1ZqYkdGeVpXUWdkWE5sY21sa0lIVnpjMjg2YUhSMGNITTZMeTlzYjJkcGJpNTFZblZ1ZEhVdVkyOXRMeXRwWkM5WVRUZG1kMlZtQUFKdVpYaDBjbUVnZXlKd1pYSnRhWE56YVc5dWN5STZJRnNpWVdOamIzVnVkQzF5WldkcGMzUmxjaTF3WVdOcllXZGxJaXdnSW1GalkyOTFiblF0ZG1sbGR5MXdZV05yWVdkbGN5SXNJQ0p3WVdOcllXZGxMVzFoYm1GblpTSXNJQ0p3WVdOcllXZGxMWFpwWlhjaVhYMEFBQVlnNm5DSWJVZ2hHdGVHRUlxREhjcERMTlVjRDQyTC0yTWJIUW0wYWFBY2VRTSJ9" #os.getenv("CHARMCRAFT_AUTH")
    if not creds_export_data:
        raise ValueError("Missing charmhub credentials,")

    str_data = base64.b64decode(creds_export_data).decode()
    auth = json.loads(str(str_data))
    v = auth.get("v")
    if not v:
        raise ValueError("Malformed charmhub credentials")
    return v

def get_charm_revision(charm_name: str, channel: str, arch: str) -> Optional[int]:
    """Get the revision of a charm in a channel."""
    auth_macaroon = get_charmhub_auth_macaroon()
    headers = {
        "Authorization": f"Macaroon {auth_macaroon}",
        "Content-Type": "application/json",
    }
    print(f"Querying Charmhub for to get revision of {charm_name} in {channel}/{arch}...")
    r = requests.get(INFO_URL, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()

    data = json.loads(r.text)

    print("Search for charm revision in channel list...")
    for channel_map in data.get("channel-map", []):
        if channel == channel_map["channel"] and arch == channel_map["architecture"]:
            return channel_map["revision"]

    return None

def get_supported_archs(charm_name: str) -> List[str]:
    """Get the supported architectures for a charm."""
    # TODO Implement call to charmhub API and get this information from the channel map
    return ["amd64"]

def promote_charm(charm_name, from_channel, to_channel):
    """Promote a charm from one channel to another."""
    subprocess.run([
        "charmcraft", "promote", charm_name, f"{from_channel}", f"{to_channel}"
    ], check=True)
