"""Utility functions for interacting with Charmhub and managing charm revisions."""

import base64
import json
import logging
import os
import random
import subprocess
from collections import defaultdict

import requests

log = logging.getLogger(__name__)

# Timeout for Store API request in seconds
TIMEOUT = 10


class CharmcraftError(Exception):
    """Exception raised when a charmcraft command fails."""

    pass


class Bundle:
    """Bundle defines a set of charms that need to be tested together.

    For example k8s-operator bundle consists of two charms, namely,
    k8s and k8s-worker charms
    """

    def __init__(self, name):
        """Initialize a Bundle with a name and an empty revision matrix."""
        self.data: defaultdict[str, RevisionMatrix] = defaultdict(None)
        self.name = name

    def set(self, charm, revision_matrix):
        """Set the revision matrix for a charm in the bundle."""
        self.data[charm] = revision_matrix

    def get(self, charm):
        """Get the revision matrix for a charm in the bundle."""
        return self.data[charm]

    def is_testable(self):
        """Check if the bundle is testable."""
        if not len(self.data) or any(matrix is None for matrix in self.data.values()):
            return False

        # All the matrices in a bundle must have the same span of arch and bases
        # and have a revision values for each (arch, base) so that they can be
        # tested alongside each other.
        item: RevisionMatrix = random.choice(list(self.data.values()))  # nosec

        bases = item.get_bases()
        archs = item.get_archs()

        for revision_matrix in self.data.values():
            if revision_matrix.get_bases() != bases or revision_matrix.get_archs() != archs:
                return False

            for base in bases:
                for arch in archs:
                    if item.get(arch, base) and not revision_matrix.get(arch, base):
                        return False

        return True

    def get_bases(self):
        """Get the unique bases in the bundle."""
        try:
            item: RevisionMatrix = random.choice(list(self.data.values()))  # nosec
            return item.get_bases()
        except StopIteration:
            return set()

    def get_archs(self):
        """Get the unique architectures in the bundle."""
        try:
            item: RevisionMatrix = random.choice(list(self.data.values()))  # nosec
            return item.get_archs()
        except StopIteration:
            return set()

    def get_revisions(self, arch, base):
        """Get the revisions for the bundle based on the architecture and base."""
        revisions = {}

        for charm in self.data.keys():
            revisions[f"{charm.replace('-', '_')}_revision"] = self.data[charm].get(arch, base)

        return revisions

    def get_version(self, arch, base):
        """Get the version string for the bundle based on the revisions."""
        charms = sorted(self.data.keys())
        if not charms:
            return None

        version = self.name
        for charm in charms:
            revision_matrix = self.data[charm]
            if not revision_matrix:
                return None

            revision = revision_matrix.get(arch, base)
            if not revision:
                return None

            version += f"-{charm}-{revision}"

        return version


class RevisionMatrix:
    """RevisionMatrix is a matrix of revisions for a charm in a channel.

    For each tuple of (name, channel, arch, base) there is a unique charm artifact
    in Charmhub. RevisionMatrix is a matrix of (arch, base) revisions, if any, for
    a specific (name, channel) tuple.
    Rows of the matrix correspond to different architectures.
    Columns of the matrix correspond to different bases.
    """

    def __init__(self):
        """Initialize an empty RevisionMatrix."""
        self.data: defaultdict[tuple[str, str], str] = defaultdict(str)

    def set(self, arch, base, revision):
        """Set the revision for a specific architecture and base."""
        self.data[(arch, base)] = revision

    def get_archs(self):
        """Get the unique architectures in the matrix."""
        return {k[0] for k in self.data.keys()}

    def get_bases(self):
        """Get the unique bases in the matrix."""
        return {k[1] for k in self.data.keys()}

    def get(self, arch, base):
        """Get the revision for a specific architecture and base."""
        return self.data.get((arch, base))

    def __eq__(self, other):
        """Check if two RevisionMatrix instances are equal."""
        return dict(self.data) == dict(other.data)

    def __bool__(self):
        """Return True if the matrix has at least one entry."""
        if not self.data.keys():
            return False
        return all(value is not None for value in self.data.values())

    def __str__(self):
        """Return a string representation of the matrix."""
        archs = sorted(self.get_archs())
        bases = sorted(self.get_bases())
        result = ["\t" + "\t".join(bases)]
        for a in archs:
            line = [a] + [str(self.data.get((a, b), "")) for b in bases]
            result.append("\t".join(line))
        return "\n".join(result)


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


def find_revision(charm_name: str, channel: str, arch: str, base: str) -> int | None:
    log.info(f"Querying Charmhub to get revisions of {charm_name=} {channel=} {arch=} {base=} ...")
    url = "https://api.charmhub.io/v2/charms/refresh"
    headers = {"Content-Type": "application/json"}
    data = {
        "actions": [
            {
                "action": "install",
                "base": {"architecture": arch, "channel": base, "name": "ubuntu"},
                "channel": channel,
                "name": charm_name,
                "instance-key": "query",
            }
        ],
        "context": [],
    }
    r = requests.post(url, headers=headers, json=data, timeout=TIMEOUT)
    return r.json()["results"][0]["charm"].get("revision") if r.status_code == 200 else None


def get_revision_matrix(charm_name: str, channel: str) -> RevisionMatrix:
    """Get the revision of a charm in a channel."""
    log.info(f"Querying Charmhub to get revisions of {charm_name} in {channel}...")

    revision_matrix = RevisionMatrix()
    for base in ["20.04", "22.04", "24.04", "26.04", "28.04", "30.04"]:
        for arch in ["amd64", "arm64"]:
            if revision := find_revision(charm_name, channel, arch, base):
                revision_matrix.set(arch, base, revision)

    return revision_matrix


def promote_charm(charm_name, from_channel, to_channel):
    """Promote a charm from one channel to another."""
    try:
        subprocess.run(
            ["/snap/bin/charmcraft", "promote", charm_name, from_channel, to_channel],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise CharmcraftError(f"promote charm failed: {e.stderr}")
