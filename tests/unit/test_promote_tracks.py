import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import unittest.mock as mock
from pathlib import Path
from typing import Optional

import promote_tracks
import pytest
from freezegun import freeze_time
from util import promotion_matrix

MOCK_BRANCH = "branchy-mcbranchface"
MOCK_TRACK = "1.31-tracky"
MOCK_SERIES = ["capricorn", "aquarius", "pisces"]
MOCK_RUNNERS = {"amd64": "runny-mcrunnerface", "arm64": "runny-armrunnerface"}
MOCK_PROFILES = [
    promotion_matrix.Profile(
        runs_on=MOCK_RUNNERS,
        lxd_series={
            "beta": MOCK_SERIES,
            "candidate": MOCK_SERIES[-1:],
            "stable": MOCK_SERIES[-1:],
        },
    )
]
PATCH_PROFILES = mock.patch(
    "promote_tracks.promotion_matrix.load_profiles", new=mock.Mock(return_value=MOCK_PROFILES)
)
args = argparse.Namespace(
    dry_run=False,
    loglevel="INFO",
    gh_action=False,
    days_in_edge_risk=promote_tracks.DAYS_TO_STAY_IN_EDGE,
    days_in_beta_risk=promote_tracks.DAYS_TO_STAY_IN_BETA,
    days_in_candidate_risk=promote_tracks.DAYS_TO_STAY_IN_CANDIDATE,
)


@pytest.fixture(autouse=True)
def reset_args():
    """Snapshot/restore the shared `args` Namespace so tests can't leak state."""
    original = vars(args).copy()
    yield
    vars(args).clear()
    vars(args).update(original)


@pytest.fixture(autouse=True)
def lp_track_mocks():
    with (
        mock.patch("util.lp.branch_from_track", return_value=MOCK_BRANCH) as mocked_branch,
        mock.patch("util.lp.recipe_for_track", return_value=None),
    ):
        yield mocked_branch


def _create_channel(track: str, risk: str, revision: int, date="2000-01-01", arch="amd64"):
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


def _expected_proposals(track, next_risk, risk, revision, upgrade_channels=None):
    if not upgrade_channels:
        upgrade_channels = [[f"{track}/stable", f"{track}/{risk}@{revision}"]]

    selected_series = MOCK_SERIES if next_risk == "beta" else MOCK_SERIES[-1:]
    images = [f"ubuntu:{s}" for s in selected_series]

    return [
        {
            "arch": "amd64",
            "branch": MOCK_BRANCH,
            "lxd-images": images,
            "name": f"k8s-1.31-tracky/{next_risk}-amd64",
            "next-risk": next_risk,
            "revision": revision,
            "runner-labels": [MOCK_RUNNERS["amd64"]],
            "snap-channel": f"{track}/{next_risk}",
            "track": track,
            "upgrade-channels": upgrade_channels,
            "version": "v1.31.0",
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


@contextlib.contextmanager
def _mock_k8s_versions(latest_stable: str = "v1.33.0"):
    with (
        mock.patch("util.k8s.get_latest_stable", new=mock.Mock(return_value=latest_stable)),
    ):
        yield


@pytest.mark.parametrize(
    "risk, next_risk, now",
    [
        ("edge", "beta", "2000-01-02"),
        ("beta", "candidate", "2000-01-02"),
        ("candidate", "stable", "2000-01-02"),
    ],
)
@PATCH_PROFILES
def test_risk_promotable(risk, next_risk, now):
    with (
        freeze_time(now),
        _make_channel_map(MOCK_TRACK, risk, extra_risk="stable"),
        _mock_k8s_versions(),
    ):
        proposals = promote_tracks.create_proposal(args)
    assert proposals == _expected_proposals(MOCK_TRACK, next_risk, risk, 2)


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-01")],
)
def test_risk_not_yet_promotable_edge(risk, now):
    with (
        freeze_time(now),
        _make_channel_map(MOCK_TRACK, risk, extra_risk="beta"),
        _mock_k8s_versions(),
    ):
        proposals = promote_tracks.create_proposal(args)
    assert proposals == [], "Channel should not be promoted too soon"


@pytest.mark.parametrize(
    "risk, now",
    [("beta", "2000-01-01"), ("candidate", "2000-01-01")],
)
def test_risk_not_yet_promotable(risk, now):
    with freeze_time(now), _make_channel_map(MOCK_TRACK, risk), _mock_k8s_versions():
        proposals = promote_tracks.create_proposal(args)
    assert proposals == [], "Channel should not be promoted too soon"


@pytest.mark.parametrize(
    "risk, now",
    [("edge", "2000-01-06")],
)
def test_latest_track(risk, now):
    with freeze_time(now), _make_channel_map("latest", risk), _mock_k8s_versions():
        proposals = promote_tracks.create_proposal(args)
    assert proposals == [], "Latest track should not be promoted"


@pytest.mark.parametrize(
    "track, ignored_entries, expected_ignored",
    [
        ("1.31", ["1.31", r"1\.\d+-classic"], True),  # Exact match
        ("1.31-classic", ["1\\.31", r"1\.\d+-classic"], True),  # Regex match
        ("1.32", ["1\\.31", r"1\.\d+-classic"], False),  # No match
        ("1.31-classic", [], False),  # Nothing ignored
    ],
)
def test_ignore_tracks_frozen(track, ignored_entries, expected_ignored):
    with _make_channel_map(track, "edge"), _mock_k8s_versions():
        args.ignore_tracks = [promote_tracks._parse_ignore_track(e) for e in ignored_entries]
        proposals = promote_tracks.create_proposal(args)
    assert (len(proposals) == 0) == expected_ignored, (
        f"Track '{track}' should {'be ignored' if expected_ignored else 'not be ignored'}"
    )


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("1.31", ("1.31", None)),
        (r"1\.36-classic:candidate", (r"1\.36-classic", "candidate")),
        ("latest:edge", ("latest", "edge")),
        # A literal `:` from regex syntax (non-capturing group), not a ceiling
        # suffix, must be left untouched.
        (r"^1\.3(?:0|1)(-.*)?$", (r"^1\.3(?:0|1)(-.*)?$", None)),
    ],
)
def test_parse_ignore_track(entry, expected):
    assert promote_tracks._parse_ignore_track(entry) == expected


def test_parse_ignore_track_rejects_invalid_ceiling():
    with pytest.raises(argparse.ArgumentTypeError):
        promote_tracks._parse_ignore_track("1.36-classic:stale")


@pytest.mark.parametrize(
    "track, ignored_entries, expected_held_at_candidate",
    [
        ("1.40-classic", ["1\\.40-classic:candidate"], True),  # Exact match
        ("1.40-classic", [r"1\.\d+-classic:candidate"], True),  # Regex match
        ("1.41-classic", ["1\\.40-classic:candidate"], False),  # No match
        ("1.40-classic", [], False),  # Nothing ignored
        ("1.40-classic", [r"1\.\d+-classic:beta"], True),  # Stricter ceiling
        ("1.40-classic", [r"1\.\d+-classic:edge"], True),  # Lowest ceiling
    ],
)
@PATCH_PROFILES
def test_ignore_tracks_ceiling(track, ignored_entries, expected_held_at_candidate):
    with (
        freeze_time("2000-01-02"),
        _make_channel_map(track, "candidate", extra_risk="stable"),
        _mock_k8s_versions(),
    ):
        args.ignore_tracks = [promote_tracks._parse_ignore_track(e) for e in ignored_entries]
        proposals = promote_tracks.create_proposal(args)
    expected_channels = [] if expected_held_at_candidate else [f"{track}/stable"]
    assert [p["snap-channel"] for p in proposals] == expected_channels, (
        f"Track '{track}' should "
        f"{'be held at candidate' if expected_held_at_candidate else 'be promoted to stable'}"
    )


@PATCH_PROFILES
def test_ignore_tracks_ceiling_allows_promotion_up_to_ceiling():
    # A track capped at beta must still be promoted edge->beta: reaching the
    # ceiling exactly is allowed, only exceeding it is blocked.
    with (
        freeze_time("2000-01-02"),
        _make_channel_map(MOCK_TRACK, "edge", extra_risk="stable"),
        _mock_k8s_versions(),
    ):
        args.ignore_tracks = [promote_tracks._parse_ignore_track(r"1\.\d+-tracky:beta")]
        proposals = promote_tracks.create_proposal(args)
    assert proposals == _expected_proposals(MOCK_TRACK, "beta", "edge", 2)


@mock.patch("promote_tracks.IGNORE_TRACKS", [(MOCK_TRACK, "candidate")])
def test_ignore_tracks_most_restrictive_wins():
    # A CLI freeze must win over a laxer built-in cap for the same track, even
    # though built-in entries are scanned first.
    with _make_channel_map(MOCK_TRACK, "edge"), _mock_k8s_versions():
        args.ignore_tracks = [promote_tracks._parse_ignore_track(MOCK_TRACK)]
        proposals = promote_tracks.create_proposal(args)
    assert proposals == [], "Explicit freeze must not be loosened by a laxer built-in cap"


@PATCH_PROFILES
def test_ignore_tracks_ceiling_does_not_block_lower_risks():
    # A track held at candidate must still be promoted through edge/beta/candidate.
    with (
        freeze_time("2000-01-02"),
        _make_channel_map(MOCK_TRACK, "beta", extra_risk="stable"),
        _mock_k8s_versions(),
    ):
        args.ignore_tracks = [promote_tracks._parse_ignore_track(r"1\.\d+-tracky:candidate")]
        proposals = promote_tracks.create_proposal(args)
    assert proposals == _expected_proposals(MOCK_TRACK, "candidate", "beta", 2)


@PATCH_PROFILES
def test_ignore_tracks_ceiling_holds_new_stable_bootstrap():
    # A track held at candidate must not take the "new stable upstream release"
    # fast path either: it must still be promoted one purgatory-gated step at a
    # time (edge->beta here), never batched straight through to stable.
    with (
        _make_channel_map(MOCK_TRACK, "edge"),
        _mock_k8s_versions("v1.31.0"),
    ):
        args.ignore_tracks = [promote_tracks._parse_ignore_track(r"1\.\d+-tracky:candidate")]
        proposals = promote_tracks.create_proposal(args)
    exp_upgrade_channels = [[f"{MOCK_TRACK}/edge"]]
    assert proposals == _expected_proposals(
        MOCK_TRACK, "beta", "edge", 2, upgrade_channels=exp_upgrade_channels
    )


@PATCH_PROFILES
def test_edge_skipped_and_retried_when_recipe_ahead_with_failed_build():
    failed_build = mock.Mock(
        arch_tag="amd64",
        buildstate="Failed to build",
        can_be_retried=True,
        revision_id=None,
        store_upload_revision=None,
        store_upload_status="Unscheduled",
    )
    stale_edge_build = mock.Mock(
        arch_tag="amd64",
        buildstate="Successfully built",
        can_be_retried=False,
        revision_id="e5aec773" * 5,
        store_upload_revision=2,
        store_upload_status="Uploaded",
    )
    recipe = mock.Mock(
        git_ref=mock.Mock(commit_sha1="b5634b2f" * 5),
        builds=[failed_build, stale_edge_build],
    )

    with (
        freeze_time("2000-01-02"),
        _make_channel_map(MOCK_TRACK, "edge", extra_risk="stable"),
        _mock_k8s_versions(),
        mock.patch("util.lp.recipe_for_track", return_value=recipe),
    ):
        proposals = promote_tracks.create_proposal(args)

    failed_build.retry.assert_called_once_with()
    assert proposals == []


@PATCH_PROFILES
@mock.patch("util.gh.requests.get")
def test_edge_promoted_when_edge_build_descends_from_recipe_git_ref(mock_get):
    mock_resp = mock.Mock()
    mock_resp.json.return_value = {"status": "ahead"}
    mock_get.return_value = mock_resp

    edge_build = mock.Mock(
        arch_tag="amd64",
        buildstate="Successfully built",
        can_be_retried=False,
        revision_id="cd66c8a238" * 4,
        store_upload_revision=2,
        store_upload_status="Uploaded",
    )
    recipe = mock.Mock(
        git_ref=mock.Mock(commit_sha1="6434d09971" * 4),
        builds=[edge_build],
    )

    with (
        freeze_time("2000-01-02"),
        _make_channel_map(MOCK_TRACK, "edge", extra_risk="stable"),
        _mock_k8s_versions(),
        mock.patch("util.lp.recipe_for_track", return_value=recipe),
    ):
        proposals = promote_tracks.create_proposal(args)

    assert proposals == _expected_proposals(MOCK_TRACK, "beta", "edge", 2)


@pytest.mark.parametrize("risk, next_risk", [("beta", "candidate"), ("candidate", "stable")])
@PATCH_PROFILES
def test_non_edge_risks_still_promote_when_recipe_git_ref_ahead(risk, next_risk):
    failed_build = mock.Mock(
        arch_tag="amd64",
        buildstate="Failed to build",
        can_be_retried=True,
        revision_id=None,
        store_upload_revision=None,
        store_upload_status="Unscheduled",
    )
    recipe = mock.Mock(
        git_ref=mock.Mock(commit_sha1="b5634b2f" * 5),
        builds=[failed_build],
    )

    with (
        freeze_time("2000-01-02"),
        _make_channel_map(MOCK_TRACK, risk, extra_risk="stable"),
        _mock_k8s_versions(),
        mock.patch("util.lp.recipe_for_track", return_value=recipe),
    ):
        proposals = promote_tracks.create_proposal(args)

    assert proposals == _expected_proposals(MOCK_TRACK, next_risk, risk, 2)


@pytest.mark.parametrize(
    "argv",
    [
        ["promote_tracks.py", "--dry-run", "propose", "--gh-action"],
        ["promote_tracks.py", "propose", "--gh-action", "--dry-run"],
    ],
)
def test_main_propose_dry_run_flag_positions(argv, monkeypatch: pytest.MonkeyPatch):
    captured: list[bool] = []
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(promote_tracks, "create_proposal", lambda a: captured.append(a.dry_run))

    promote_tracks.main()

    assert captured == [True]


@PATCH_PROFILES
def test_new_stable():
    # In this scenario, the channel version matches the latest stable.
    with _make_channel_map(MOCK_TRACK, "edge"), _mock_k8s_versions("v1.31.0"):
        proposals = promote_tracks.create_proposal(args)

    # New stable release, we expect it to be promoted to all risk levels.
    exp_upgrade_channels = [[f"{MOCK_TRACK}/edge"]]
    exp_proposals = [
        _expected_proposals(MOCK_TRACK, "beta", "edge", 2, upgrade_channels=exp_upgrade_channels)[
            0
        ],
        _expected_proposals(
            MOCK_TRACK, "candidate", "edge", 2, upgrade_channels=exp_upgrade_channels
        )[0],
        _expected_proposals(
            MOCK_TRACK, "stable", "edge", 2, upgrade_channels=exp_upgrade_channels
        )[0],
    ]
    assert proposals == exp_proposals


def _make_squashfs_snap(tmp_path: Path, bom: dict | None = None) -> Path:
    if not shutil.which("mksquashfs") or not shutil.which("unsquashfs"):
        pytest.skip("squashfs-tools not installed")
    root = tmp_path / "snap-root"
    root.mkdir()
    if bom is not None:
        (root / "bom.json").write_text(json.dumps(bom))
    else:
        (root / "placeholder").write_text("no bom")
    snap_path = tmp_path / "k8s.snap"
    subprocess.run(
        ["mksquashfs", str(root), str(snap_path), "-noappend", "-quiet"],
        check=True,
    )
    return snap_path


def _make_snap_git_repo(tmp_path: Path, branch: str = "release-1.35") -> tuple[Path, str, str]:
    remote = tmp_path / "k8s-snap.git"
    remote.mkdir()
    git_env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["git", "init", "-b", branch], cwd=remote, env=git_env, check=True)
    tests_dir = remote / "tests" / "integration" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_smoke.py").write_text("# initial\n")
    subprocess.run(["git", "add", "."], cwd=remote, env=git_env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "snap build commit"], cwd=remote, env=git_env, check=True
    )
    snap_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=remote, text=True).strip()

    (tests_dir / "test_new_head_only.py").write_text("# merged after snap built\n")
    subprocess.run(["git", "add", "."], cwd=remote, env=git_env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "post-build commit"], cwd=remote, env=git_env, check=True
    )
    head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=remote, text=True).strip()
    return remote, snap_sha, head_sha


def test_execute_e2e_test_pins_to_snap_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    remote, snap_sha, head_sha = _make_snap_git_repo(tmp_path)
    assert snap_sha != head_sha
    snap_path = _make_squashfs_snap(
        tmp_path, {"k8s": {"version": "release-1.35", "revision": snap_sha}}
    )
    monkeypatch.setenv("TEST_SNAP", str(snap_path))
    monkeypatch.setattr(promote_tracks.util, "SNAP_REPO", f"file://{remote}")

    checked_out_commits: list[str] = []
    real_run = subprocess.run

    def fake_tox_run(cmd, *run_args, **run_kwargs):
        if cmd and cmd[0] == "tox" and "--version" not in cmd:
            cwd = Path(run_kwargs["cwd"])
            checked_out = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=cwd, text=True
            ).strip()
            checked_out_commits.append(checked_out)
            assert not (cwd / "tests" / "test_new_head_only.py").exists()
            return subprocess.CompletedProcess(cmd, 0)
        if cmd and cmd[0] == "tox":
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, *run_args, **run_kwargs)

    e2e_args = argparse.Namespace(branch="release-1.35", next_risk="beta")
    with mock.patch("promote_tracks.subprocess.run", side_effect=fake_tox_run):
        promote_tracks.execute_e2e_test(e2e_args)

    assert checked_out_commits == [snap_sha]


def test_execute_e2e_test_falls_back_to_branch_when_bom_missing_or_commit_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, _, head_sha = _make_snap_git_repo(tmp_path)
    monkeypatch.setattr(promote_tracks.util, "SNAP_REPO", f"file://{remote}")

    # Unreachable commit in bom.json must fall back to branch HEAD rather than aborting.
    snap_path = _make_squashfs_snap(
        tmp_path,
        {"k8s": {"version": "release-1.35", "revision": "deadbeef" * 5}},
    )
    monkeypatch.setenv("TEST_SNAP", str(snap_path))

    checked_out_commits: list[str] = []
    real_run = subprocess.run

    def fake_tox_run(cmd, *run_args, **run_kwargs):
        if cmd and cmd[0] == "tox" and "--version" not in cmd:
            checked_out = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=run_kwargs["cwd"], text=True
            ).strip()
            checked_out_commits.append(checked_out)
            return subprocess.CompletedProcess(cmd, 0)
        if cmd and cmd[0] == "tox":
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, *run_args, **run_kwargs)

    e2e_args = argparse.Namespace(branch="release-1.35", next_risk="beta")
    with mock.patch("promote_tracks.subprocess.run", side_effect=fake_tox_run):
        promote_tracks.execute_e2e_test(e2e_args)

    assert checked_out_commits == [head_sha]

    # Missing bom.json or non-SHA revision also falls back to branch HEAD.
    no_bom_dir = tmp_path / "no-bom"
    no_bom_dir.mkdir()
    assert promote_tracks._snap_commit(str(_make_squashfs_snap(no_bom_dir, None))) is None
    bad_sha_dir = tmp_path / "bad-sha"
    bad_sha_dir.mkdir()
    bad_snap = _make_squashfs_snap(bad_sha_dir, {"k8s": {"revision": "not-a-sha"}})
    assert promote_tracks._snap_commit(str(bad_snap)) is None
    assert promote_tracks._snap_commit(None) is None


def test_execute_e2e_test_does_not_rerun_on_branch_when_tox_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remote, snap_sha, _ = _make_snap_git_repo(tmp_path)
    snap_path = _make_squashfs_snap(
        tmp_path, {"k8s": {"version": "release-1.35", "revision": snap_sha}}
    )
    monkeypatch.setenv("TEST_SNAP", str(snap_path))
    monkeypatch.setattr(promote_tracks.util, "SNAP_REPO", f"file://{remote}")

    tox_invocations = 0
    real_run = subprocess.run

    def failing_tox_run(cmd, *run_args, **run_kwargs):
        nonlocal tox_invocations
        if cmd and cmd[0] == "tox" and "--version" not in cmd:
            tox_invocations += 1
            raise subprocess.CalledProcessError(1, cmd)
        if cmd and cmd[0] == "tox":
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, *run_args, **run_kwargs)

    e2e_args = argparse.Namespace(branch="release-1.35", next_risk="beta")
    with (
        mock.patch("promote_tracks.subprocess.run", side_effect=failing_tox_run),
        pytest.raises(promote_tracks.ProposalTestError),
    ):
        promote_tracks.execute_e2e_test(e2e_args)

    assert tox_invocations == 1


def test_auto_promotion_ignore_tracks_constant():
    assert r"1\.36-classic:candidate" in promote_tracks.AUTO_PROMOTION_IGNORE_TRACKS
    assert r"1\.37-classic:candidate" in promote_tracks.AUTO_PROMOTION_IGNORE_TRACKS
    assert r"^1\.\d{2}$" in promote_tracks.AUTO_PROMOTION_IGNORE_TRACKS


def test_resolve_propose_inputs(monkeypatch: pytest.MonkeyPatch):
    # Test scheduled event defaults
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    resolved = promote_tracks.resolve_propose_inputs(argparse.Namespace())
    assert resolved.days_in_edge_risk == promote_tracks.DAYS_TO_STAY_IN_EDGE
    assert resolved.days_in_beta_risk == promote_tracks.DAYS_TO_STAY_IN_BETA
    assert resolved.days_in_candidate_risk == promote_tracks.DAYS_TO_STAY_IN_CANDIDATE
    assert resolved.ignore_tracks == [
        promote_tracks._parse_ignore_track(t) for t in promote_tracks.AUTO_PROMOTION_IGNORE_TRACKS
    ]

    # Test dispatch event with env vars
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("DAYS_IN_EDGE_RISK", "5")
    monkeypatch.setenv("DAYS_IN_BETA_RISK", "7")
    monkeypatch.setenv("DAYS_IN_CANDIDATE_RISK", "9")
    monkeypatch.setenv("IGNORE_TRACKS", "1.36-classic:candidate")
    monkeypatch.setenv("IGNORE_ARCHES", "arm64 s390x")

    resolved2 = promote_tracks.resolve_propose_inputs(argparse.Namespace())
    assert resolved2.days_in_edge_risk == 5
    assert resolved2.days_in_beta_risk == 7
    assert resolved2.days_in_candidate_risk == 9
    assert resolved2.ignore_tracks == [("1.36-classic", "candidate")]
    assert resolved2.ignore_arches == ["arm64", "s390x"]

    # An explicit CLI value wins over the environment, even when it equals the default.
    explicit = promote_tracks.build_parser().parse_args(["propose", "--days-in-beta-risk", "1"])
    resolved3 = promote_tracks.resolve_propose_inputs(explicit)
    assert resolved3.days_in_beta_risk == 1
    assert resolved3.days_in_edge_risk == 5


@pytest.mark.parametrize(
    "event, env_tracks, cli, expected",
    [
        ("schedule", None, [], promote_tracks.AUTO_PROMOTION_IGNORE_TRACKS),
        ("workflow_dispatch", r"1\.36-classic:candidate", [], [r"1\.36-classic:candidate"]),
        ("workflow_dispatch", None, [], []),
        ("schedule", None, ["--ignore-tracks", "latest"], ["latest"]),
    ],
)
def test_resolve_propose_inputs_from_real_parser(monkeypatch, event, env_tracks, cli, expected):
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.delenv("IGNORE_TRACKS", raising=False)
    if env_tracks:
        monkeypatch.setenv("IGNORE_TRACKS", env_tracks)
    args = promote_tracks.build_parser().parse_args(["propose", "--gh-action", *cli])

    resolved = promote_tracks.resolve_propose_inputs(args)

    assert resolved.ignore_tracks == [promote_tracks._parse_ignore_track(t) for t in expected]


def test_resolve_propose_inputs_preserves_backslashes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv(
        "IGNORE_TRACKS",
        r"^1\.\d{2}$ (?!1\.36-classic$).* 1\.36-classic:candidate",
    )
    test_args = argparse.Namespace()
    resolved = promote_tracks.resolve_propose_inputs(test_args)

    assert resolved.ignore_tracks == [
        (r"^1\.\d{2}$", None),
        (r"(?!1\.36-classic$).*", None),
        (r"1\.36-classic", "candidate"),
    ]

    def evaluate(track: str) -> tuple[int, Optional[str]]:
        matches = [
            ((-1 if ceiling is None else promote_tracks.RISK_LEVELS.index(ceiling)), pattern)
            for pattern, ceiling in resolved.ignore_tracks
            if re.fullmatch(pattern, track)
        ]
        return min(matches, default=(len(promote_tracks.RISK_LEVELS) - 1, None))

    # 1.36 frozen
    res_136, _ = evaluate("1.36")
    assert res_136 == -1

    # 1.36-classic capped at candidate (matches 1\.36-classic)
    res_136_classic, pattern_136_classic = evaluate("1.36-classic")
    assert res_136_classic == promote_tracks.RISK_LEVELS.index("candidate")
    assert pattern_136_classic == r"1\.36-classic"

    # 1.35-classic frozen (matches (?!1\.36-classic$).*)
    res_135_classic, pattern_135_classic = evaluate("1.35-classic")
    assert res_135_classic == -1
    assert pattern_135_classic == r"(?!1\.36-classic$).*"

    # 1x36-classic not matched by the cap pattern 1\.36-classic
    cap_pattern = next(p for p, c in resolved.ignore_tracks if c == "candidate")
    assert re.fullmatch(cap_pattern, "1x36-classic") is None


def test_sanitize_artifact_name():
    assert (
        promote_tracks.sanitize_artifact_name("k8s-1.36-classic/beta-amd64")
        == "k8s-1.36-classic.beta-amd64"
    )
    assert promote_tracks.sanitize_artifact_name("foo:bar/baz") == "foo.bar.baz"


def test_compute_test_outcome():
    assert promote_tracks.compute_test_outcome("success", "success", "[]") == (True, True)
    assert promote_tracks.compute_test_outcome("failure", "success", "[]") == (False, True)
    assert promote_tracks.compute_test_outcome("success", "failure", "[]") == (False, False)
    # Empty upgrade matrix skipped is treated as success
    assert promote_tracks.compute_test_outcome("success", "skipped", "[]") == (True, True)
    # Non-empty upgrade matrix skipped is treated as not passed
    assert promote_tracks.compute_test_outcome("success", "skipped", '[["a", "b"]]') == (
        False,
        False,
    )
    # A malformed upgrade matrix with a skipped upgrade job fails closed
    assert promote_tracks.compute_test_outcome("success", "skipped", "[not-json") == (
        False,
        False,
    )


def test_record_test_result(tmp_path: Path):
    test_args = argparse.Namespace(
        proposal_name="k8s-1.36-classic/beta-amd64",
        track="1.36-classic",
        next_risk="beta",
        snap_channel="1.36-classic/beta",
        arch="amd64",
        revision="6058",
        e2e_result="success",
        upgrade_result="skipped",
        upgrade_channels="[]",
        output_dir=str(tmp_path),
    )
    with mock.patch("promote_tracks.core.set_output") as mock_set_output:
        file_path = promote_tracks.record_test_result(test_args)
        assert file_path.is_file()
        data = json.loads(file_path.read_text())
        assert data["name"] == "k8s-1.36-classic/beta-amd64"
        assert data["passed"] is True
        mock_set_output.assert_any_call("artifact-name", "test-result-k8s-1.36-classic.beta-amd64")
        mock_set_output.assert_any_call("artifact-path", str(file_path))


def test_install_snapcraft():
    # Dry run returns without executing
    with mock.patch("promote_tracks.subprocess.run") as mock_run:
        promote_tracks.install_snapcraft(argparse.Namespace(dry_run=True))
        mock_run.assert_not_called()

    # Success run
    with mock.patch("promote_tracks.subprocess.run") as mock_run:
        promote_tracks.install_snapcraft(argparse.Namespace(dry_run=False))
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0][-3:] == ["install", "snapcraft", "--classic"]
        assert mock_run.call_args[1] == {"timeout": 300, "check": True}

    # Failure with retry and abort
    mock_run = mock.Mock(side_effect=[subprocess.CalledProcessError(1, ["cmd"]), None, None])
    with (
        mock.patch("promote_tracks.subprocess.run", mock_run),
        mock.patch("promote_tracks.wait_fixed", return_value=lambda _: 0),
    ):
        promote_tracks.install_snapcraft(argparse.Namespace(dry_run=False))
        assert mock_run.call_count >= 2


def test_download_snap(tmp_path: Path):
    # Success run
    with mock.patch("promote_tracks.subprocess.run") as mock_run:
        promote_tracks.download_snap(argparse.Namespace(revision="6058", snap_name="k8s"))
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0][1:] == [
            "download",
            "k8s",
            "--revision=6058",
            "--basename",
            "k8s",
        ]
        assert mock_run.call_args[1] == {"timeout": 600, "check": True}

    # Failure with file cleanup and retry
    mock_run = mock.Mock(side_effect=[subprocess.CalledProcessError(1, ["cmd"]), None])
    with (
        mock.patch("promote_tracks.subprocess.run", mock_run),
        mock.patch("promote_tracks.wait_fixed", return_value=lambda _: 0),
    ):
        promote_tracks.download_snap(argparse.Namespace(revision="6058", snap_name="k8s"))
        assert mock_run.call_count == 2


@pytest.mark.parametrize(
    "preset, channels, expected",
    [
        (
            {},
            None,
            {
                "TEST_VERSION_UPGRADE_MIN_RELEASE": "1.31",
                "TEST_DEFAULT_WAIT_RETRIES": "200",
                "TEST_DEFAULT_WAIT_DELAY_S": "10",
            },
        ),
        (
            {"TEST_DEFAULT_WAIT_RETRIES": "5", "TEST_VERSION_UPGRADE_CHANNELS": "old"},
            "1.35-classic/stable 1.36-classic/edge@6058",
            {
                "TEST_DEFAULT_WAIT_RETRIES": "5",
                "TEST_VERSION_UPGRADE_CHANNELS": "1.35-classic/stable 1.36-classic/edge@6058",
            },
        ),
    ],
)
def test_execute_upgrade_test_environment(monkeypatch, preset, channels, expected):
    for key in (
        "TEST_VERSION_UPGRADE_MIN_RELEASE",
        "TEST_DEFAULT_WAIT_RETRIES",
        "TEST_DEFAULT_WAIT_DELAY_S",
        "TEST_VERSION_UPGRADE_CHANNELS",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in preset.items():
        monkeypatch.setenv(key, value)
    seen = {}

    def run_tox(args, tox, test_path, ref=None):
        seen.update({k: os.environ.get(k) for k in expected})
        assert tox == shlex.split("tox -e integration -- -k test_version_upgrades")

    with mock.patch("promote_tracks._run_tox_tests", side_effect=run_tox) as mock_run:
        promote_tracks.execute_upgrade_test(argparse.Namespace(channels=channels))

    mock_run.assert_called_once()
    assert seen == expected


@PATCH_PROFILES
def test_create_proposal_drops_channel_when_an_arch_is_stale():
    snap_info = {
        "channel-map": [
            _create_channel("1.36-classic", "edge", 6058, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "edge", 6059, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6050, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6050, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="arm64", date="2000-01-01"),
        ]
    }

    def mock_edge_is_stale(recipe, arch, edge_revision):
        return arch == "amd64"

    with (
        freeze_time("2000-01-05"),
        mock.patch("promote_tracks.snapstore.info", return_value=snap_info),
        mock.patch("util.lp.recipe_for_track", return_value=mock.Mock()),
        mock.patch("util.lp.retry_failed_builds", return_value=[]),
        mock.patch("util.lp.edge_is_stale", side_effect=mock_edge_is_stale),
        mock.patch("util.k8s.get_latest_stable", return_value="v1.36.0"),
    ):
        proposals = promote_tracks.create_proposal(args)

    beta_proposals = [p for p in proposals if p["snap-channel"] == "1.36-classic/beta"]
    assert beta_proposals == []


@PATCH_PROFILES
def test_create_proposal_keeps_channel_when_no_arch_is_stale():
    snap_info = {
        "channel-map": [
            _create_channel("1.36-classic", "edge", 6058, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "edge", 6059, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6050, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6050, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="arm64", date="2000-01-01"),
        ]
    }

    with (
        freeze_time("2000-01-05"),
        mock.patch("promote_tracks.snapstore.info", return_value=snap_info),
        mock.patch("util.lp.recipe_for_track", return_value=mock.Mock()),
        mock.patch("util.lp.retry_failed_builds", return_value=[]),
        mock.patch("util.lp.edge_is_stale", return_value=False),
        mock.patch("util.k8s.get_latest_stable", return_value="v1.36.0"),
    ):
        proposals = promote_tracks.create_proposal(args)

    beta_proposals = [p for p in proposals if p["snap-channel"] == "1.36-classic/beta"]
    assert len(beta_proposals) == 2
    assert {p["arch"] for p in beta_proposals} == {"amd64", "arm64"}


@PATCH_PROFILES
def test_create_proposal_legitimately_not_needed_arch_does_not_block():
    # amd64 already at beta revision 6058, arm64 at 6050 needing upgrade to 6058
    snap_info = {
        "channel-map": [
            _create_channel("1.36-classic", "edge", 6058, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "edge", 6058, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6058, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6050, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="arm64", date="2000-01-01"),
        ]
    }

    with (
        freeze_time("2000-01-05"),
        mock.patch("promote_tracks.snapstore.info", return_value=snap_info),
        mock.patch("util.lp.recipe_for_track", return_value=mock.Mock()),
        mock.patch("util.lp.retry_failed_builds", return_value=[]),
        mock.patch("util.lp.edge_is_stale", return_value=False),
        mock.patch("util.k8s.get_latest_stable", return_value="v1.36.0"),
    ):
        proposals = promote_tracks.create_proposal(args)

    beta_proposals = [p for p in proposals if p["snap-channel"] == "1.36-classic/beta"]
    assert len(beta_proposals) == 1
    assert beta_proposals[0]["arch"] == "arm64"


@PATCH_PROFILES
def test_create_proposal_ignore_arches_allows_single_arch():
    snap_info = {
        "channel-map": [
            _create_channel("1.36-classic", "edge", 6058, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "edge", 6059, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6050, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "beta", 6050, arch="arm64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="amd64", date="2000-01-01"),
            _create_channel("1.36-classic", "stable", 6040, arch="arm64", date="2000-01-01"),
        ]
    }
    args.ignore_arches = ["arm64"]

    with (
        freeze_time("2000-01-05"),
        mock.patch("promote_tracks.snapstore.info", return_value=snap_info),
        mock.patch("util.lp.recipe_for_track", return_value=mock.Mock()),
        mock.patch("util.lp.retry_failed_builds", return_value=[]),
        mock.patch("util.lp.edge_is_stale", return_value=False),
        mock.patch("util.k8s.get_latest_stable", return_value="v1.36.0"),
    ):
        proposals = promote_tracks.create_proposal(args)

    assert len(proposals) > 0
    assert all(p["arch"] == "amd64" for p in proposals)


def test_load_test_results(tmp_path: Path):
    assert promote_tracks.load_test_results(None) == {}
    assert promote_tracks.load_test_results(tmp_path / "nonexistent") == {}

    sub_dir = tmp_path / "subdir"
    sub_dir.mkdir()
    (sub_dir / "res1.json").write_text(json.dumps({"name": "prop-1", "passed": True}))
    (tmp_path / "res2.json").write_text(json.dumps({"name": "prop-2", "passed": False}))
    (tmp_path / "invalid.json").write_text("not json")
    (tmp_path / "list.json").write_text("[]")

    results = promote_tracks.load_test_results(tmp_path)
    assert len(results) == 2
    assert results["prop-1"]["passed"] is True
    assert results["prop-2"]["passed"] is False


def test_evaluate_channel_promotions_all_pass():
    proposals = [
        {"name": "p1", "snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 10},
        {"name": "p2", "snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 11},
    ]
    test_results = {
        "p1": {"name": "p1", "passed": True},
        "p2": {"name": "p2", "passed": True},
    }
    approved, blocked = promote_tracks.evaluate_channel_promotions(proposals, test_results)
    assert approved == proposals
    assert blocked == {}


def test_evaluate_channel_promotions_one_arch_fails_blocks_channel():
    proposals = [
        {"name": "p1", "snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 10},
        {"name": "p2", "snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 11},
    ]
    test_results = {
        "p1": {
            "name": "p1",
            "passed": False,
            "e2e_result": "failure",
            "upgrade_result": "success",
        },
        "p2": {"name": "p2", "passed": True},
    }
    approved, blocked = promote_tracks.evaluate_channel_promotions(proposals, test_results)
    assert approved == []
    assert "1.36-classic/beta" in blocked
    assert any("amd64" in r for r in blocked["1.36-classic/beta"])


def test_evaluate_channel_promotions_missing_result_counts_as_failed():
    proposals = [
        {"name": "p1", "snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 10},
        {"name": "p2", "snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 11},
    ]
    test_results = {
        "p2": {"name": "p2", "passed": True},
    }
    approved, blocked = promote_tracks.evaluate_channel_promotions(proposals, test_results)
    assert approved == []
    assert "1.36-classic/beta" in blocked
    assert any("no test results found" in r for r in blocked["1.36-classic/beta"])


def test_evaluate_channel_promotions_independent_channels():
    p_136 = [
        {"name": "p1", "snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 10},
        {"name": "p2", "snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 11},
    ]
    p_135 = [
        {"name": "p3", "snap-channel": "1.35-classic/beta", "arch": "amd64", "revision": 20},
        {"name": "p4", "snap-channel": "1.35-classic/beta", "arch": "arm64", "revision": 21},
    ]
    proposals = p_136 + p_135
    test_results = {
        "p1": {"name": "p1", "passed": False},
        "p2": {"name": "p2", "passed": True},
        "p3": {"name": "p3", "passed": True},
        "p4": {"name": "p4", "passed": True},
    }
    approved, blocked = promote_tracks.evaluate_channel_promotions(proposals, test_results)
    assert approved == p_135
    assert "1.36-classic/beta" in blocked
    assert "1.35-classic/beta" not in blocked


def test_evaluate_channel_promotions_single_arch_releasable():
    proposals = [
        {"name": "p1", "snap-channel": "1.34-classic/candidate", "arch": "amd64", "revision": 30},
    ]
    test_results = {"p1": {"name": "p1", "passed": True}}
    approved, blocked = promote_tracks.evaluate_channel_promotions(proposals, test_results)
    assert approved == proposals
    assert blocked == {}


def test_release_snap_and_dry_run():
    with mock.patch("promote_tracks.subprocess.run") as mock_run:
        promote_tracks.release_snap(6058, "1.36-classic/beta", dry_run=True)
        mock_run.assert_not_called()

        promote_tracks.release_snap(6058, "1.36-classic/beta", dry_run=False)
        mock_run.assert_called_once_with(
            ["/snap/bin/snapcraft", "release", "k8s", "6058", "1.36-classic/beta"],
            check=True,
            timeout=promote_tracks.SNAPCRAFT_RELEASE_TIMEOUT_S,
        )

    mock_run = mock.Mock(side_effect=[subprocess.CalledProcessError(1, ["cmd"]), None])
    with (
        mock.patch("promote_tracks.subprocess.run", mock_run),
        mock.patch("promote_tracks.wait_fixed", return_value=lambda _: 0),
    ):
        promote_tracks.release_snap(6058, "1.36-classic/beta", dry_run=False)
        assert mock_run.call_count == 2


def test_promote_evaluated_proposals(tmp_path: Path):
    proposals = [
        {"name": "p1", "snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 10},
    ]
    (tmp_path / "p1.json").write_text(json.dumps({"name": "p1", "passed": True}))

    with (
        mock.patch("promote_tracks.release_snap") as mock_release,
        mock.patch("promote_tracks._create_channel_map", return_value={}),
    ):
        approved = promote_tracks.promote_evaluated_proposals(proposals, tmp_path, dry_run=False)
        assert approved == proposals
        mock_release.assert_called_once_with(10, "1.36-classic/beta", dry_run=False)


def _channel(revision, version="v1.36.6"):
    return mock.Mock(revision=revision, version=version)


def _passing(tmp_path: Path, proposals: list[dict]) -> None:
    for p in proposals:
        (tmp_path / f"{p['name']}.json").write_text(
            json.dumps({"name": p["name"], "passed": True})
        )


def test_newest_per_target_keeps_newest_revision():
    # Fast path from edge and an aged beta both target candidate for amd64.
    beta = {"snap-channel": "1.37-classic/candidate", "arch": "amd64", "revision": 6050}
    edge = {"snap-channel": "1.37-classic/candidate", "arch": "amd64", "revision": 6062}
    arm64 = {"snap-channel": "1.37-classic/candidate", "arch": "arm64", "revision": 6069}
    stable = {"snap-channel": "1.37-classic/stable", "arch": "amd64", "revision": 6050}
    assert promote_tracks._newest_per_target([edge, beta, arm64, stable]) == [edge, arm64, stable]
    assert promote_tracks._newest_per_target([beta, edge]) == [edge]


def _proposal(name, arch, revision, version):
    return {
        "name": name,
        "snap-channel": "1.34-classic/candidate",
        "arch": arch,
        "revision": revision,
        "version": version,
    }


@pytest.mark.parametrize(
    "extra, arm64_channel, ignore_arches, blocked",
    [
        ([], _channel(5830, "v1.34.12"), "", True),  # arm64 would stay on the old version
        ([], _channel(5830, "v1.34.13"), "", False),  # arm64 already on the new version
        ([], None, "", False),  # arm64 never published in this channel
        ([], _channel(5830, "v1.34.12"), "arm64", False),  # arm64 explicitly ignored
        # arm64 proposed, but on another version than amd64
        ([_proposal("b", "arm64", 6074, "v1.34.12")], _channel(5830, "v1.34.12"), "", True),
        # two proposals for amd64
        ([_proposal("b", "amd64", 6070, "v1.34.13")], None, "", True),
    ],
)
def test_promote_blocks_channel_split_across_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra, arm64_channel, ignore_arches, blocked
):
    monkeypatch.setenv("IGNORE_ARCHES", ignore_arches)
    proposals = [_proposal("a", "amd64", 6066, "v1.34.13"), *extra]
    _passing(tmp_path, proposals)
    channel_map = {
        "amd64": {"1.34-classic/candidate": _channel(5945, "v1.34.12")},
        "arm64": {"1.34-classic/candidate": arm64_channel} if arm64_channel else {},
    }
    with (
        mock.patch("promote_tracks.release_snap") as mock_release,
        mock.patch("promote_tracks._create_channel_map", return_value=channel_map),
    ):
        released = promote_tracks.promote_evaluated_proposals(proposals, tmp_path)
    assert released == ([] if blocked else proposals)
    assert mock_release.called is not blocked


@pytest.mark.parametrize(
    "live_revision, blocked",
    [
        (5945, False),  # live revision older than the proposal: safe to release
        (6066, True),  # live revision equals the proposal: already released by another run
        (6070, True),  # live revision newer than the proposal: stale, would be a downgrade
    ],
)
def test_promote_blocks_stale_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_revision, blocked
):
    monkeypatch.setenv("IGNORE_ARCHES", "")
    proposals = [_proposal("a", "amd64", 6066, "v1.34.13")]
    _passing(tmp_path, proposals)
    channel_map = {"amd64": {"1.34-classic/candidate": _channel(live_revision, "v1.34.12")}}
    with (
        mock.patch("promote_tracks.release_snap") as mock_release,
        mock.patch("promote_tracks._create_channel_map", return_value=channel_map),
    ):
        released = promote_tracks.promote_evaluated_proposals(proposals, tmp_path)
    assert released == ([] if blocked else proposals)
    assert mock_release.called is not blocked


def test_promote_rolls_back_partially_released_channel(tmp_path: Path):
    proposals = [
        {"name": "a", "snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 6058},
        {"name": "b", "snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 6059},
        {"name": "c", "snap-channel": "1.35-classic/beta", "arch": "amd64", "revision": 6000},
    ]
    for name in "abc":
        (tmp_path / f"{name}.json").write_text(json.dumps({"name": name, "passed": True}))
    channel_map = {
        "amd64": {"1.36-classic/beta": _channel(5934), "1.35-classic/beta": _channel(5900)},
        "arm64": {"1.36-classic/beta": _channel(5935)},
    }

    def release(revision, channel, dry_run=False):
        if revision == 6059:
            raise subprocess.CalledProcessError(1, ["snapcraft"])

    with (
        mock.patch("promote_tracks.release_snap", side_effect=release) as mock_release,
        mock.patch("promote_tracks._create_channel_map", return_value=channel_map),
        pytest.raises(promote_tracks.ChannelReleaseError, match="amd64: rolled back to r5934"),
    ):
        promote_tracks.promote_evaluated_proposals(proposals, tmp_path, dry_run=False)

    assert mock_release.call_args_list == [
        mock.call(6058, "1.36-classic/beta", dry_run=False),
        mock.call(6059, "1.36-classic/beta", dry_run=False),
        mock.call(5934, "1.36-classic/beta", dry_run=False),
        mock.call(5935, "1.36-classic/beta", dry_run=False),
        mock.call(6000, "1.35-classic/beta", dry_run=False),
    ]


def test_promote_defers_second_unrecoverable_arch(tmp_path: Path):
    # Both arches are new to this channel (e.g. the track's first beta release), so neither
    # can be rolled back; only one goes out this run, in case the other fails.
    proposals = [
        {
            "name": "a",
            "snap-channel": "1.36-classic/beta",
            "arch": "amd64",
            "revision": 6058,
            "version": "v1.36.0",
        },
        {
            "name": "b",
            "snap-channel": "1.36-classic/beta",
            "arch": "arm64",
            "revision": 6059,
            "version": "v1.36.0",
        },
    ]
    _passing(tmp_path, proposals)
    with (
        mock.patch("promote_tracks.release_snap") as mock_release,
        mock.patch("promote_tracks._create_channel_map", return_value={}),
    ):
        released = promote_tracks.promote_evaluated_proposals(proposals, tmp_path)
    assert released == [proposals[0]]
    mock_release.assert_called_once_with(6058, "1.36-classic/beta", dry_run=False)


def test_release_channel_group_releases_unrecoverable_arch_last():
    group = [
        {"snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 6058},
        {"snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 6059},
    ]
    # arm64 fails with an OSError (e.g. snapcraft missing) before amd64 is attempted.
    side_effect = [FileNotFoundError("snapcraft"), None]
    with mock.patch("promote_tracks.release_snap", side_effect=side_effect) as mock_release:
        error = promote_tracks.release_channel_group(
            "1.36-classic/beta", group, {"amd64": None, "arm64": 5935}
        )
    assert error is not None
    assert "arm64: rolled back to r5935" in error
    assert "amd64" not in error.split(";", 1)[1]
    assert mock_release.call_args_list == [
        mock.call(6059, "1.36-classic/beta", dry_run=False),
        mock.call(5935, "1.36-classic/beta", dry_run=False),
    ]


def test_release_channel_group_reports_arch_without_previous_revision():
    group = [
        {"snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 6058},
        {"snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 6059},
    ]
    side_effect = [None, subprocess.CalledProcessError(1, ["snapcraft"]), None]
    with mock.patch("promote_tracks.release_snap", side_effect=side_effect) as mock_release:
        error = promote_tracks.release_channel_group(
            "1.36-classic/beta", group, {"amd64": None, "arm64": 5935}
        )
    assert error is not None
    assert "amd64: no previous revision in 1.36-classic/beta, may remain at r6058" in error
    assert "arm64: rolled back to r5935" in error
    assert mock_release.call_args_list == [
        mock.call(6059, "1.36-classic/beta", dry_run=False),
        mock.call(6058, "1.36-classic/beta", dry_run=False),
        mock.call(5935, "1.36-classic/beta", dry_run=False),
    ]


def test_release_channel_group_rolls_back_on_store_timeout():
    group = [
        {"snap-channel": "1.36-classic/beta", "arch": "amd64", "revision": 6058},
        {"snap-channel": "1.36-classic/beta", "arch": "arm64", "revision": 6059},
    ]
    side_effect = [None, subprocess.TimeoutExpired(["snapcraft"], 300), None, None]
    with mock.patch("promote_tracks.release_snap", side_effect=side_effect) as mock_release:
        error = promote_tracks.release_channel_group(
            "1.36-classic/beta", group, {"amd64": 5934, "arm64": 5935}
        )
    assert error is not None
    # The timed-out arm64 release may have been applied, so it is restored too.
    assert "amd64: rolled back to r5934; arm64: rolled back to r5935" in error
    assert mock_release.call_args_list[2:] == [
        mock.call(5934, "1.36-classic/beta", dry_run=False),
        mock.call(5935, "1.36-classic/beta", dry_run=False),
    ]


def test_release_revision_cli_modes(tmp_path: Path):
    # Single-revision mode
    with mock.patch("promote_tracks.release_snap") as mock_release:
        single_args = argparse.Namespace(
            snap_revision="6058", snap_channel="1.36-classic/beta", dry_run=False
        )
        promote_tracks.release_revision(single_args)
        mock_release.assert_called_once_with("6058", "1.36-classic/beta", dry_run=False)

    # Proposals JSON string mode
    proposals = [{"name": "p1", "snap-channel": "1.36-classic/beta", "revision": 6058}]
    with mock.patch("promote_tracks.promote_evaluated_proposals") as mock_eval:
        str_args = argparse.Namespace(
            proposals=json.dumps(proposals), results_dir="res_dir", dry_run=True
        )
        promote_tracks.release_revision(str_args)
        mock_eval.assert_called_once_with(proposals, "res_dir", dry_run=True)

    # Proposals JSON file mode
    prop_file = tmp_path / "proposals.json"
    prop_file.write_text(json.dumps(proposals))
    with mock.patch("promote_tracks.promote_evaluated_proposals") as mock_eval:
        file_args = argparse.Namespace(proposals=str(prop_file), results_dir=None, dry_run=False)
        promote_tracks.release_revision(file_args)
        mock_eval.assert_called_once_with(proposals, None, dry_run=False)

    # Missing both raises ValueError
    with pytest.raises(ValueError, match="Must specify either"):
        promote_tracks.release_revision(argparse.Namespace())
