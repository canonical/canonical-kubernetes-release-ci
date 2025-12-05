"""Utility functions for interacting with Launchpad and Snap recipes."""

import logging
import os
from configparser import ConfigParser
from functools import cache

from launchpadlib.launchpad import Launchpad, PersonSet
from lazr.restfulclient.errors import NotFound

OWNER: str = "containers"


@cache
def client():
    """Use launchpad credentials to interact with launchpad."""
    cred_file = os.getenv("LPCREDS")
    creds_local = os.getenv("LPLOCAL")
    anon_login = os.getenv("LPANON", "").lower() in ("1", "true")
    if cred_file:
        parser = ConfigParser()
        parser.read(cred_file)
        return Launchpad.login_with(
            application_name=parser["1"]["consumer_key"],
            service_root="production",
            version="devel",
            credentials_file=cred_file,
        )
    elif creds_local:
        return Launchpad.login_with(
            "localhost",
            "production",
            version="devel",
        )
    elif anon_login:
        # Anonymous login, can be used for dry-runs.
        return Launchpad.login_anonymously(
            "Canonical K8s promotion dry-run", "production", version="devel"
        )
    else:
        raise ValueError("No launchpad credentials found")


def snap_recipe(owner: PersonSet, name: str):
    """Return the recipe object for a given owner and name."""
    lp_client = client()
    try:
        lp_client.snaps.getByName(owner=owner, name=name)
    except NotFound:
        LOG.info("    Recipe %s not found for owner %s", name, owner.name)
        return None


def snap_by_owner(snap: str):
    """Return the owner object for a given owner name."""
    lp_client = client()
    return lp_client.snaps.findByStoreName(owner=lp_client.people[OWNER], store_name=snap)


def branch_from_track(snap, track):
    """Return the branch name for a given track."""
    for recipe in snap_by_owner(snap):
        if any(chan.split("/")[0] == track for chan in recipe.store_channels):
            return recipe.git_ref_link.split("+ref/")[1]


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)
