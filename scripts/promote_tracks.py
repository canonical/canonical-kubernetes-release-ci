#!/usr/bin/env python3
"""Promote revisions of the Canonical Kubernetes snap through the risk levels of each track."""

import argparse
import contextlib
import dataclasses
import datetime
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import Optional

import util.k8s as k8s
import util.lp as lp
import util.promotion_matrix as promotion_matrix
import util.repo as repo
import util.snapstore as snapstore
import util.util as util
from actions_toolkit import core
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed

USAGE = "./promote_tracks.py"

DESCRIPTION = """
Promote revisions of the Canonical Kubernetes snap through the risk levels of each track.
Expects snapcraft to be logged in with sufficient permissions, if not dry-running.
The script only targets releases. The 'latest' track is ignored.
Each revision is promoted after being at a risk level for a certain amount of days.
The script will only promote a revision to stable if there is already another revision
   for this track at stable.
The first stable release for each track requires blessing from SolQA and is promoted manually.
--ignore-tracks entries are `PATTERN` (frozen: never promoted at any risk level) or
   `PATTERN:CEILING` (capped: promoted normally up through CEILING, never past it),
   where CEILING is one of edge, beta, or candidate.
"""

# Tracks matching a bare pattern are frozen (never promoted at any risk level).
# Tracks matching a `(pattern, ceiling)` pair are capped at that risk level.
IGNORE_TRACKS: list[tuple[str, Optional[str]]] = [
    ("latest", None),
]

# Default ignore tracks applied for automatic scheduled promotions.
AUTO_PROMOTION_IGNORE_TRACKS = [
    r"^1\.\d{2}$",
    r"^1\.\d{2}-moonray$",
    r"^1\.30(-.*)?$",
    r"^1\.31(-.*)?$",
    r"1\.36-classic:candidate",
    r"1\.37-classic:candidate",
]
# The snap risk levels, used to find the next risk level for a revision.
RISK_LEVELS = ["edge", "beta", "candidate", "stable"]
NEXT_RISK = RISK_LEVELS[1:] + [None]

# Revisions stay at a certain risk level for some days before being promoted.
DAYS_TO_STAY_IN_EDGE = 1
DAYS_TO_STAY_IN_BETA = 1
DAYS_TO_STAY_IN_CANDIDATE = 1

TRACK_RE = re.compile(r"^(\d+)\.(\d+)(\S*)$")


def _parse_ignore_track(entry: str) -> tuple[str, Optional[str]]:
    """Parse a `--ignore-tracks` entry: `PATTERN` (frozen) or `PATTERN:CEILING`.

    CEILING is the highest risk level (edge, beta, or candidate) the track may
    ever be promoted to; the track is never promoted past it. A bare pattern
    with no `:CEILING` suffix freezes the track at every risk level.
    """
    pattern, sep, ceiling = entry.rpartition(":")
    if not sep or not ceiling.isalpha():
        # No `:` at all, or the tail isn't a bare word (e.g. a `(?:...)` group
        # inside the regex itself) — treat the whole entry as the pattern.
        return entry, None
    if ceiling not in RISK_LEVELS[:-1]:
        raise argparse.ArgumentTypeError(
            f"Invalid risk ceiling '{ceiling}' in --ignore-tracks entry '{entry}'; "
            f"must be one of {RISK_LEVELS[:-1]}"
        )
    return pattern, ceiling


class ProposalTestError(Exception):
    """Exception raised when a proposal test fails."""

    pass


class Hyphenized:
    """Base class for dataclasses that convert hyphenated keys to snake_case."""

    @classmethod
    def bake(cls, *args, **kwargs):
        """Create an instance of the class, converting hyphenated keys to snake_case."""
        return cls(*args, **{s.replace("-", "_").lower(): v for s, v in kwargs.items()})


@dataclasses.dataclass
class ChannelMetadata(Hyphenized):
    """Metadata for a snap or charm revision channel."""

    name: Optional[str] = None
    track: Optional[str] = None
    risk: Optional[str] = None
    architecture: Optional[str] = None
    released_at: Optional[str] = None


@dataclasses.dataclass
class Channel(Hyphenized):
    """A channel of a snap or charm revision."""

    channel: ChannelMetadata
    created_at: Optional[str] = None
    revision: Optional[int] = None
    version: Optional[str] = None
    type: Optional[str] = None
    download: Optional[dict] = dataclasses.field(default_factory=dict)

    @cached_property
    def next_risk(self):
        """Get the next risk level for this channel."""
        return NEXT_RISK[RISK_LEVELS.index(self.risk)]

    def __getattr__(self, name):
        """Allow access to channel metadata attributes directly."""
        return getattr(self.channel, name)


EMPTY_CHANNEL = Channel(channel=ChannelMetadata())


def _build_upgrade_channels(channel: Channel, channels: dict[str, Channel]) -> list[list[str]]:
    """Build the upgrade channels for a proposal within this architecture.

    At most, there will be three validation tests:
    - Upgrade from the next risk to this risk within the channel
      (simulates a snap refresh)
    - Upgrade from the highest risk in this track to this risk
      (confirms this can replace the highest risk'd snap)
    - Upgrade from the highest risk in prior track to this risk
      (confirms this can replace the highest prior risk'd snap)

    If the starting revision doesn't exist, the test is skipped.

    Args:
        channel:  The current snap revision to promote
        channels: All channels of the snap with the same arch

    Returns:
        A valid list of upgrade proposal stages.

    """
    track = channel.track
    next_risk = channel.next_risk

    # The next risk on this track
    next_channel = f"{track}/{next_risk}"
    source_channels = set()
    if next_channel in channels:
        source_channels |= {next_channel}

    # First highest risk on this track (excluding next-risk)
    same_track_channels = [
        f"{track}/{r}" for idx, r in enumerate(RISK_LEVELS) if idx > RISK_LEVELS.index(next_risk)
    ]
    for source in reversed(same_track_channels):
        if source in channels:
            source_channels |= {source}
            break

    # First highest risk on the previous track
    if match := TRACK_RE.match(track):
        maj, min, tail = match.groups()
    else:
        raise ValueError(f"Invalid track name: {track}")
    prior_track = f"{maj}.{int(min) - 1}{tail}"
    prior_track_channels = [f"{prior_track}/{r}" for r in RISK_LEVELS]
    for source in reversed(prior_track_channels):
        if source in channels:
            source_channels |= {source}
            break

    if not source_channels:
        LOG.info("Without anything to upgrade from, just bootstrap on %s", channel.name)
        return [[channel.name]]

    # Only run tests on revision changes
    return [
        [source, f"{channel.name}@{channel.revision}"]
        for source in sorted(source_channels)
        if channel.revision != channels[source].revision
    ]


def _create_channel_map():
    snap_info = snapstore.info(util.SNAP_NAME)
    channel_map: dict[str, dict[str, Channel]] = defaultdict(dict)

    for c in snap_info["channel-map"]:
        channel_data = ChannelMetadata.bake(**c.pop("channel"))
        revision_data = Channel.bake(channel=channel_data, **c)
        channel_map[channel_data.architecture][channel_data.name] = revision_data

    return channel_map


def resolve_propose_inputs(args) -> argparse.Namespace:
    """Resolve propose parameters from CLI arguments and environment variables."""
    days_edge = getattr(args, "days_in_edge_risk", None)
    if days_edge is None or days_edge == DAYS_TO_STAY_IN_EDGE:
        if env_val := os.getenv("DAYS_IN_EDGE_RISK"):
            with contextlib.suppress(ValueError):
                args.days_in_edge_risk = int(env_val)

    days_beta = getattr(args, "days_in_beta_risk", None)
    if days_beta is None or days_beta == DAYS_TO_STAY_IN_BETA:
        if env_val := os.getenv("DAYS_IN_BETA_RISK"):
            with contextlib.suppress(ValueError):
                args.days_in_beta_risk = int(env_val)

    days_cand = getattr(args, "days_in_candidate_risk", None)
    if days_cand is None or days_cand == DAYS_TO_STAY_IN_CANDIDATE:
        if env_val := os.getenv("DAYS_IN_CANDIDATE_RISK"):
            with contextlib.suppress(ValueError):
                args.days_in_candidate_risk = int(env_val)

    if not getattr(args, "ignore_arches", None):
        if env_arches := os.getenv("IGNORE_ARCHES"):
            args.ignore_arches = env_arches.split()

    if getattr(args, "ignore_tracks", None) is None:
        if env_tracks := os.getenv("IGNORE_TRACKS"):
            args.ignore_tracks = [_parse_ignore_track(t) for t in env_tracks.split() if t]
        elif os.getenv("GITHUB_EVENT_NAME") == "schedule":
            args.ignore_tracks = [_parse_ignore_track(t) for t in AUTO_PROMOTION_IGNORE_TRACKS]
        else:
            args.ignore_tracks = []

    return args


def create_proposal(args):
    args = resolve_propose_inputs(args)
    channel_map = _create_channel_map()
    proposals = []

    for arch, channels in channel_map.items():
        proposals.extend(_create_arch_proposals(arch, channels, args))

    if args.gh_action:
        core.set_output("proposals", json.dumps(proposals))
    return proposals


def _create_arch_proposals(arch, channels: dict[str, Channel], args):
    proposals = []
    ignored_tracks = IGNORE_TRACKS + getattr(args, "ignore_tracks", [])
    ignored_arches = getattr(args, "ignore_arches", [])
    days_to_stay_in_risk = {
        "edge": args.days_in_edge_risk,
        "beta": args.days_in_beta_risk,
        "candidate": args.days_in_candidate_risk,
    }
    unrestricted = len(RISK_LEVELS) - 1  # index of "stable": no promotion cap

    def sorter(info: Channel):
        return (info.name, RISK_LEVELS.index(info.risk))

    def risk_ceiling(track: str) -> tuple[int, Optional[str]]:
        """Return (max risk index the track may reach, matched pattern).

        -1 means the track is frozen (never promoted at any risk level).
        `unrestricted` means the track has no cap (default, no match).
        The most restrictive matching entry wins, so a built-in default can be
        tightened by a `--ignore-tracks` argument but never loosened by one.
        """
        matches = [
            ((-1 if ceiling is None else RISK_LEVELS.index(ceiling)), pattern)
            for pattern, ceiling in ignored_tracks
            if re.fullmatch(pattern, track)
        ]
        return min(matches, default=(unrestricted, None))

    latest_upstream_stable = k8s.get_latest_stable()
    for channel_info in sorted(channels.values(), key=sorter, reverse=True):
        track = channel_info.channel.track
        risk = channel_info.risk
        next_risk = channel_info.next_risk
        revision = channel_info.revision
        chan_log = logging.getLogger(f"{logger_name} {track:>15}/{risk:<9}")

        if not track:
            chan_log.debug("Skipping trackless channel")
            continue

        if not next_risk:
            chan_log.debug("Skipping promoting stable")
            continue

        max_risk_index, matched = risk_ceiling(track)
        if max_risk_index < 0:
            chan_log.debug(f"Skipping ignored track '{track}' (matched pattern: '{matched}')")
            continue

        if arch in ignored_arches:
            chan_log.debug("Skipping ignored architecture")
            continue

        held = max_risk_index < unrestricted
        if RISK_LEVELS.index(next_risk) > max_risk_index:
            chan_log.info(
                f"Skipping promotion of track '{track}' past {RISK_LEVELS[max_risk_index]} "
                f"(matched pattern: '{matched}')"
            )
            continue

        if risk == "edge":
            if recipe := lp.recipe_for_track(util.SNAP_NAME, track):
                retried = lp.retry_failed_builds(recipe, arch=arch, dry_run=args.dry_run)
                if retried or lp.edge_is_stale(recipe, arch=arch, edge_revision=revision):
                    chan_log.info(
                        "Skipping promotion of stale edge rev=%-5s arch=%s "
                        "(recipe git_ref not contained in edge or latest build failed/pending)",
                        revision,
                        arch,
                    )
                    continue

        now = datetime.datetime.now(datetime.timezone.utc)

        if released_at := channel_info.channel.released_at:
            released_at_date = datetime.datetime.fromisoformat(released_at)
        else:
            released_at_date = None

        chan_log.debug(
            "Evaluate rev=%-5s arch=%s released at %s",
            revision,
            arch,
            released_at_date,
        )

        purgatory_complete = (
            released_at_date
            and (now - released_at_date).days >= days_to_stay_in_risk[risk]
            and channels.get(f"{track}/{risk}", EMPTY_CHANNEL).revision
            != channels.get(f"{track}/{next_risk}", EMPTY_CHANNEL).revision
        )
        new_patch_in_edge = (
            risk == "edge"
            and channels.get(f"{track}/{next_risk}", EMPTY_CHANNEL).version
            != channels.get(f"{track}/{risk}", EMPTY_CHANNEL).version
        )
        revision_in_stable = bool(channels.get(f"{track}/stable", EMPTY_CHANNEL).revision)

        def _get_proposal(next_risk):
            final_channel = f"{track}/{next_risk}"

            proposal = {}
            proposal["arch"] = arch
            proposal["branch"] = lp.branch_from_track(util.SNAP_NAME, track)
            proposal["upgrade-channels"] = _build_upgrade_channels(channel_info, channels)
            proposal["revision"] = revision
            proposal["track"] = track
            proposal["next-risk"] = next_risk
            proposal["snap-channel"] = final_channel
            proposal["name"] = f"{util.SNAP_NAME}-{track}/{next_risk}-{arch}"
            profile = promotion_matrix.profile_for_track(track)
            proposal["runner-labels"] = [profile.runner_for(arch)]
            proposal["lxd-images"] = [f"ubuntu:{s}" for s in profile.lxd_series[next_risk]]
            return proposal

        # Whenever there's a new stable upstream release, we'll skip purgatory
        # and promote it to all risk levels, including stable.
        # We'll only do this for the latest upstream release
        # and channels that do not have a stable release yet.
        # A track capped below stable never takes this fast path: since it
        # never reaches stable, revision_in_stable would stay False forever,
        # and this branch would otherwise re-fire on every new patch, skipping
        # the beta/candidate purgatory soak entirely.
        if not revision_in_stable:
            k8s_version = channel_info.version

            if new_patch_in_edge and k8s_version == latest_upstream_stable and not held:
                chan_log.info(
                    f"{track}/edge contains a stable upstream release: {k8s_version}, "
                    "we'll skip purgatory and promote it to all risk levels "
                    "(including stable)."
                )
                for proposed_risk in ("beta", "candidate", "stable"):
                    chan_log.info(
                        "Promotes rev=%-5s arch=%s to %s",
                        revision,
                        arch,
                        proposed_risk,
                    )
                    proposal = _get_proposal(next_risk=proposed_risk)
                    proposals.append(proposal)
                continue

            if not k8s.is_stable_release(k8s_version):
                chan_log.info(
                    f"{track}/{risk} contains pre-release: {k8s_version}, "
                    "automatic promotion disabled."
                )
                continue
        if purgatory_complete or new_patch_in_edge:
            chan_log.info(
                "Promotes rev=%-5s arch=%s to %s",
                revision,
                arch,
                next_risk,
            )
            proposal = _get_proposal(next_risk=next_risk)
            proposals.append(proposal)
    return proposals


def release_revision(args):
    # Note: we cannot use `snapcraft promote` here because it does not allow
    # to promote from edge to beta without manual confirmation.
    revision, channel = args.snap_revision, args.snap_channel
    LOG.info("Promote r%s to %s%s", revision, channel, args.dry_run and " (dry-run)" or "")
    if args.dry_run:
        return
    subprocess.run(
        ["/snap/bin/snapcraft", "release", util.SNAP_NAME, revision, channel],
        check=True,
    )


def install_snapcraft(args=None):
    """Install snapcraft classic with retries and abort recovery."""
    dry_run = getattr(args, "dry_run", False) if args else False
    if dry_run:
        LOG.info("Skipping snapcraft installation (dry-run)")
        return
    sudo_bin = shutil.which("sudo") or "/usr/bin/sudo"
    snap_bin = shutil.which("snap") or "/usr/bin/snap"
    retrier = Retrying(
        stop=stop_after_attempt(3),
        wait=wait_fixed(10),
        reraise=True,
    )
    for attempt in retrier:
        with attempt:
            LOG.info(
                "Attempt %d of 3: installing snapcraft...",
                attempt.retry_state.attempt_number,
            )
            try:
                subprocess.run(  # nosec B607
                    [sudo_bin, snap_bin, "install", "snapcraft", "--classic"],
                    timeout=300,
                    check=True,
                )
                LOG.info("Snapcraft installed successfully.")
                return
            except (subprocess.SubprocessError, OSError) as e:
                LOG.warning("Snapcraft install failed (%s); aborting pending install...", e)
                with contextlib.suppress(subprocess.SubprocessError, OSError):
                    subprocess.run(  # nosec B607
                        [sudo_bin, snap_bin, "abort", "--last=install"], timeout=30, check=False
                    )
                raise


def download_snap(args):
    """Download snap revision with retries."""
    revision = str(args.revision)
    snap_name = getattr(args, "snap_name", util.SNAP_NAME)
    snap_bin = shutil.which("snap") or "/usr/bin/snap"
    retrier = Retrying(
        stop=stop_after_attempt(3),
        wait=wait_fixed(10),
        reraise=True,
    )
    for attempt in retrier:
        with attempt:
            LOG.info(
                "Attempt %d of 3: downloading snap revision %s...",
                attempt.retry_state.attempt_number,
                revision,
            )
            try:
                subprocess.run(  # nosec B607
                    [
                        snap_bin,
                        "download",
                        snap_name,
                        f"--revision={revision}",
                        "--basename",
                        snap_name,
                    ],
                    timeout=600,
                    check=True,
                )
                LOG.info("Snap revision %s downloaded successfully.", revision)
                return
            except (subprocess.SubprocessError, OSError) as e:
                LOG.warning("Snap download failed (%s); cleaning up...", e)
                for f in (Path(f"{snap_name}.snap"), Path(f"{snap_name}.assert")):
                    if f.exists():
                        f.unlink(missing_ok=True)
                raise


def run_e2e_test(args):
    """Run e2e tests (no retry)."""
    execute_e2e_test(args)


def retry_upgrade_test(args):
    """Retry version upgrade tests."""
    retrier = Retrying(
        stop=stop_after_attempt(args.max_attempts),
        retry=retry_if_exception_type(ProposalTestError),
        wait=wait_fixed(1),
        before=lambda state: core.start_group(f"Attempt {state.attempt_number}"),
        after=lambda _: core.end_group(),
        reraise=True,
    )
    retrier(execute_upgrade_test, args)


def _snap_commit(snap_path: Optional[str]) -> Optional[str]:
    """Read the k8s-snap git commit SHA from bom.json inside a snap file."""
    unsquashfs = shutil.which("unsquashfs")
    if not snap_path or not Path(snap_path).is_file() or not unsquashfs:
        return None
    try:
        raw = subprocess.check_output(
            [unsquashfs, "-n", "-cat", snap_path, "bom.json"],
            stderr=subprocess.DEVNULL,
        )
        revision = json.loads(raw)["k8s"]["revision"]
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError) as e:
        LOG.warning("Could not read git revision from %s: %s", snap_path, e)
        return None
    if not isinstance(revision, str) or not repo.is_commit_sha(revision):
        LOG.warning("Invalid git revision %r in %s bom.json", revision, snap_path)
        return None
    return revision


def _run_tox_tests(args, tox: list[str], test_path: Path, ref: Optional[str] = None):
    """Run tox tests with the given command and test path."""
    try:
        # Probe for tox by attempting to run it; FileNotFoundError means it's not installed.
        subprocess.run(
            tox[:1] + ["--version"],  # tox --version
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        LOG.info("`tox` not found in PATH, skipping integration tests")
        raise

    with contextlib.ExitStack() as stack:
        target_ref = ref or args.branch
        if ref:
            try:
                dir = stack.enter_context(repo.clone(util.SNAP_REPO, ref))
            except subprocess.CalledProcessError as e:
                LOG.warning(
                    "Failed to clone %s at commit %s (%s); falling back to %s",
                    util.SNAP_REPO,
                    ref,
                    e,
                    args.branch,
                )
                target_ref = args.branch
                dir = stack.enter_context(repo.clone(util.SNAP_REPO, target_ref))
        else:
            dir = stack.enter_context(repo.clone(util.SNAP_REPO, target_ref))

        if repo.ls_tree(dir, test_path):
            LOG.info("Running integration tests for %s", target_ref)
            try:
                subprocess.run(tox, cwd=dir / "tests/integration", check=True)
            except subprocess.CalledProcessError as e:
                raise ProposalTestError(f"Integration tests failed: {e}") from e
            return
        else:
            LOG.error("No integration tests found for %s, failing", target_ref)
            raise ProposalTestError("No integration tests found")


RISK_TO_TAG = {
    "beta": "edge_to_beta",
    "candidate": "beta_to_candidate",
    "stable": "candidate_to_stable",
}


def execute_e2e_test(args):
    """Execute e2e tests."""
    tag = RISK_TO_TAG.get(args.next_risk)
    if not tag:
        raise ValueError(f"Unknown risk level '{args.next_risk}'")

    tox = shlex.split(f"tox -e integration -- --tags {tag}")
    test_path = Path("tests/integration/tests/")
    commit = _snap_commit(os.getenv("TEST_SNAP"))
    _run_tox_tests(args, tox, test_path, ref=commit)


def execute_upgrade_test(args):
    """Execute version upgrade tests."""
    os.environ.setdefault("TEST_VERSION_UPGRADE_MIN_RELEASE", "1.31")
    os.environ.setdefault("TEST_DEFAULT_WAIT_RETRIES", "200")
    os.environ.setdefault("TEST_DEFAULT_WAIT_DELAY_S", "10")
    if getattr(args, "channels", None):
        os.environ["TEST_VERSION_UPGRADE_CHANNELS"] = args.channels
    tox = shlex.split("tox -e integration -- -k test_version_upgrades")
    test_path = Path("tests/integration/tests/test_version_upgrades.py")
    _run_tox_tests(args, tox, test_path)


def build_parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(Path(__file__).name, usage=USAGE, description=DESCRIPTION)
    subparsers = arg_parser.add_subparsers(required=True)
    propose_args = subparsers.add_parser("propose", help="Propose revisions for promotion")
    propose_args.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Do not trigger Launchpad retries when proposing promotions",
    )
    propose_args.add_argument(
        "--gh-action",
        action="store_true",
        help="Output the proposals to be used in a GitHub Action",
    )
    propose_args.add_argument(
        "--days-in-edge-risk",
        type=int,
        help="The number of days a revision stays in edge risk",
        default=DAYS_TO_STAY_IN_EDGE,
    )
    propose_args.add_argument(
        "--days-in-beta-risk",
        type=int,
        help="The number of days a revision stays in beta risk",
        default=DAYS_TO_STAY_IN_BETA,
    )
    propose_args.add_argument(
        "--days-in-candidate-risk",
        type=int,
        help="The number of days a revision stays in candidate risk",
        default=DAYS_TO_STAY_IN_CANDIDATE,
    )
    propose_args.add_argument(
        "--ignore-tracks",
        nargs="*",
        type=_parse_ignore_track,
        help=(
            "Tracks to ignore when proposing revisions. Each entry is a fullmatch regex, "
            "optionally suffixed `:CEILING` (edge, beta, or candidate) to cap the track at "
            "that risk level instead of freezing it entirely, e.g. "
            r"'1\.36-classic:candidate' holds a track at candidate, never promoting to stable."
        ),
        # None means "not given", so resolve_propose_inputs can apply env/schedule defaults.
        default=None,
    )
    propose_args.add_argument(
        "--ignore-arches",
        nargs="*",
        help="Architectures to ignore when proposing revisions",
        default=[],
    )
    propose_args.set_defaults(func=create_proposal)

    test_parser = subparsers.add_parser("test", help="Run tests for a proposal")
    test_subparsers = test_parser.add_subparsers(required=True)

    test_e2e_args = test_subparsers.add_parser("e2e", help="Run e2e tests")
    test_e2e_args.add_argument("--branch", required=True, help="The branch from which to test")
    test_e2e_args.add_argument(
        "--next-risk",
        required=True,
        help="The next risk level (e.g., beta, candidate, stable)",
        dest="next_risk",
    )
    test_e2e_args.set_defaults(func=run_e2e_test)

    test_upgrade_args = test_subparsers.add_parser("upgrade", help="Run version upgrade tests")
    test_upgrade_args.add_argument("--branch", required=True, help="The branch from which to test")
    test_upgrade_args.add_argument(
        "--max-attempts",
        type=int,
        help="Maximum number of attempts for retrying failed tests",
        default=3,
    )

    test_upgrade_args.add_argument(
        "--channels", required=False, help="Space-separated list of upgrade channels"
    )
    test_upgrade_args.set_defaults(func=retry_upgrade_test)

    promote_args = subparsers.add_parser("promote", help="Promote the proposed revisions")
    promote_args.add_argument(
        "--snap-revision",
        required=True,
        help="The snap revision to promote",
        dest="snap_revision",
    )
    promote_args.add_argument(
        "--snap-channel",
        required=True,
        help="The snap channel to promote to",
        dest="snap_channel",
    )
    promote_args.set_defaults(func=release_revision)

    install_snapcraft_args = subparsers.add_parser(
        "install-snapcraft", help="Install snapcraft classic with retries"
    )
    install_snapcraft_args.set_defaults(func=install_snapcraft)

    download_snap_args = subparsers.add_parser(
        "download-snap", help="Download a snap revision with retries"
    )
    download_snap_args.add_argument("--revision", required=True, help="Snap revision to download")
    download_snap_args.add_argument(
        "--snap-name", default=util.SNAP_NAME, dest="snap_name", help="Name of the snap"
    )
    download_snap_args.set_defaults(func=download_snap)
    return arg_parser


def main():
    args = util.setup_arguments(build_parser())
    args.func(args)


is_main = __name__ == "__main__"
logger_name = Path(sys.argv[0]).stem if is_main else __name__
LOG = logging.getLogger(logger_name)
if is_main:
    main()
else:
    LOG.setLevel(logging.DEBUG)
