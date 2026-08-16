"""WebVTT cue timing and text normalisation."""

from onetake import Scene
from onetake.subtitles import build_vtt, format_timestamp


def segs(*pairs) -> list[dict]:
    return [
        {"scene": Scene(id=sid, narration=text, action="wait"), "duration": dur}
        for sid, text, dur in pairs
    ]


class TestFormatTimestamp:
    def test_zero(self):
        assert format_timestamp(0) == "00:00:00.000"

    def test_sub_second(self):
        assert format_timestamp(0.25) == "00:00:00.250"

    def test_minutes_and_seconds(self):
        assert format_timestamp(125.5) == "00:02:05.500"

    def test_hours(self):
        assert format_timestamp(3725.125) == "01:02:05.125"

    def test_milliseconds_are_rounded_not_truncated(self):
        assert format_timestamp(1.9996) == "00:00:02.000"

    def test_negative_is_clamped_because_vtt_has_no_such_thing(self):
        assert format_timestamp(-5) == "00:00:00.000"


class TestBuildVtt:
    def test_starts_with_the_required_header(self):
        assert build_vtt(segs(("a", "Hello.", 2.0))).startswith("WEBVTT\n")

    def test_one_cue_per_scene_identified_by_scene_id(self):
        vtt = build_vtt(segs(("hook", "One.", 2.0), ("tour", "Two.", 3.0)))
        assert "hook" in vtt
        assert "tour" in vtt
        assert vtt.count("-->") == 2

    def test_cues_run_back_to_back(self):
        vtt = build_vtt(segs(("a", "One.", 2.0), ("b", "Two.", 3.0)))
        assert "00:00:00.000 --> 00:00:02.000" in vtt
        assert "00:00:02.000 --> 00:00:05.000" in vtt

    def test_the_offset_accounts_for_the_intro_card(self):
        """The failure this guards against is a whole track four seconds
        early, which reads as the captions being broken rather than offset."""
        vtt = build_vtt(segs(("a", "One.", 2.0)), offset=4.0)
        assert "00:00:04.000 --> 00:00:06.000" in vtt

    def test_the_offset_shifts_every_cue_not_just_the_first(self):
        vtt = build_vtt(segs(("a", "One.", 2.0), ("b", "Two.", 2.0)), offset=4.0)
        assert "00:00:06.000 --> 00:00:08.000" in vtt

    def test_narration_whitespace_is_collapsed(self):
        """Narration is usually a wrapped multi-line Python string; a cue
        containing those newlines renders as a caption box with ragged
        internal line breaks."""
        vtt = build_vtt(segs(("a", "One\n  two\n\tthree.", 2.0)))
        assert "One two three." in vtt

    def test_a_scene_with_no_narration_produces_no_cue(self):
        vtt = build_vtt(segs(("a", "", 2.0), ("b", "Two.", 3.0)))
        assert vtt.count("-->") == 1
        assert "\nb\n" in vtt

    def test_a_silent_scene_still_advances_the_clock(self):
        """It occupies real time in the video even without a cue, so the
        cues after it must not slide earlier."""
        vtt = build_vtt(segs(("silent", "", 2.0), ("b", "Two.", 3.0)))
        assert "00:00:02.000 --> 00:00:05.000" in vtt

    def test_no_scenes_is_a_valid_empty_document(self):
        assert build_vtt([]).strip() == "WEBVTT"

    def test_cue_blocks_are_blank_line_separated(self):
        blocks = build_vtt(segs(("a", "One.", 2.0), ("b", "Two.", 2.0))).split("\n\n")
        assert blocks[0] == "WEBVTT"
        assert blocks[1].splitlines()[0] == "a"
        assert blocks[2].splitlines()[0] == "b"
