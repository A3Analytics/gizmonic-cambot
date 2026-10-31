"""Daily frame and token caps. Hitting a cap pauses indexing, not recording."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    frames_per_day: int = 2000
    tokens_per_day: int = 500_000
    frames_used: int = 0
    tokens_used: int = 0

    def allow(self, frames: int) -> bool:
        return self.frames_used + frames <= self.frames_per_day and self.tokens_used < self.tokens_per_day

    def note(self, frames: int, tokens: int) -> None:
        self.frames_used += frames
        self.tokens_used += tokens

    @property
    def paused(self) -> bool:
        return self.frames_used >= self.frames_per_day or self.tokens_used >= self.tokens_per_day
