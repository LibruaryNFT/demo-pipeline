"""Fit each scene's footage to the narration window it belongs to.

Every scene ends at a fixed offset from t0, so a slow action steals from
the scenes after it. They start late, still finish at their own absolute
deadline, and the footage they capture is therefore shorter than the
narration describing it. From the first slow action onward the picture and
the voice describe different moments, and compose's `-t audio_dur` quietly
trims the accumulated drift off the tail.

The fix is to resample rather than to pad or truncate. Each scene's real
footage is remapped onto exactly the span its narration occupies: an
action that overran is sped up, a scene that got squeezed is stretched.
Nothing is added and nothing is cut, so the result is aligned with the
audio while still showing everything that happened.

This module is deliberately pure. It turns measurements into an ffmpeg
filter string and decides when not to bother; it never runs anything.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# A scene shorter than this yields too few frames to stretch convincingly —
# the result reads as a stutter, which is more distracting than the drift.
MIN_SOURCE_S = 0.30

# Outside this band the remap is doing more than fixing sync. Above the top
# the action becomes unreadable; below the bottom it is a freeze frame with
# extra steps. Either means the timings are wrong in a way remapping cannot
# rescue, so we decline the whole render rather than clamp — clamping would
# break the very alignment this exists to produce.
MAX_SPEED = 8.0
MIN_SPEED = 0.25

# Below this, a scene is on time. Wall-clock measurement has noise in it and
# a 1.002x remap is a re-encode that buys nothing.
SPEED_EPSILON = 0.02


@dataclass(frozen=True)
class SceneTiming:
    """Where a scene landed, against where its narration says it should be.

    All four are seconds from t0, the moment the first scene began. The
    target pair comes from the measured narration durations; the actual pair
    is wall clock.
    """

    id: str
    target_start: float
    target_end: float
    actual_start: float
    actual_end: float

    @property
    def target_duration(self) -> float:
        return self.target_end - self.target_start

    @property
    def actual_duration(self) -> float:
        return self.actual_end - self.actual_start

    @property
    def overrun(self) -> float:
        """Seconds the action ran past its narration. Negative if squeezed."""
        return self.actual_duration - self.target_duration


@dataclass(frozen=True)
class Capture:
    """A screen recording plus what is known about how it lines up.

    `lead_in_s` is the footage recorded before the first scene started —
    page load and settle on the playwright backend, and nothing at all on
    xvfb, which starts ffmpeg only once the page is ready. It is passed
    through at normal speed so enabling the remap does not reframe the
    opening.
    """

    path: Path
    lead_in_s: float = 0.0
    scenes: tuple[SceneTiming, ...] = field(default=())


@dataclass(frozen=True)
class Segment:
    """A span of the recording, and the rate to play it at.

    `speed` > 1 compresses (the source is longer than the slot), < 1 stretches.
    """

    start: float
    end: float
    speed: float

    @property
    def source_duration(self) -> float:
        return self.end - self.start

    @property
    def output_duration(self) -> float:
        return self.source_duration / self.speed


def build_segments(
    scenes: tuple[SceneTiming, ...] | list[SceneTiming],
    lead_in_s: float = 0.0,
) -> list[Segment] | None:
    """Plan the remap, or return None to leave the recording alone.

    None is the answer whenever remapping would be pointless (every scene is
    already on time) or unsound (a scene with too little footage, or one
    needing a rate outside the sane band). Returning None rather than a
    best-effort plan is deliberate: a partial remap desynchronises
    everything after the segment it gave up on, which is worse than the
    drift it set out to fix.
    """
    if not scenes:
        return None

    segments: list[Segment] = []
    if lead_in_s > 0:
        segments.append(Segment(0.0, lead_in_s, 1.0))

    for scene in scenes:
        source = scene.actual_duration
        slot = scene.target_duration
        if slot <= 0:
            logger.warning(
                "timelapse off: scene %s has a zero-length narration window",
                scene.id,
            )
            return None
        if source < MIN_SOURCE_S:
            logger.warning(
                "timelapse off: scene %s captured only %.2fs of footage, "
                "too little to fill %.2fs",
                scene.id, source, slot,
            )
            return None

        speed = source / slot
        if not MIN_SPEED <= speed <= MAX_SPEED:
            logger.warning(
                "timelapse off: scene %s would need %.2fx (%.2fs of footage "
                "into a %.2fs window), outside the %.2f–%.2f band",
                scene.id, speed, source, slot, MIN_SPEED, MAX_SPEED,
            )
            return None

        segments.append(
            Segment(
                lead_in_s + scene.actual_start,
                lead_in_s + scene.actual_end,
                speed,
            )
        )

    if all(abs(s.speed - 1.0) < SPEED_EPSILON for s in segments):
        return None

    return segments


def build_filter(segments: list[Segment], label: str = "tl") -> str:
    """An ffmpeg filter_complex chain producing `[label]` from input 0's video.

    One `trim` per segment rather than one `setpts` expression over the whole
    stream: a piecewise rate change cannot be written as a single monotonic
    PTS expression without an `if` ladder that grows with the scene count and
    is unreadable at four scenes, let alone thirteen.

    `setpts=(PTS-STARTPTS)/speed` rebases each piece to zero before scaling,
    which is what makes them safe to concat back to back.
    """
    if not segments:
        raise ValueError("build_filter needs at least one segment")

    chains = [
        f"[0:v]trim=start={seg.start:.3f}:end={seg.end:.3f},"
        f"setpts=(PTS-STARTPTS)/{seg.speed:.6f}[{label}{i}]"
        for i, seg in enumerate(segments)
    ]
    inputs = "".join(f"[{label}{i}]" for i in range(len(segments)))
    chains.append(f"{inputs}concat=n={len(segments)}:v=1:a=0[{label}]")
    return ";".join(chains)


def describe(segments: list[Segment]) -> str:
    """One line for the log, naming what the remap actually changes."""
    changed = [s for s in segments if abs(s.speed - 1.0) >= SPEED_EPSILON]
    fastest = max((s.speed for s in changed), default=1.0)
    slowest = min((s.speed for s in changed), default=1.0)
    total = sum(s.output_duration for s in segments)
    return (
        f"{len(changed)} of {len(segments)} segments remapped "
        f"({slowest:.2f}x–{fastest:.2f}x), {total:.1f}s out"
    )
