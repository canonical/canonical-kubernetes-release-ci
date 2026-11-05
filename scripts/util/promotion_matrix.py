"""Where and against what each track is tested, as defined in config/promotion-matrix.yaml."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Optional

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "promotion-matrix.yaml"

_TRACK_RE = re.compile(r"^(\d+)\.(\d+)")


@dataclass(frozen=True)
class Profile:
    """Test setup for the tracks older than `older_than` (all tracks if unset)."""

    runs_on: dict[str, str]
    lxd_series: dict[str, list[str]]
    older_than: Optional[tuple[int, int]] = None

    def runner_for(self, arch: str) -> str:
        """Get the label of the runner for an architecture."""
        try:
            return self.runs_on[arch]
        except KeyError:
            raise ValueError(f"No runner configured for architecture '{arch}'") from None


def _parse_track(track: str) -> tuple[int, int]:
    match = _TRACK_RE.match(track)
    if not match:
        raise ValueError(f"Invalid track name: {track}")
    return int(match[1]), int(match[2])


@cache
def load_profiles(path: Path = CONFIG_PATH) -> list[Profile]:
    """Load the overrides in file order, followed by the default profile."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if "default" not in data:
        raise ValueError(f"{path}: a 'default' profile is required")

    profiles = [
        Profile(
            runs_on=entry["runs-on"],
            lxd_series=entry["lxd-series"],
            older_than=_parse_track(entry["older-than"]) if "older-than" in entry else None,
        )
        for entry in [*data.get("overrides", []), data["default"]]
    ]
    if any(p.older_than is None for p in profiles[:-1]) or profiles[-1].older_than:
        raise ValueError(f"{path}: overrides require 'older-than', the default must not have it")
    return profiles


def profile_for_track(track: str, profiles: Optional[list[Profile]] = None) -> Profile:
    """Get the first override whose `older-than` is above the track, else the default."""
    version = _parse_track(track)
    if profiles is None:
        profiles = load_profiles()
    return next(p for p in profiles if p.older_than is None or version < p.older_than)
