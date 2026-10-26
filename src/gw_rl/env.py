"""Gymnasium environment: label a strain spectrogram as noise, chirp or glitch."""

from __future__ import annotations

from typing import Any, ClassVar

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .data import LABELS


class ChirpGlitchEnv(gym.Env):
    """One decision per episode: +1 reward for the correct label, -1 otherwise.

    With a single step per episode this is a contextual bandit; PPO and DQN both
    handle it, and it can later grow into a multi-step search over a data stream.
    """

    metadata: ClassVar[dict[str, Any]] = {"render_modes": []}

    def __init__(self, observations: np.ndarray, labels: np.ndarray) -> None:
        super().__init__()
        self._x = np.asarray(observations, dtype=np.float32)
        self._y = np.asarray(labels, dtype=np.int64)
        self.observation_space = spaces.Box(
            float(self._x.min()), float(self._x.max()), shape=self._x.shape[1:], dtype=np.float32
        )
        self.action_space = spaces.Discrete(len(LABELS))
        self._i = 0

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        self._i = int(self.np_random.integers(len(self._x)))
        return self._x[self._i], {}

    def step(self, action):
        label = int(self._y[self._i])
        reward = 1.0 if int(action) == label else -1.0
        return self._x[self._i], reward, True, False, {"label": label}
