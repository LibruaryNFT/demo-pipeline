"""Narration audio per scene, with caching and three ways to get it.

Each scene's MP3 is named `seg_NN_<scene_id>.mp3` in the project's audio
directory. The filename is the whole cache key, so changing a scene's
narration means deleting its MP3 or the old audio is reused.

Where the audio comes from, in order:

1. **`Scene.audio`** — a file you already have. Nothing is generated and
   nothing is cached; the file is used exactly as given.
2. **The cache** — a previous run's MP3, still on disk.
3. **`ProjectConfig.tts`** — any callable taking narration text and
   returning audio bytes. This is the seam for a local engine, a different
   provider, or a recorded human voice.
4. **OpenAI** — the built-in default.

The API key is only read at step 4, and only for the first scene that
actually reaches it. A project whose scenes all supply their own audio, or
which sets `tts`, renders with no key and no billing — which is what makes
running in CI viable.
"""

import logging
import os
import subprocess
from pathlib import Path

from .config import ProjectConfig

logger = logging.getLogger(__name__)


def get_duration(ffprobe: str, path: Path) -> float:
    """Read an audio or video file's duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def _openai_to_file(config: ProjectConfig, text: str, path: Path) -> None:
    """Default narration provider. Imported and keyed only when reached.

    Streams straight to disk rather than returning bytes, which is the
    documented OpenAI path and keeps a long narration out of memory. The
    `tts` seam is bytes-in-bytes-out because that is far easier to implement
    against; only this built-in gets the streaming treatment.
    """
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Either set it, pass a `tts` callable on "
            "ProjectConfig, or give each Scene an `audio` file."
        )
    client = OpenAI(api_key=api_key)
    with client.audio.speech.with_streaming_response.create(
        model=config.tts_model,
        voice=config.tts_voice,
        input=text,
        speed=config.tts_speed,
    ) as response:
        response.stream_to_file(str(path))


def _is_usable(path: Path, min_bytes: int) -> bool:
    """A truncated write is worse than no file — it renders as silence."""
    return path.exists() and path.stat().st_size > min_bytes


def resolve_segment(config: ProjectConfig, index: int, scene) -> tuple[Path, str]:
    """Return the audio path for one scene and where it came from.

    Generating when needed, reusing when possible, and never touching the
    network when the caller has already supplied the sound.
    """
    if scene.audio:
        path = Path(scene.audio)
        if not path.exists():
            raise FileNotFoundError(
                f"scene {scene.id!r}: audio file not found: {path}"
            )
        return path, "file"

    path = config.audio_dir / f"seg_{index:02d}_{scene.id}.mp3"
    if _is_usable(path, config.min_audio_bytes):
        return path, "cache"

    if config.tts is not None:
        data = config.tts(scene.narration)
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(
                f"config.tts returned {type(data).__name__}, expected bytes"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if not _is_usable(path, config.min_audio_bytes):
            raise RuntimeError(
                f"scene {scene.id!r}: config.tts returned {len(data)} bytes, "
                f"below min_audio_bytes={config.min_audio_bytes}"
            )
        return path, "tts"

    path.parent.mkdir(parents=True, exist_ok=True)
    _openai_to_file(config, scene.narration, path)
    return path, "openai"


def generate_audio_segments(config: ProjectConfig) -> list[dict]:
    """Resolve narration for every scene. Returns [{scene, audio_path, duration}]."""
    config.audio_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    total_scenes = len(config.scenes)

    for i, scene in enumerate(config.scenes):
        path, source = resolve_segment(config, i, scene)
        duration = get_duration(config.ffprobe, path)
        logger.info(
            "[%d/%d] %s %s (%.1fs)", i + 1, total_scenes, source, scene.id, duration
        )
        results.append({
            "scene": scene,
            "audio_path": str(path),
            "duration": duration,
        })

    total = sum(r["duration"] for r in results)
    logger.info("total narration: %.1fs across %d scenes", total, len(results))
    return results
