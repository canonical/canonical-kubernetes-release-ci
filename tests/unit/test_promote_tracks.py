import unittest.mock as mock

import pytest
from freezegun import freeze_time
from promote_tracks import check_and_promote


@pytest.fixture
def release_revision():
    with mock.patch("promote_tracks.release_revision") as mocked:
        yield mocked


def _create_channel(track: str, risk: str, revision: int):
    return {
        "channel": {
            "architecture": "amd64",
            "name": f"{track}/{risk}",
            "released-at": "2000-01-01T00:00:00.0+00:00",
            "risk": risk,
            "track": track,
        },
        "created-at": "2000-01-01T00:00:00.000000+00:00",
        "download": {},
        "revision": revision,
        "type": "app",
        "version": "v1.31.0",
    }


def _make_channel_map(track: str, risk: str, has_stable: bool = False):
    snap_info = {"channel-map": [_create_channel(track, risk, 2)]}
    if has_stable:
        snap_info["channel-map"].append(_create_channel(track, "stable", 1))
    return snap_info


@pytest.mark.parametrize(
    "risk, next_risk, now",
    [
        ("edge", "beta", "2000-01-02"),
        ("beta", "candidate", "2000-01-04"),
        ("candidate", "stable", "2000-01-06"),
    ],
)
def test_risk_promotable(risk, next_risk, now, release_revision):
    with freeze_time(now):
        check_and_promote(
            _make_channel_map("tracky", risk, has_stable=True), dry_run=False
        )
    release_revision.assert_called_once_with(2, f"tracky/{next_risk}")


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-01"), ("beta", "2000-01-03"), ("candidate", "2000-01-05")],
)
def test_risk_not_yet_promotable(risk, now, release_revision):
    with freeze_time(now):
        check_and_promote(_make_channel_map("tracky", risk), dry_run=False)
    release_revision.assert_not_called(), "Channel should not be promoted too soon"


@pytest.mark.parametrize(
    "risk, now",
    [("candidate", "2000-01-06")],
)
def test_risk_promotable_without_stable(risk, now, release_revision):
    with freeze_time(now):
        check_and_promote(_make_channel_map("tracky", risk), dry_run=False)
    (
        release_revision.assert_not_called(),
        "Candidate track should not be promoted if stable is missing",
    )


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-06")],
)
def test_latest_track(risk, now, release_revision):
    with freeze_time(now):
        check_and_promote(_make_channel_map("latest", risk), dry_run=False)
    release_revision.assert_not_called(), "Latest track should not be promoted"
