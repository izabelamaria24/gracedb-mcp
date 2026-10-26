"""Tests for the chirp/glitch RL environment and its signal generators (no network)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("gymnasium")
pytest.importorskip("scipy")

from gymnasium.utils.env_checker import check_env

from gw_rl import data
from gw_rl.env import ChirpGlitchEnv


def _zero_crossing_rate(x: np.ndarray) -> float:
    return np.mean(np.abs(np.diff(np.sign(x))) > 0)


def test_toy_chirp_frequency_rises():
    signal = data.toy_chirp(np.random.default_rng(1))
    support = np.flatnonzero(np.abs(signal) > 0)
    third = len(support) // 3
    early = signal[support[:third]]
    late = signal[support[-2 * third : -third]]
    assert _zero_crossing_rate(late) > _zero_crossing_rate(early)


def test_imr_chirp_peaks_inside_window():
    pytest.importorskip("lalsimulation")
    rng = np.random.default_rng(6)
    for _ in range(5):
        signal = data.imr_chirp(rng)
        assert np.isfinite(signal).all()
        peak = np.argmax(np.abs(signal)) / data.WINDOW
        assert 0.55 < peak < 1.0  # merger placed in the last part of the window


def test_whiten_like_flat_asd_only_bandpasses():
    seg = data.Segment(
        name="flat/H1", x=np.zeros(1), event_index=0,
        asd_freqs=np.array([0.0, 2048.0]), asd=np.ones(2),
    )
    t = np.arange(data.WINDOW) / data.SAMPLE_RATE
    envelope = np.exp(-(((t - 0.5) / 0.05) ** 2))  # smooth so neither tone leaks across the band edge
    in_band = envelope * np.sin(2 * np.pi * 100 * t)
    out_of_band = envelope * np.sin(2 * np.pi * 1000 * t)
    out = data.whiten_like(in_band + out_of_band, seg, taper_s=0.0)
    assert np.allclose(out, in_band, atol=1e-6)


def test_supervised_baseline_learns_separable_classes():
    pytest.importorskip("torch")
    from gw_rl.baseline import train_classifier

    rng = np.random.default_rng(7)
    y = np.repeat([0, 1, 2], 100)
    x = rng.standard_normal((300, 30, 31)).astype(np.float32)
    x[:, :10] += 2.0 * (y == 1)[:, None, None]
    x[:, 20:] += 2.0 * (y == 2)[:, None, None]
    for arch in ("mlp", "cnn"):
        predict = train_classifier(x, y, arch=arch, epochs=5)
        assert np.mean(predict(x) == y) > 0.95


def test_inject_hits_requested_snr():
    signal = data.sine_gaussian(np.random.default_rng(2))
    out = data.inject(np.zeros_like(signal), signal, snr=12.0)
    assert np.sqrt(np.sum(out**2)) == pytest.approx(12.0)


def test_features_shape_and_finite():
    x = np.random.default_rng(3).standard_normal(data.WINDOW)
    feats = data.features(x)
    assert feats.dtype == np.float32
    assert feats.shape == (30, 31)
    assert np.isfinite(feats).all()


def test_env_passes_gymnasium_checks_and_rewards_correct_label():
    rng = np.random.default_rng(4)
    x = rng.standard_normal((10, 30, 31)).astype(np.float32)
    y = rng.integers(0, 3, size=10)
    env = ChirpGlitchEnv(x, y)
    check_env(env, skip_render_check=True)

    env.reset(seed=0)
    label = int(y[env._i])
    _, reward, terminated, truncated, info = env.step(label)
    assert (reward, terminated, truncated, info["label"]) == (1.0, True, False, label)
    env.reset(seed=0)
    _, reward, *_ = env.step((label + 1) % 3)
    assert reward == -1.0


def test_load_segment_locates_event_at_file_centre(tmp_path):
    ts_mod = pytest.importorskip("gwpy.timeseries")
    noise = 1e-21 * np.random.default_rng(5).standard_normal(32 * data.SAMPLE_RATE)
    path = tmp_path / "GW000000" / "strain" / "H1-1000000000-32.hdf5"
    path.parent.mkdir(parents=True)
    ts_mod.TimeSeries(
        noise, t0=1_000_000_000.25, sample_rate=data.SAMPLE_RATE, name="H1:TEST"
    ).write(str(path))

    seg = data.load_segment(path)
    assert seg.name == "GW000000/H1"
    assert len(seg.x) == 28 * data.SAMPLE_RATE  # 2 s cropped from each end
    assert seg.event_index == 14 * data.SAMPLE_RATE
    assert 0.2 < np.std(seg.x) < 1.0  # strain-scale input is normalised to order one
