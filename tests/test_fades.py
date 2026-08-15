"""Fade chain construction, especially for short clips.

Regression: the fade-out start was computed as `clip - fade_out` with no
floor. Any demo whose narration was shorter than the fade produced a
negative start time, which ffmpeg rejects outright. Long demos never hit it,
so it survived every end-to-end test until someone rendered one short scene.
"""

import pytest

from demo_pipeline.compose import fade_filter


def start_of(chain: str) -> float:
    for part in chain.split(","):
        if part.startswith("fade=out"):
            return float(part.split("st=")[1].split(":")[0])
    raise AssertionError(f"no fade-out in {chain!r}")


def duration_of(chain: str) -> float:
    for part in chain.split(","):
        if part.startswith("fade=out"):
            return float(part.split("d=")[1])
    raise AssertionError(f"no fade-out in {chain!r}")


class TestLongClips:
    def test_fade_out_lands_at_the_end(self):
        chain = fade_filter(30.0, 30, 2.0)
        assert start_of(chain) == pytest.approx(28.0)
        assert duration_of(chain) == pytest.approx(2.0)

    def test_fade_in_is_always_present(self):
        assert fade_filter(30.0, 30, 2.0).startswith("fade=in:0:30")


class TestShortClips:
    @pytest.mark.parametrize("clip", [0.5, 1.0, 1.9, 2.0, 3.0])
    def test_start_is_never_negative(self, clip):
        # ffmpeg exits non-zero on a negative st=, taking the whole render
        # down at the very last step.
        assert start_of(fade_filter(clip, 30, 2.0)) >= 0.0

    @pytest.mark.parametrize("clip", [0.5, 1.0, 1.9, 3.0])
    def test_fade_never_outlasts_the_clip(self, clip):
        chain = fade_filter(clip, 30, 2.0)
        assert start_of(chain) + duration_of(chain) <= clip + 1e-6

    def test_the_exact_regression_case(self):
        # 0.552s of narration against a 2.0s fade produced "st=-1".
        chain = fade_filter(0.552, 30, 2.0)
        assert "st=-" not in chain
        assert start_of(chain) >= 0.0

    def test_fade_out_is_dropped_for_a_zero_length_clip(self):
        chain = fade_filter(0.0, 30, 2.0)
        assert "fade=out" not in chain
        assert "fade=in" in chain


class TestDisabledFade:
    def test_zero_fade_out_emits_no_fade_out(self):
        assert "fade=out" not in fade_filter(30.0, 30, 0.0)
