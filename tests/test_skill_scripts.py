"""Injection tests for the open-data analysis skill's matched filter and targeted search (no network)."""

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
import targeted_search as ts

FS, DURATION, GPS = 2048, 32, 1_000_000_000.0
M1, M2 = 36.0, 29.0
F_LOW = 20.0


def _injection(
    snr: float, delay: float, rng: np.random.Generator,
    masses: tuple[float, float] = (M1, M2), f_low: float = F_LOW, duration: float = DURATION,
) -> SimpleNamespace:
    """White Gaussian noise plus an IMRPhenomD signal with optimal SNR ``snr``, merging at GPS + delay."""
    n, dt = int(FS * duration), 1 / FS
    freqs = np.fft.rfftfreq(n, dt)
    df = 1 / duration
    h_f = mf.template(*masses, f_low, df, len(freqs))
    psd = 2 / FS  # one-sided PSD of unit-variance white noise
    band = (freqs >= f_low) & (freqs <= 0.9 * FS / 2)
    sigma = np.sqrt(4 * df * np.sum(np.abs(h_f[band]) ** 2 / psd))
    merger = duration / 2 + delay
    signal = np.fft.irfft(h_f * snr / sigma * np.exp(-2j * np.pi * freqs * merger), n) / dt
    return SimpleNamespace(
        value=rng.standard_normal(n) + signal,
        sample_rate=SimpleNamespace(value=float(FS)),
        t0=SimpleNamespace(value=GPS - duration / 2),
        duration=SimpleNamespace(value=float(duration)),
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


# A small neutron-star bank and a high start frequency keep the targeted search fast.
BNS, BNS_F_LOW, SEARCH_SECONDS = (1.35, 1.4), 60.0, 128.0


def _search(monkeypatch, capsys, tmp_path, data):
    monkeypatch.setattr(ts, "load", lambda args: data)
    argv = ["targeted_search.py", "--gps", str(GPS), "--fetch", *data, "--m1", "1.3", "1.4",
            "--fmin", str(BNS_F_LOW), "--out", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)
    assert ts.main() == 0
    return json.loads(capsys.readouterr().out)


def test_targeted_search_finds_merger_before_trigger(monkeypatch, capsys, tmp_path):
    rng = np.random.default_rng(5)
    data = {
        ifo: _injection(snr, -2.0 + delay, rng, BNS, BNS_F_LOW, SEARCH_SECONDS)
        for ifo, snr, delay in (("H1", 12.0, 0.0), ("L1", 10.0, 0.003))
    }
    result = _search(monkeypatch, capsys, tmp_path, data)

    on = result["on_source"]
    assert on["network_snr"] == pytest.approx(np.hypot(12, 10), abs=2.5)
    assert on["time_offset_s"] == pytest.approx(-2.0, abs=0.005)
    assert result["off_source"]["louder_than_on_source"] == 0
    assert result["off_source"]["max"] < 9
    assert result["false_alarm_probability"] == pytest.approx(1 / (result["off_source"]["windows"] + 1), abs=1e-4)
    assert "note" in result


def test_targeted_search_noise_is_not_significant(monkeypatch, capsys, tmp_path):
    rng = np.random.default_rng(9)
    data = {ifo: _injection(0.0, 0.0, rng, BNS, BNS_F_LOW, SEARCH_SECONDS) for ifo in ("H1", "L1")}
    result = _search(monkeypatch, capsys, tmp_path, data)
    assert result["on_source"]["network_snr"] < 9
    assert result["false_alarm_probability"] > 0.1


def test_bank_spacing_follows_signal_cycles():
    # Lower start frequency means more cycles, so a denser bank.
    assert len(ts.bank(1.0, 2.5, 1.4, 30.0)) > len(ts.bank(1.0, 2.5, 1.4, 50.0)) > 100
    masses = ts.bank(1.0, 2.5, 1.4, 40.0)
    assert masses[0] == 1.0 and masses[-1] <= 2.5
    assert np.all(np.diff(masses) > 0)
