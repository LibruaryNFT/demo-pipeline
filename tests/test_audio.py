"""Narration generation: ffprobe parsing and the on-disk cache."""

from unittest.mock import patch

from demo_pipeline import Scene
from demo_pipeline.audio import generate_audio_segments, get_duration

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
