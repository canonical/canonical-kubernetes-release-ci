import pytest
from util import promotion_matrix

RISKS = ["beta", "candidate", "stable"]


@pytest.fixture
def profiles():
    return [
        promotion_matrix.Profile(
            {"amd64": "oldest"}, {r: ["18.04"] for r in RISKS}, older_than=(1, 30)
        ),
        promotion_matrix.Profile(
            {"amd64": "old"}, {r: ["20.04"] for r in RISKS}, older_than=(1, 36)
        ),
        promotion_matrix.Profile({"amd64": "new"}, {r: ["24.04"] for r in RISKS}),
    ]


@pytest.mark.parametrize(
    "track, runner",
    [
        ("1.29", "oldest"),
        ("1.30", "old"),
        ("1.35-classic", "old"),
        ("1.36", "new"),
        ("1.36-classic", "new"),
        ("1.100", "new"),
        ("2.0", "new"),
    ],
)
def test_profile_for_track(profiles, track, runner):
    assert promotion_matrix.profile_for_track(track, profiles).runner_for("amd64") == runner


def test_profile_for_track_invalid(profiles):
    with pytest.raises(ValueError):
        promotion_matrix.profile_for_track("latest", profiles)


@pytest.mark.parametrize(
    "content",
    [
        "overrides: []\n",
        "default: {runs-on: {amd64: x}, lxd-series: {}, older-than: '1.36'}\n",
        "default: {runs-on: {amd64: x}, lxd-series: {}}\n"
        "overrides: [{runs-on: {amd64: y}, lxd-series: {}}]\n",
    ],
)
def test_load_profiles_invalid(tmp_path, content):
    config = tmp_path / "matrix.yaml"
    config.write_text(content)
    with pytest.raises(ValueError):
        promotion_matrix.load_profiles.__wrapped__(config)


def test_repo_config_is_valid():
    for profile in promotion_matrix.load_profiles.__wrapped__():
        assert set(profile.lxd_series) == set(RISKS)
        assert set(profile.runs_on) == {"amd64", "arm64"}
    assert (
        promotion_matrix.profile_for_track("1.35").runner_for("arm64")
        == "self-hosted-linux-arm64-jammy-large"
    )
    assert (
        promotion_matrix.profile_for_track("1.36").runner_for("amd64")
        == "self-hosted-linux-amd64-noble-large"
    )
    assert "ubuntu:20.04" not in [
        f"ubuntu:{s}" for s in promotion_matrix.profile_for_track("1.36").lxd_series["beta"]
    ]


def test_runner_for_unknown_arch(profiles):
    with pytest.raises(ValueError):
        profiles[-1].runner_for("s390x")
