"""Unit tests for usage-profile loading and fingerprinting (SCRUM-38)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.profiles import (
    DEFAULT_PROFILE,
    Profile,
    ProfileError,
    get_profile,
    load_profiles,
    profile_fingerprint,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "profiles.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# --- the shipped profiles file -----------------------------------------------

def test_shipped_profiles_load_and_include_default():
    profiles = load_profiles()
    assert DEFAULT_PROFILE in profiles
    assert {"etudiant", "rapide", "economie"} <= set(profiles)


def test_get_profile_unknown_raises_with_available_list():
    with pytest.raises(ProfileError) as e:
        get_profile("inexistant")
    assert "available" in str(e.value)


def test_default_profile_weights_are_positive():
    prof = get_profile(DEFAULT_PROFILE)
    assert prof.weights and all(w >= 0 for w in prof.weights.values())


# --- validation ---------------------------------------------------------------

def test_load_rejects_unknown_metric(tmp_path):
    path = _write(tmp_path, """
        name: p
        version: 1
        profiles:
          x:
            weights: { not_a_metric: 1.0 }
    """)
    with pytest.raises(ProfileError):
        load_profiles(path)


def test_load_rejects_negative_weight(tmp_path):
    path = _write(tmp_path, """
        name: p
        version: 1
        profiles:
          x:
            weights: { mean_judge_score: -1 }
    """)
    with pytest.raises(ProfileError):
        load_profiles(path)


def test_load_rejects_all_zero_weights(tmp_path):
    path = _write(tmp_path, """
        name: p
        version: 1
        profiles:
          x:
            weights: { mean_judge_score: 0 }
    """)
    with pytest.raises(ProfileError):
        load_profiles(path)


def test_load_rejects_missing_profiles_key(tmp_path):
    path = _write(tmp_path, "name: p\nversion: 1\n")
    with pytest.raises(ProfileError):
        load_profiles(path)


# --- fingerprint --------------------------------------------------------------

def test_fingerprint_is_stable_regardless_of_weight_order():
    a = Profile("p", "", {"mean_judge_score": 0.5, "avg_latency_ms": 0.5})
    b = Profile("p", "", {"avg_latency_ms": 0.5, "mean_judge_score": 0.5})
    assert profile_fingerprint(a) == profile_fingerprint(b)


def test_fingerprint_changes_with_weight():
    a = Profile("p", "", {"mean_judge_score": 0.5})
    b = Profile("p", "", {"mean_judge_score": 0.6})
    assert profile_fingerprint(a) != profile_fingerprint(b)
