"""Supervised classifiers trained on the same spectrograms, to measure what RL adds."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import torch
from torch import nn

from .data import LABELS


def _mlp(shape: tuple[int, int]) -> nn.Module:
    # Same hidden layers as stable-baselines3's default MlpPolicy, so only the training differs.
    return nn.Sequential(
        nn.Flatten(),
        nn.Linear(shape[0] * shape[1], 64),
        nn.Tanh(),
        nn.Linear(64, 64),
        nn.Tanh(),
        nn.Linear(64, len(LABELS)),
    )


def _cnn(shape: tuple[int, int]) -> nn.Module:
    return nn.Sequential(
        nn.Conv2d(1, 16, 3, padding=1),
        nn.ReLU(),
        nn.MaxPool2d(2),
        nn.Conv2d(16, 32, 3, padding=1),
        nn.ReLU(),
        nn.MaxPool2d(2),
        nn.Flatten(),
        nn.Linear(32 * (shape[0] // 4) * (shape[1] // 4), 64),
        nn.ReLU(),
        nn.Linear(64, len(LABELS)),
    )


def train_classifier(
    x: np.ndarray,
    y: np.ndarray,
    arch: str = "cnn",
    epochs: int = 20,
    batch_size: int = 128,
    seed: int = 0,
) -> Callable[[np.ndarray], np.ndarray]:
    """Train with cross-entropy and return a function mapping spectrograms to labels."""
    torch.manual_seed(seed)
    model = {"mlp": _mlp, "cnn": _cnn}[arch](x.shape[1:])
    xt = torch.as_tensor(x, dtype=torch.float32).unsqueeze(1)
    yt = torch.as_tensor(y, dtype=torch.long)
    optimiser = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    order = torch.Generator().manual_seed(seed)

    model.train()
    for _ in range(epochs):
        for idx in torch.randperm(len(xt), generator=order).split(batch_size):
            optimiser.zero_grad()
            loss_fn(model(xt[idx]), yt[idx]).backward()
            optimiser.step()
    model.eval()

    def predict(x_new: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            logits = model(torch.as_tensor(x_new, dtype=torch.float32).unsqueeze(1))
        return logits.argmax(1).numpy()

    return predict
