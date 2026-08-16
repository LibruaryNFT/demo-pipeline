"""Narration generation: ffprobe parsing and the on-disk cache."""

from unittest.mock import patch

import pytest

from demo_pipeline import Scene
from demo_pipeline.audio import (
    generate_audio_segments,
    get_duration,
    resolve_segment,
)

from .test_config import make_config


class TestGetDuration:
    @patch("demo_pipeline.audio.subprocess.run")
    def test_parses_ffprobe_output(self, mock_run):
        mock_run.return_value.stdout = "12.345\n"
        assert get_duration("ffprobe", "audio.mp3") == 12.345

    @patch("demo_pipeline.audio.subprocess.run")
    def test_invokes_ffprobe_with_given_binary(self, mock_run):
        mock_run.return_value.stdout = "1.0"
        get_duration("/custom/ffprobe", "audio.mp3")
        assert mock_run.call_args[0][0][0] == "/custom/ffprobe"

    @patch("demo_pipeline.audio.subprocess.run")
    def test_requests_only_the_duration_field(self, mock_run):
        mock_run.return_value.stdout = "1.0"
        get_duration("ffprobe", "audio.mp3")
        assert "format=duration" in mock_run.call_args[0][0]


class TestAudioCache:
    @patch("demo_pipeline.audio.get_duration", return_value=4.2)
    def test_reuses_existing_segments_without_calling_the_api(
        self, _mock_dur, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        # A cached segment is one that exists and is over the size floor.
        (audio_dir / "seg_00_hook.mp3").write_bytes(b"x" * 2000)

        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=audio_dir,
            scenes=[Scene(id="hook", narration="Hello.", action="wait")],
        )

        # No OpenAI client is constructed, so an unpatched network call would
        # surface as an error here rather than passing silently.
        with patch("openai.OpenAI") as mock_openai:
            segments = generate_audio_segments(config)
            mock_openai.return_value.audio.speech.with_streaming_response.create.assert_not_called()

        assert len(segments) == 1
        assert segments[0]["duration"] == 4.2
        assert segments[0]["scene"].id == "hook"

    @patch("demo_pipeline.audio.get_duration", return_value=1.0)
    def test_truncated_file_is_not_treated_as_cached(
        self, _mock_dur, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        # Below the 1000-byte floor: a failed previous write, not a real cache.
        (audio_dir / "seg_00_hook.mp3").write_bytes(b"x" * 10)

        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=audio_dir,
            scenes=[Scene(id="hook", narration="Hello.", action="wait")],
        )

        with patch("openai.OpenAI") as mock_openai:
            generate_audio_segments(config)
            create = mock_openai.return_value.audio.speech.with_streaming_response.create
            create.assert_called_once()

    def test_missing_api_key_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = make_config(output_path=tmp_path / "demo.mp4")
        try:
            generate_audio_segments(config)
        except RuntimeError as e:
            assert "OPENAI_API_KEY" in str(e)
        else:
            raise AssertionError("expected RuntimeError for missing API key")


class TestAudioSources:
    """Where narration comes from, and what it costs to get it."""

    def test_a_scene_audio_file_is_used_as_given(self, tmp_path):
        supplied = tmp_path / "voiced-by-a-human.mp3"
        supplied.write_bytes(b"x" * 5000)
        config = make_config(
            output_path=tmp_path / "demo.mp4",
            scenes=[Scene(id="hook", narration="Hello.", action="wait", audio=supplied)],
        )
        path, source = resolve_segment(config, 0, config.scenes[0])
        assert path == supplied
        assert source == "file"

    def test_a_missing_scene_audio_file_names_the_scene(self, tmp_path):
        config = make_config(
            output_path=tmp_path / "demo.mp4",
            scenes=[Scene(id="hook", narration="H", action="wait", audio=tmp_path / "gone.mp3")],
        )
        with pytest.raises(FileNotFoundError, match="hook"):
            resolve_segment(config, 0, config.scenes[0])

    def test_no_api_key_is_needed_when_every_scene_brings_its_own_audio(
        self, tmp_path, monkeypatch
    ):
        """The property that makes running in CI possible."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        supplied = tmp_path / "a.mp3"
        supplied.write_bytes(b"x" * 5000)
        config = make_config(
            output_path=tmp_path / "demo.mp4",
            scenes=[Scene(id="hook", narration="H", action="wait", audio=supplied)],
        )
        path, source = resolve_segment(config, 0, config.scenes[0])
        assert source == "file"

    def test_a_tts_callable_is_used_and_cached(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        calls = []

        def fake_tts(text: str) -> bytes:
            calls.append(text)
            return b"y" * 4000

        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=tmp_path / "audio",
            scenes=[Scene(id="hook", narration="Spoken line.", action="wait")],
            tts=fake_tts,
        )
        path, source = resolve_segment(config, 0, config.scenes[0])
        assert source == "tts"
        assert calls == ["Spoken line."]

        # Second pass hits the cache, so a provider is not re-billed per render.
        _, source_again = resolve_segment(config, 0, config.scenes[0])
        assert source_again == "cache"
        assert calls == ["Spoken line."]

    def test_a_tts_callable_returning_the_wrong_type_says_so(self, tmp_path):
        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=tmp_path / "audio",
            scenes=[Scene(id="hook", narration="H", action="wait")],
            tts=lambda text: "not bytes",
        )
        with pytest.raises(TypeError, match="expected bytes"):
            resolve_segment(config, 0, config.scenes[0])

    def test_a_tts_callable_returning_almost_nothing_is_rejected(self, tmp_path):
        """Silence renders as a scene with no narration and correct timing,
        which is far harder to notice than an error."""
        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=tmp_path / "audio",
            scenes=[Scene(id="hook", narration="H", action="wait")],
            tts=lambda text: b"tiny",
        )
        with pytest.raises(RuntimeError, match="min_audio_bytes"):
            resolve_segment(config, 0, config.scenes[0])

    def test_the_cache_wins_over_the_tts_callable(self, tmp_path):
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        (audio_dir / "seg_00_hook.mp3").write_bytes(b"z" * 5000)

        def explode(text):
            raise AssertionError("should not have been called")

        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=audio_dir,
            scenes=[Scene(id="hook", narration="H", action="wait")],
            tts=explode,
        )
        assert resolve_segment(config, 0, config.scenes[0])[1] == "cache"

    def test_scene_audio_wins_over_the_cache(self, tmp_path):
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        (audio_dir / "seg_00_hook.mp3").write_bytes(b"z" * 5000)
        supplied = tmp_path / "explicit.mp3"
        supplied.write_bytes(b"x" * 5000)
        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=audio_dir,
            scenes=[Scene(id="hook", narration="H", action="wait", audio=supplied)],
        )
        path, source = resolve_segment(config, 0, config.scenes[0])
        assert (path, source) == (supplied, "file")

    def test_the_missing_key_message_names_all_three_ways_out(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = make_config(
            output_path=tmp_path / "demo.mp4",
            audio_dir=tmp_path / "audio",
            scenes=[Scene(id="hook", narration="H", action="wait")],
        )
        with pytest.raises(RuntimeError) as excinfo:
            resolve_segment(config, 0, config.scenes[0])
        message = str(excinfo.value)
        assert "OPENAI_API_KEY" in message
        assert "tts" in message
        assert "audio" in message
