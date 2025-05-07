import base64
import json
import logging
import os
import subprocess
import random
from collections import defaultdict

import requests

LOG = logging.getLogger(__name__)

# Timeout for Store API request in seconds
TIMEOUT = 10

class Bundle:
    """
    Bundle defines a set of charms that need to be tested together. 
    For example k8s-operator bundle consists of two charms, namely,
    k8s and k8s-worker charms
    """
    
    def __init__(self):
        self.data: defaultdict[str, RevisionMatrix] = defaultdict(dict)

    def set(self, charm, revision_matrix):
        self.data[charm] = revision_matrix

    def is_testable(self):
        if not len(self.data) or any(matrix is None for matrix in self.data.values()):
            return False
        
        # All the matrices in a bundle must have the same span of arch and bases 
        # and have a revision values for each (arch, base) so that they can be 
        # tested alongside each other.
        item: RevisionMatrix = random.choice(list(self.data.values()))
        
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
        try:
            item: RevisionMatrix = random.choice(list(self.data.values()))
            return item.get_bases()
        except StopIteration:
            return set()
        
    def get_archs(self):
        try:
            item: RevisionMatrix = random.choice(list(self.data.values()))
            return item.get_archs()
        except StopIteration:
            return set()
    
    def get_revisions(self, arch, base):
        revisions = {}

        for charm in self.data.keys():
            revisions[f"{charm.replace("-", "_")}_revision"] = self.data[charm].get(arch, base)

        return revisions

    def get_version(self, arch, base):
        
        charms = sorted(self.data.keys())
        if not charms:
            return None

        version = "k8s-operator"
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
    """
    For each tuple of (name, channel, arch, base) there is a unique charm artifact
    in Charmhub. RevisionMatrix is a matrix of (arch, base) revisions, if any, for
    a specific (name, channel) tuple.
    Rows of the matrix correspond to different architectures.
    Columns of the matrix correspond to different bases.
    """

    def __init__(self):
        self.data: defaultdict[str, dict[str, str]] = defaultdict(dict)

    def set(self, arch, base, revision):
        self.data[arch][base] = revision

    def get_archs(self):
        return set(self.data.keys())

    def get_bases(self):
        bases: set[str] = set()
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

    def __bool__(self):
        return all(value is not None for value in self.data.values())

    def __str__(self):
        archs = sorted(self.data)
        bases = sorted({c for r in self.data.values() for c in r})
        result = ["\t" + "\t".join(bases)]
        for a in archs:
            line = [a] + [str(self.data[a].get(c, "")) for c in bases]
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

def get_revision_matrix(charm_name: str, channel: str) -> RevisionMatrix:
    """Get the revision of a charm in a channel."""
    headers = {
        "Content-Type": "application/json",
    }
    print(f"Querying Charmhub to get revisions of {charm_name} in {channel}...")
    
    url = f"https://api.charmhub.io/v2/charms/info/{charm_name}?fields=channel-map"
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()

    data = json.loads(r.text)
    print("Search for charm revisions in channel list...")

    revision_matrix = RevisionMatrix()
    for channel_map in data.get("channel-map", []):
        
        if channel == channel_map["channel"]["name"]:
            revision_matrix.set(
                channel_map["channel"]["base"]["architecture"],
                channel_map["channel"]["base"]["channel"],
                int(channel_map["revision"]["revision"]),
            )

    return revision_matrix


def promote_charm(charm_name, from_channel, to_channel):
    """Promote a charm from one channel to another."""
    subprocess.run(
        ["/snap/bin/charmcraft", "promote", charm_name, from_channel, to_channel],
        check=True,
    )
