import base64
import hashlib
import json
import logging
import os
import subprocess
from collections import defaultdict
from typing import Dict, List

import requests

LOG = logging.getLogger(__name__)

INFO_URL = "https://api.charmhub.io/v1/charm/k8s/releases"

# Timeout for Store API request in seconds
TIMEOUT = 10


class RevisionMatrix:
    """
    For each tuple of (name, channel, arch, base) there is a unique charm artifact
    in Charmhub. RevisionMatrix is a matrix of (arch, base) revisions if any for a 
    specific (name, channel) tuple.
    Rows of the matrix correspond to different architectures.
    Columns of the matrix correspond to different bases.
    """
    def __init__(self):
        self.data = defaultdict(dict)

    def set(self, arch, base, revision):
        self.data[arch][base] = revision

    def get_archs(self):
        return list(self.data.keys())

    def get_bases(self):
        bases = set()
        for base in self.data.values():
            bases.update(base.keys())
        return bases

    def get(self, arch, base):
        return self.data.get(arch, {}).get(base, None)

    def remove_arch(self, arch):
        if arch in self.data:
            del self.data[arch]

    def remove_base(self, base):
        for arch in list(self.data):
            if base in self.data[arch]:
                del self.data[arch][base]

    def __eq__(self, other):
        if not isinstance(other, RevisionMatrix):
            return NotImplemented
        return dict(self.data) == dict(other.data)

    def __str__(self):
        archs = sorted(self.data)
        bases = sorted({c for r in self.data.values() for c in r})
        result = ["\t" + "\t".join(bases)]
        for a in archs:
            line = [a] + [str(self.data[a].get(c, "")) for c in bases]
            result.append("\t".join(line))
        return "\n".join(result)


def get_channel_version_string(channel: str) -> str:
    """Get the version string for a given channel."""

    k8s_version = get_charm_channel_hashes("k8s", channel)
    k8s_worker_version = get_charm_channel_hashes("k8s-worker", channel)

    return f"k8s-operator-{channel}-{k8s_version}-{k8s_worker_version}"


def get_charm_channel_hashes(charm_name: str, channel: str) -> str:
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
    creds_export_data = os.getenv("CHARMCRAFT_AUTH")
    if not creds_export_data:
        raise ValueError("Missing charmhub credentials,")

    str_data = base64.b64decode(creds_export_data).decode()
    auth = json.loads(str(str_data))
    v = auth.get("v")
    if not v:
        raise ValueError("Malformed charmhub credentials")
    return v


def get_revision_matrix(charm_name: str, channel: str) -> RevisionMatrix:
    """Get the revision of a charm in a channel."""
    auth_macaroon = get_charmhub_auth_macaroon()
    headers = {
        "Authorization": f"Macaroon {auth_macaroon}",
        "Content-Type": "application/json",
    }
    print(f"Querying Charmhub for to get revisions of {charm_name} in {channel}...")
    r = requests.get(INFO_URL, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()

    data = json.loads(r.text)
    print("Search for charm revisions in channel list...")

    revision_matrix=RevisionMatrix()
    for channel_map in data.get("channel-map", []):
        if channel == channel_map["channel"]:
            revision_matrix.set(
                channel_map["base"]["architecture"], 
                channel_map["base"]["base"],
                int(channel_map["revision"])
                )
            

    return revision_matrix


def promote_charm(charm_name, from_channel, to_channel):
    """Promote a charm from one channel to another."""
    subprocess.run([
        "charmcraft", "promote", charm_name, f"{from_channel}", f"{to_channel}"
    ], check=True)
