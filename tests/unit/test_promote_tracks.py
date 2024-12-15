import argparse
import contextlib
import unittest.mock as mock

import promote_tracks
import pytest
from freezegun import freeze_time

MOCK_BRANCH = "branchy-mcbranchface"
MOCK_TRACK = "1.31-tracky"
args = argparse.Namespace(
    dry_run=False,
    loglevel="INFO",
    gh_action=False,
    days_in_edge_risk=promote_tracks.DAYS_TO_STAY_IN_EDGE,
    days_in_beta_risk=promote_tracks.DAYS_TO_STAY_IN_BETA,
    days_in_candidate_risk=promote_tracks.DAYS_TO_STAY_IN_CANDIDATE,
)


@pytest.fixture(autouse=True)
def branch_from_track():
    with mock.patch("util.lp.branch_from_track") as mocked:
        mocked.return_value = MOCK_BRANCH
        yield mocked


def _create_channel(
    track: str, risk: str, revision: int, date="2000-01-01", arch="amd64"
):
    return {
        "channel": {
            "architecture": arch,
            "name": f"{track}/{risk}",
            "released-at": f"{date}T00:00:00.000000+00:00",
            "risk": risk,
            "track": track,
        },
        "created-at": f"{date}T00:00:00.000000+00:00",
        "download": {},
        "revision": revision,
        "type": "app",
        "version": "v1.31.0",
    }


def _expected_proposals(track, next_risk, risk, revision):
    return [
        {
            "arch": "amd64",
            "branch": MOCK_BRANCH,
            "lxd-images": ["ubuntu:20.04", "ubuntu:22.04", "ubuntu:24.04"],
            "name": f"k8s-1.31-tracky/{next_risk}-amd64",
            "next-risk": next_risk,
            "revision": revision,
            "runner-labels": ["X64", "self-hosted"],
            "snap-channel": f"{track}/{next_risk}",
            "track": track,
            "upgrade-channels": [[f"{track}/stable", f"{track}/{risk}"]],
        }
    ]


@contextlib.contextmanager
def _make_channel_map(track: str, risk: str, extra_risk: None | str = None):
    snap_info = {"channel-map": [_create_channel(track, risk, 2)]}
    if extra_risk:
        snap_info["channel-map"].append(_create_channel(track, extra_risk, 1))
    snap_info["channel-map"].append(
        _create_channel(track, "stable", 3, arch="arm64", date="2001-01-01")
    )
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
    with freeze_time(now), _make_channel_map(MOCK_TRACK, risk, extra_risk="stable"):
        proposals = promote_tracks.create_proposal(args)
    assert proposals == _expected_proposals(MOCK_TRACK, next_risk, risk, 2)


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-01")],
)
def test_risk_not_yet_promotable_edge(risk, now):
    with freeze_time(now), _make_channel_map(MOCK_TRACK, risk, extra_risk="beta"):
        proposals = promote_tracks.create_proposal(args)
    assert proposals == [], "Channel should not be promoted too soon"


@pytest.mark.parametrize(
    "risk, now",
    [("beta", "2000-01-03"), ("candidate", "2000-01-05")],
)
def test_risk_not_yet_promotable(risk, now):
    with freeze_time(now), _make_channel_map(MOCK_TRACK, risk):
        proposals = promote_tracks.create_proposal(args)
    assert proposals == [], "Channel should not be promoted too soon"


@pytest.mark.parametrize(
    "risk, now",
    [("candidate", "2000-01-06")],
)
def test_risk_promotable_without_stable(risk, now):
    with freeze_time(now), _make_channel_map(MOCK_TRACK, risk):
        proposals = promote_tracks.create_proposal(args)

    assert (
        proposals == []
    ), "Candidate track should not be promoted if stable is missing"


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-06")],
)
def test_latest_track(risk, now):
    with freeze_time(now), _make_channel_map("latest", risk):
        proposals = promote_tracks.create_proposal(args)
    assert proposals == [], "Latest track should not be promoted"

@pytest.mark.parametrize(
    "track, ignored_patterns, expected_ignored",
    [
        ("1.31", ["1.31", r"1\.\d+-classic"], True),  # Exact match
        ("1.31-classic", ["1\\.31", r"1\.\d+-classic"], True),  # Regex match
        ("1.32", ["1\\.31", r"1\.\d+-classic"], False),  # No match
        ("1.31-classic", [], False),  # Nothing ignored
    ],
)
def test_ignored_tracks(track, ignored_patterns, expected_ignored):
    with _make_channel_map(track, "edge"):
        args.ignore_tracks = ignored_patterns
        proposals = promote_tracks.create_proposal(args)
    assert (len(proposals) == 0) == expected_ignored, (
        f"Track '{track}' should {'be ignored' if expected_ignored else 'not be ignored'}"
        )
