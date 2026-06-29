"""
Usage profiles for the final model recommendation (SCRUM-38).

A profile is a named set of NUMERIC weights over the decision metrics — it
encodes a way of using the platform (student = quality + low cost, occasional
user = speed, …). Profiles are versioned in `app/decision_profiles.yaml` so a
recommendation is reproducible: the same weights always rank models the same way.

NB: this file lives OUTSIDE `app/prompts/templates/` on purpose — that folder is
scanned by the prompt sync (app/prompts/sync.py), which requires a `content`
field. Profiles are config, not a prompt, so they get this dedicated,
schema-aware loader. `profile_fingerprint()` hashes the weights into the
decision cache key, so editing a weight invalidates the cached decision.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.decision_scoring import METRIC_DIRECTION
from app.prompts.hasher import compute_hash

PROFILES_PATH = Path(__file__).parent / "decision_profiles.yaml"
DEFAULT_PROFILE = "equilibre"


class ProfileError(ValueError):
    """Raised when the profiles file is missing, malformed, or names an unknown metric."""


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    weights: dict[str, float]


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, Profile]:
    """Load and validate every profile. Weights must be >= 0 over known metrics."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict) or not data["profiles"]:
        raise ProfileError(f"{path}: expected a non-empty 'profiles' mapping")

    profiles: dict[str, Profile] = {}
    for name, body in data["profiles"].items():
        if not isinstance(body, dict):
            raise ProfileError(f"profile {name!r}: must be a mapping")
        weights = body.get("weights")
        if not isinstance(weights, dict) or not weights:
            raise ProfileError(f"profile {name!r}: 'weights' must be a non-empty mapping")

        clean: dict[str, float] = {}
        for metric, w in weights.items():
            if metric not in METRIC_DIRECTION:
                raise ProfileError(
                    f"profile {name!r}: unknown metric {metric!r} "
                    f"(allowed: {sorted(METRIC_DIRECTION)})"
                )
            if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0:
                raise ProfileError(f"profile {name!r}: weight for {metric!r} must be a number >= 0")
            clean[metric] = float(w)

        if sum(clean.values()) <= 0:
            raise ProfileError(f"profile {name!r}: weights must not all be zero")

        profiles[str(name)] = Profile(
            name=str(name),
            description=str(body.get("description", "")),
            weights=clean,
        )
    return profiles


def get_profile(name: str, path: Path = PROFILES_PATH) -> Profile:
    """Return one profile by name, or raise with the list of available ones."""
    profiles = load_profiles(path)
    if name not in profiles:
        raise ProfileError(f"unknown profile {name!r}; available: {sorted(profiles)}")
    return profiles[name]


def profile_fingerprint(profile: Profile) -> str:
    """Stable hash of (name + weights) — the profile side of the decision cache key."""
    canon = json.dumps(
        {"name": profile.name, "weights": dict(sorted(profile.weights.items()))},
        sort_keys=True,
        ensure_ascii=False,
    )
    return compute_hash(canon)
