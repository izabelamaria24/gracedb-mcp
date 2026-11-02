"""Injection tests for the open-data analysis skill's matched filter (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("lalsimulation")
pytest.importorskip("gwpy")

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "gw-open-data-analysis" / "scripts"))
import matched_filter as mf

FS, DURATION, GPS = 2048, 32, 1_000_000_000.0
M1, M2 = 36.0, 29.0
F_LOW = 20.0


def _injection(snr: float, delay: float, rng: np.random.Generator) -> SimpleNamespace:
    """White Gaussian noise plus an IMRPhenomD signal with optimal SNR ``snr``, merging at GPS + delay."""
    n, dt = FS * DURATION, 1 / FS
    freqs = np.fft.rfftfreq(n, dt)
    df = 1 / DURATION
    h_f = mf.template(M1, M2, F_LOW, df, len(freqs))
    psd = 2 / FS  # one-sided PSD of unit-variance white noise
    band = (freqs >= F_LOW) & (freqs <= 0.9 * FS / 2)
    sigma = np.sqrt(4 * df * np.sum(np.abs(h_f[band]) ** 2 / psd))
    merger = DURATION / 2 + delay
    signal = np.fft.irfft(h_f * snr / sigma * np.exp(-2j * np.pi * freqs * merger), n) / dt
    return SimpleNamespace(
        value=rng.standard_normal(n) + signal,
        sample_rate=SimpleNamespace(value=float(FS)),
        t0=SimpleNamespace(value=GPS - DURATION / 2),
        duration=SimpleNamespace(value=float(DURATION)),
    )


def _run(monkeypatch, capsys, tmp_path, data, *extra):
    monkeypatch.setattr(mf, "load", lambda args: data)
    argv = ["matched_filter.py", "--gps", str(GPS), "--masses", str(M1), str(M2),
            "--fetch", *data, "--out", str(tmp_path), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    assert mf.main() == 0
    return json.loads(capsys.readouterr().out)


def test_recovers_injected_snr_and_arrival_times(monkeypatch, capsys, tmp_path):
    rng = np.random.default_rng(7)
    data = {"H1": _injection(20.0, 0.0, rng), "L1": _injection(15.0, 0.005, rng)}
    result = _run(monkeypatch, capsys, tmp_path, data)

    # Noise moves a recovered SNR by about ±1, so allow two of that.
    h1, l1 = result["detectors"]["H1"], result["detectors"]["L1"]
    assert h1["snr"] == pytest.approx(20.0, abs=2)
    assert l1["snr"] == pytest.approx(15.0, abs=2)
    assert result["combined_snr"] == pytest.approx(np.hypot(h1["snr"], l1["snr"]), abs=0.02)
    assert result["combined_snr"] == pytest.approx(25.0, abs=2.5)
    assert h1["time_offset_s"] == pytest.approx(0.0, abs=0.002)
    assert l1["time_offset_s"] == pytest.approx(0.005, abs=0.002)
    assert h1["background_max_snr"] < 6 and l1["background_max_snr"] < 6
    assert (tmp_path / "snr.png").exists()


def test_noise_alone_stays_near_background(monkeypatch, capsys, tmp_path):
    rng = np.random.default_rng(11)
    data = {"H1": _injection(0.0, 0.0, rng), "L1": _injection(0.0, 0.0, rng)}
    result = _run(monkeypatch, capsys, tmp_path, data)
    for det in result["detectors"].values():
        assert det["snr"] < 6


def test_gate_removes_a_glitch(monkeypatch, capsys, tmp_path):
    rng = np.random.default_rng(3)
    glitched = _injection(10.0, 0.0, rng)
    t = np.arange(FS * DURATION) / FS - DURATION / 2
    glitched.value = glitched.value + 500 * np.exp(-((t + 1.0) / 0.01) ** 2) * np.sin(2 * np.pi * 100 * t)
    data = {"H1": _injection(10.0, 0.0, rng), "L1": glitched}

    loud = _run(monkeypatch, capsys, tmp_path, data)["detectors"]["L1"]
    gated = _run(monkeypatch, capsys, tmp_path, data, "--gate", "L1:-1.0")["detectors"]["L1"]
    assert loud["background_max_snr"] > 10
    assert gated["background_max_snr"] < 6
    assert gated["snr"] == pytest.approx(10.0, abs=1.5)
    assert gated["gated_s"] == [-1.0]
