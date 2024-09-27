import argparse
import contextlib
import unittest.mock as mock

import pytest
from freezegun import freeze_time
from promote_tracks import create_proposal

MOCK_BRANCH = "branchy-mcbranchface"
args = argparse.Namespace(dry_run=False, loglevel="INFO", gh_action=False)


@pytest.fixture(autouse=True)
def branch_from_track():
    with mock.patch("util.lp.branch_from_track") as mocked:
        mocked.return_value = MOCK_BRANCH
        yield mocked


def _create_channel(track: str, risk: str, revision: int):
    return {
        "channel": {
            "architecture": "amd64",
            "name": f"{track}/{risk}",
            "released-at": "2000-01-01T00:00:00.000000+00:00",
            "risk": risk,
            "track": track,
        },
        "created-at": "2000-01-01T00:00:00.000000+00:00",
        "download": {},
        "revision": revision,
        "type": "app",
        "version": "v1.31.0",
    }


def _expected_proposals(next_risk, risk, revision):
    return [
        {
            "name": f"k8s-tracky-{next_risk}-amd64",
            "branch": MOCK_BRANCH,
            "lxd-images": ["ubuntu:20.04", "ubuntu:22.04", "ubuntu:24.04"],
            "runner-labels": ["X64", "self-hosted"],
            "upgrade-channels": [[f"tracky/{next_risk}", f"tracky/{risk}"]],
            "snap-channel": f"tracky/{next_risk}",
            "revision": revision,
        }
    ]


@contextlib.contextmanager
def _make_channel_map(track: str, risk: str, extra_risk: None | str = None):
    snap_info = {"channel-map": [_create_channel(track, risk, 2)]}
    if extra_risk:
        snap_info["channel-map"].append(_create_channel(track, extra_risk, 1))
    with mock.patch("promote_tracks.snapstore.info") as mocked:
        mocked.return_value = snap_info
        yield snap_info


@pytest.mark.parametrize(
    "risk, next_risk, now",
    [
        ("edge", "beta", "2000-01-02"),
        ("beta", "candidate", "2000-01-04"),
        ("candidate", "stable", "2000-01-06"),
    ],
)
def test_risk_promotable(risk, next_risk, now):
    with freeze_time(now), _make_channel_map("tracky", risk, extra_risk="stable"):
        proposals = create_proposal(args)
    assert proposals == _expected_proposals(next_risk, risk, 2)


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-01")],
)
def test_risk_not_yet_promotable_edge(risk, now):
    with freeze_time(now), _make_channel_map("tracky", risk, extra_risk="beta"):
        proposals = create_proposal(args)
    assert proposals == [], "Channel should not be promoted too soon"


@pytest.mark.parametrize(
    "risk, now",
    [("beta", "2000-01-03"), ("candidate", "2000-01-05")],
)
def test_risk_not_yet_promotable(risk, now):
    with freeze_time(now), _make_channel_map("tracky", risk):
        proposals = create_proposal(args)
    assert proposals == [], "Channel should not be promoted too soon"


@pytest.mark.parametrize(
    "risk, now",
    [("candidate", "2000-01-06")],
)
def test_risk_promotable_without_stable(risk, now):
    with freeze_time(now), _make_channel_map("tracky", risk):
        proposals = create_proposal(args)

    assert (
        proposals == []
    ), "Candidate track should not be promoted if stable is missing"


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-06")],
)
def test_latest_track(risk, now):
    with freeze_time(now), _make_channel_map("latest", risk):
        proposals = create_proposal(args)
    assert proposals == [], "Latest track should not be promoted"
