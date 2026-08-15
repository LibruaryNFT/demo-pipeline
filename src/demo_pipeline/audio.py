"""TTS narration generation with per-scene caching.

Each scene's MP3 is named seg_NN_<scene_id>.mp3. The cache key is the
filename — if you change a scene's narration, delete the corresponding MP3
to force regeneration.
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


def generate_audio_segments(config: ProjectConfig) -> list[dict]:
    """Generate TTS audio per scene and return [{scene, audio_path, duration}].

    Reads OPENAI_API_KEY from the environment. Cached MP3s on disk are
    reused — delete them to force regeneration.
    """
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set — needed for TTS narration")

    client = OpenAI(api_key=api_key)
    config.audio_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    for i, scene in enumerate(config.scenes):
        audio_path = config.audio_dir / f"seg_{i:02d}_{scene.id}.mp3"

        if audio_path.exists() and audio_path.stat().st_size > 1000:
            duration = get_duration(config.ffprobe, audio_path)
            logger.info(
                "[%d/%d] cached %s (%.1fs)",
                i + 1, len(config.scenes), scene.id, duration,
            )
        else:
            logger.info(
                "[%d/%d] generating TTS for %s...",
                i + 1, len(config.scenes), scene.id,
            )
            with client.audio.speech.with_streaming_response.create(
                model=config.tts_model,
                voice=config.tts_voice,
                input=scene.narration,
                speed=config.tts_speed,
            ) as response:
                response.stream_to_file(str(audio_path))
            duration = get_duration(config.ffprobe, audio_path)
            logger.info("            %.1fs", duration)

        results.append({
            "scene": scene,
            "audio_path": str(audio_path),
            "duration": duration,
        })

    total = sum(r["duration"] for r in results)
    logger.info("total narration: %.1fs across %d scenes", total, len(results))
    return results
