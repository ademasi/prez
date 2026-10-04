"""Talk timer: elapsed/remaining bookkeeping with an injectable clock. Pure Python."""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum


class TimerState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


class TalkTimer:
    """Stopwatch for the talk, optionally compared against an expected duration.

    ``clock`` must return seconds as a float and never go backwards (``time.monotonic``
    by default); tests inject a fake clock to make timing deterministic.
    """

    def __init__(
        self,
        talk_seconds: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self.state = TimerState.IDLE
        self.talk_seconds: int | None = None
        self._accumulated = 0.0  # seconds counted before the current run
        self._started_at: float | None = None  # clock reading when the current run began
        self.set_talk_time(talk_seconds)

    # -- read-only views -------------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        """Seconds the timer has been running, excluding pauses."""
        total = self._accumulated
        if self.state is TimerState.RUNNING and self._started_at is not None:
            total += self._clock() - self._started_at
        return max(0.0, total)

    @property
    def remaining(self) -> float | None:
        """``talk_seconds - elapsed`` (negative when over time), or None without a talk time."""
        if self.talk_seconds is None:
            return None
        return self.talk_seconds - self.elapsed

    @property
    def fraction(self) -> float | None:
        """``elapsed / talk_seconds`` (unclamped, may exceed 1), or None without a talk time."""
        if not self.talk_seconds:
            return None
        return self.elapsed / self.talk_seconds

    @property
    def overtime(self) -> bool:
        remaining = self.remaining
        return remaining is not None and remaining < 0

    # -- control ---------------------------------------------------------------------------

    def start(self) -> None:
        """Start counting from IDLE or PAUSED; a no-op while RUNNING."""
        if self.state is TimerState.RUNNING:
            return
        self._started_at = self._clock()
        self.state = TimerState.RUNNING

    def pause(self) -> None:
        """Stop counting but keep the elapsed time; a no-op unless RUNNING."""
        if self.state is not TimerState.RUNNING:
            return
        if self._started_at is not None:
            self._accumulated += max(0.0, self._clock() - self._started_at)
        self._started_at = None
        self.state = TimerState.PAUSED

    def toggle(self) -> None:
        """RUNNING → pause; IDLE or PAUSED → start."""
        if self.state is TimerState.RUNNING:
            self.pause()
        else:
            self.start()

    def reset(self) -> None:
        """Back to IDLE with zero elapsed time. The talk time is kept."""
        self._accumulated = 0.0
        self._started_at = None
        self.state = TimerState.IDLE

    def set_talk_time(self, seconds: int | None) -> None:
        """Set the expected duration in seconds; None or 0 disables the comparison."""
        if seconds is None:
            self.talk_seconds = None
            return
        value = int(seconds)
        if value < 0:
            raise ValueError(f"talk time must not be negative, got {seconds!r}")
        self.talk_seconds = value or None

    # -- text conversion -------------------------------------------------------------------

    @staticmethod
    def format(seconds: float) -> str:
        """``"mm:ss"`` below one hour, ``"h:mm:ss"`` from one hour; negative → ``"-mm:ss"``."""
        sign = "-" if seconds < 0 else ""
        total = int(abs(seconds))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{sign}{hours}:{minutes:02d}:{secs:02d}"
        return f"{sign}{minutes:02d}:{secs:02d}"

    @staticmethod
    def parse(text: str) -> int:
        """Seconds from ``"MM"`` (minutes), ``"MM:SS"`` or ``"H:MM:SS"``; ValueError otherwise.

        An empty component counts as zero (``"20:"`` is twenty minutes); anything that is
        not a non-negative decimal integer is rejected.
        """
        parts = [part.strip() for part in text.strip().split(":")]
        if not 1 <= len(parts) <= 3 or all(part == "" for part in parts):
            raise ValueError(f"invalid talk time {text!r}; expected MM, MM:SS or H:MM:SS")
        values: list[int] = []
        for part in parts:
            if part == "":
                values.append(0)
            elif part.isdecimal():
                values.append(int(part))
            else:
                raise ValueError(f"invalid talk time {text!r}; expected MM, MM:SS or H:MM:SS")
        if len(values) == 1:
            return values[0] * 60
        if len(values) == 2:
            return values[0] * 60 + values[1]
        return values[0] * 3600 + values[1] * 60 + values[2]
