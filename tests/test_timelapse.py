"""Rate-remap planning: when to remap, when to decline, and what graph."""

import pytest

from onetake import Capture, SceneTiming
from onetake.timelapse import (
    MAX_SPEED,
    Segment,
    build_filter,
    build_segments,
    describe,
)


def on_time(scene_id: str, start: float, duration: float) -> SceneTiming:
    return SceneTiming(scene_id, start, start + duration, start, start + duration)


def overran(scene_id: str, window: float, real: float, at: float = 0.0) -> SceneTiming:
    """A scene whose narration is `window` long but whose action took `real`."""
    return SceneTiming(scene_id, at, at + window, at, at + real)


class TestSceneTiming:
    def test_durations_come_from_the_pairs(self):
        t = SceneTiming("a", 0.0, 5.0, 0.0, 7.0)
        assert t.target_duration == 5.0
        assert t.actual_duration == 7.0

    def test_overrun_is_positive_when_the_action_ran_long(self):
        assert SceneTiming("a", 0.0, 5.0, 0.0, 7.0).overrun == pytest.approx(2.0)

    def test_overrun_is_negative_for_a_scene_squeezed_by_the_one_before(self):
        assert SceneTiming("a", 5.0, 10.0, 7.0, 10.0).overrun == pytest.approx(-2.0)


class TestWhenToDoNothing:
    def test_no_scenes_means_no_plan(self):
        assert build_segments([]) is None

    def test_an_entirely_on_time_render_is_left_alone(self):
        """A demo where nothing overran should not pay for a re-encode."""
        scenes = [on_time("a", 0.0, 5.0), on_time("b", 5.0, 4.0)]
        assert build_segments(scenes) is None

    def test_measurement_noise_does_not_trigger_a_remap(self):
        """Wall-clock has jitter in it; a 1.004x remap buys nothing."""
        scenes = [SceneTiming("a", 0.0, 5.0, 0.0, 5.02)]
        assert build_segments(scenes) is None


class TestWhenToDecline:
    """All-or-nothing on purpose: a partial remap desynchronises everything
    after the segment it gave up on, which is worse than the original drift."""

    def test_a_scene_with_almost_no_footage_cancels_the_whole_plan(self):
        scenes = [overran("a", window=5.0, real=0.1), on_time("b", 5.0, 5.0)]
        assert build_segments(scenes) is None

    def test_a_speed_past_the_ceiling_cancels_the_whole_plan(self):
        scenes = [overran("a", window=1.0, real=MAX_SPEED + 1.0)]
        assert build_segments(scenes) is None

    def test_a_slowdown_past_the_floor_cancels_the_whole_plan(self):
        # 1s of footage stretched over 8s reads as a freeze frame.
        scenes = [overran("a", window=8.0, real=1.0)]
        assert build_segments(scenes) is None

    def test_a_zero_length_narration_window_cancels_the_whole_plan(self):
        scenes = [SceneTiming("a", 0.0, 0.0, 0.0, 3.0)]
        assert build_segments(scenes) is None

    def test_one_bad_scene_cancels_its_good_neighbours_too(self):
        scenes = [
            overran("a", window=5.0, real=7.0, at=0.0),
            SceneTiming("b", 5.0, 10.0, 7.0, 7.05),  # 0.05s of footage
        ]
        assert build_segments(scenes) is None

    def test_the_reason_is_logged_rather_than_swallowed(self, caplog):
        build_segments([overran("a", window=5.0, real=0.1)])
        assert "timelapse off" in caplog.text
        assert "scene a" in caplog.text


class TestPlan:
    def test_an_overrun_is_compressed_into_its_window(self):
        (seg,) = build_segments([overran("a", window=5.0, real=7.0)])
        assert seg.speed == pytest.approx(1.4)
        assert seg.output_duration == pytest.approx(5.0)

    def test_a_squeezed_scene_is_stretched_to_fill_its_window(self):
        (seg,) = build_segments([SceneTiming("a", 0.0, 5.0, 0.0, 3.0)])
        assert seg.speed == pytest.approx(0.6)
        assert seg.output_duration == pytest.approx(5.0)

    def test_every_scene_ends_up_exactly_its_narration_long(self):
        """The whole point. Whatever happened in wall clock, the output is
        the narration's shape."""
        scenes = [
            SceneTiming("a", 0.0, 5.0, 0.0, 5.0),
            SceneTiming("b", 5.0, 10.0, 5.0, 12.0),
            SceneTiming("c", 10.0, 15.0, 12.0, 15.0),
        ]
        plan = build_segments(scenes)
        assert [s.output_duration for s in plan] == pytest.approx([5.0, 5.0, 5.0])

    def test_the_total_matches_the_narration_total(self):
        scenes = [
            SceneTiming("a", 0.0, 4.0, 0.0, 6.0),
            SceneTiming("b", 4.0, 9.0, 6.0, 9.0),
        ]
        plan = build_segments(scenes)
        assert sum(s.output_duration for s in plan) == pytest.approx(9.0)

    def test_segments_are_contiguous_so_no_footage_is_orphaned(self):
        scenes = [
            SceneTiming("a", 0.0, 5.0, 0.0, 7.0),
            SceneTiming("b", 5.0, 10.0, 7.0, 10.0),
        ]
        plan = build_segments(scenes)
        for earlier, later in zip(plan, plan[1:], strict=False):
            assert earlier.end == pytest.approx(later.start)


class TestLeadIn:
    """Footage recorded before the first scene — page load and settle on the
    playwright backend. It passes through untouched so turning the remap on
    does not reframe the opening."""

    def test_the_lead_in_becomes_a_full_speed_first_segment(self):
        plan = build_segments([overran("a", window=5.0, real=7.0)], lead_in_s=3.0)
        assert plan[0] == Segment(0.0, 3.0, 1.0)

    def test_scene_spans_are_offset_past_it(self):
        plan = build_segments([overran("a", window=5.0, real=7.0)], lead_in_s=3.0)
        assert plan[1].start == pytest.approx(3.0)
        assert plan[1].end == pytest.approx(10.0)

    def test_no_lead_in_means_no_extra_segment(self):
        plan = build_segments([overran("a", window=5.0, real=7.0)], lead_in_s=0.0)
        assert len(plan) == 1

    def test_a_lead_in_alone_does_not_justify_a_remap(self):
        """Otherwise every xvfb render with an on-time timeline would
        re-encode for a segment that changes nothing."""
        assert build_segments([on_time("a", 0.0, 5.0)], lead_in_s=3.0) is None


class TestBuildFilter:
    def test_one_trim_chain_per_segment_plus_a_concat(self):
        graph = build_filter([Segment(0, 5, 1.0), Segment(5, 12, 1.4)])
        assert graph.count("trim=") == 2
        assert "concat=n=2:v=1:a=0[tl]" in graph

    def test_each_piece_is_rebased_before_being_scaled(self):
        """Without PTS-STARTPTS the pieces keep their source timestamps and
        the concat produces a video with a huge gap in it."""
        graph = build_filter([Segment(10, 17, 1.4)])
        assert "setpts=(PTS-STARTPTS)/1.400000" in graph

    def test_boundaries_are_absolute_source_times(self):
        graph = build_filter([Segment(10.5, 17.25, 1.0)])
        assert "trim=start=10.500:end=17.250" in graph

    def test_labels_are_unique_and_all_feed_the_concat(self):
        graph = build_filter([Segment(0, 1, 1.0), Segment(1, 2, 1.0), Segment(2, 3, 1.0)])
        assert "[tl0][tl1][tl2]concat=n=3" in graph

    def test_the_output_label_is_configurable_for_a_caller_that_needs_it(self):
        assert "[x]" in build_filter([Segment(0, 1, 1.0)], label="x")

    def test_an_empty_plan_is_a_programming_error_not_a_silent_no_op(self):
        with pytest.raises(ValueError):
            build_filter([])


class TestDescribe:
    def test_it_counts_only_the_segments_that_actually_change(self):
        plan = [Segment(0, 3, 1.0), Segment(3, 10, 1.4)]
        assert describe(plan).startswith("1 of 2 segments remapped")

    def test_it_names_the_range_and_the_resulting_length(self):
        plan = [Segment(0, 7, 1.4), Segment(7, 10, 0.6)]
        line = describe(plan)
        assert "0.60x–1.40x" in line
        assert "10.0s out" in line


class TestCapture:
    def test_a_bare_capture_has_nothing_to_remap(self):
        from pathlib import Path

        cap = Capture(path=Path("/tmp/x.mp4"))
        assert cap.lead_in_s == 0.0
        assert build_segments(cap.scenes, cap.lead_in_s) is None
