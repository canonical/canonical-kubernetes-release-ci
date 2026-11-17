## Credentials necessary for the job:

### Launchpad Credentials
This job will need access from a user to manipulate and create launchpad recipes.
To do so, the GH secrets must contain a base64 encoded secret written into a file
on the builder. Generating that secret is detailed below

See team credentials storage for 'canonical-kubernetes-release-ci'

Generated with:

```python
from base64 import b64encode
from launchpadlib.credentials import Credentials
credentials = Credentials("canonical-kubernetes-release-ci")
request_token_info = credentials.get_request_token(web_root="production")
print(request_token_info)
# login with this request using the administrative account
credentials.exchange_request_token_for_access_token(web_root="production")
serialized = credentials.serialize()
print("--ini file--")
print(serialized.decode())
print("------------")
print()
print("--base64 encoded--")
print(b64encode(serialized).decode())
print("------------")
```

Write to repository secrets by taking the base64 encoded value
and pushing to `LP_CREDS`


### Charmcraft Credentials
This job will need access to the snapstore.io api, through the use of a macaroon
to access the PublisherGW API to inspect and create tracks in the charm.  

Generate these credentials using `charmcraft` for the next year

```bash
charmcraft login --export charmhub-creds.dat --ttl 31536000
cat charmhub-creds.dat
```

Write to repository secrets by taking the output and pushing to `CHARMCRAFT_AUTH`.

Store the file contents verbatim, i.e. the base64 blob, exactly as
`CHARMCRAFT_AUTH=$(cat charmhub-creds.dat)` would be used with the `charmcraft`
CLI. `scripts/util/charmhub.py` base64-decodes the value and reads the macaroon
from the `v` field of the resulting JSON. Storing a decoded macaroon
(`base64 -d | jq -r .v`) instead leaves snap track creation failing with a
base64 decode error.
