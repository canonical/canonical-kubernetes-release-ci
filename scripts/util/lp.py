import os
from configparser import ConfigParser
from functools import cache

from launchpadlib.launchpad import Launchpad


@cache
def client():
    """Use launchpad credentials to interact with launchpad."""
    cred_file = os.environ.get("LPCREDS", None)
    creds_local = os.environ.get("LPLOCAL", None)
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
    else:
        raise ValueError("No launchpad credentials found")
