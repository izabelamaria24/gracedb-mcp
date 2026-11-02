"""Matched-filter public GWOSC strain with a waveform model for the catalogue masses.

This is how LIGO finds signals: slide a predicted waveform (the template) along the data
and measure how well it fits at every moment, as a signal-to-noise ratio (SNR). The
Fourier-transform steps are plain numpy calls so they can be read below.

    python matched_filter.py --gps 1126259462.4 --masses 34.6 30.0 --redshift 0.1 \
        data/GW150914/strain/*.hdf5
    python matched_filter.py --gps 1126259462.4 --masses 34.6 30.0 --redshift 0.1 --fetch H1 L1

Needs gwpy and lalsuite (for the waveform model):
``uv run --with gwpy --with lalsuite python matched_filter.py ...``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from qscan import load
from scipy.signal import welch
from scipy.signal.windows import tukey

SUN_SECONDS = 4.925491e-6  # G * M_sun / c^3
PSD_SECONDS = 4.0
TAPER = 0.1  # fraction of the data tapered at the two ends
EVENT_WINDOW = 0.2  # s around --gps to look for the event (catalogue times are rounded)
COINCIDENCE = 0.03  # s: light travel between the detectors (up to 27 ms) plus slack
BACKGROUND_GAP = 0.5  # s around the event left out of the noise background
BANK_SIZE = 21
GATE_HALF = 0.25  # s of data zeroed each side of a gated glitch, plus a 0.25 s taper


def chirp_mass(m1: float, m2: float) -> float:
    return (m1 * m2) ** 0.6 / (m1 + m2) ** 0.2


def start_frequency(mc: float, seconds: float, floor: float) -> float:
    """Frequency the signal has ``seconds`` before merger (leading order), at least ``floor``."""
    return max(floor, (256 * seconds / 5) ** -0.375 * (mc * SUN_SECONDS) ** -0.625 / np.pi)


def cycles(mc: float, f_low: float) -> float:
    """Number of wave cycles from ``f_low`` to merger (leading order)."""
    return (mc * SUN_SECONDS * f_low) ** (-5 / 3) / (32 * np.pi ** (8 / 3))


def template(m1: float, m2: float, f_low: float, df: float, n_freq: int) -> np.ndarray:
    """IMRPhenomD waveform (non-spinning) in the frequency domain, merger at t = 0."""
    import lal
    import lalsimulation as ls

    hp, _ = ls.SimInspiralChooseFDWaveform(
        m1 * lal.MSUN_SI, m2 * lal.MSUN_SI, 0, 0, 0, 0, 0, 0,
        1e6 * lal.PC_SI, 0, 0, 0, 0, 0,  # distance only scales it; SNR is normalised
        df, f_low, (n_freq - 1) * df, f_low, None, ls.IMRPhenomD,
    )
    h = np.zeros(n_freq, dtype=complex)
    m = min(n_freq, hp.data.length)
    h[:m] = hp.data.data[:m]
    return h


def gate(d: np.ndarray, fs: float, index: float) -> np.ndarray:
    """Zero the data around ``index`` with smooth edges, as LVK did for GW170817's L1 glitch."""
    half = int(GATE_HALF * fs)
    lo, hi = max(0, int(index) - 2 * half), min(len(d), int(index) + 2 * half)
    window = np.ones(len(d))
    window[lo:hi] = 1 - tukey(4 * half, alpha=0.5)[: hi - lo]
    return d * window


class Detector:
    """One detector's data, Fourier-transformed once and filtered with any template."""

    def __init__(self, raw, f_low: float, gps: float, gates: list[float], lead: float | None = None) -> None:
        d, self.fs = raw.value, raw.sample_rate.value
        for offset in gates:
            d = gate(d, self.fs, (gps + offset - raw.t0.value) * self.fs)
        self.n, dt = len(d), 1 / self.fs
        self.df = 1 / (self.n * dt)
        self.f_low = f_low
        freqs = np.fft.rfftfreq(self.n, dt)

        # 1. Noise power spectrum S(f): average of FFTs of 4 s chunks (Welch), on the data's grid.
        psd_f, psd = welch(d, fs=self.fs, nperseg=int(PSD_SECONDS * self.fs), average="median")
        self.S = np.interp(freqs, psd_f, psd)

        # 2. Fourier transform of the data; taper the ends so the cut does not ring.
        self.d_f = np.fft.rfft(d * tukey(self.n, alpha=TAPER)) * dt
        self.band = (freqs >= f_low) & (freqs <= 0.9 * self.fs / 2)

        # Times where the template neither wraps around the start nor hits the tapers.
        duration = self.n * dt
        edge = TAPER / 2 * duration
        lead = duration / 4 if lead is None else lead
        t = raw.t0.value + np.arange(self.n) * dt - gps
        self.valid = (t >= t[0] + edge + lead) & (t <= t[-1] - edge)
        self.t = t[self.valid]

    def snr(self, m1: float, m2: float) -> np.ndarray:
        """SNR for each merger time in ``self.t`` (seconds from --gps)."""
        return self.filter(template(m1, m2, self.f_low, self.df, len(self.S)))

    def filter(self, h_f: np.ndarray) -> np.ndarray:
        """SNR time series for a frequency-domain template on this detector's grid."""
        b = self.band
        # 3. Compare data and template at every time shift at once: multiply in frequency
        #    (weighting by 1/S, so noisy frequencies count less), then inverse FFT.
        weighted = np.zeros(len(self.S), dtype=complex)
        weighted[b] = self.d_f[b] * np.conj(h_f[b]) / self.S[b]
        z = 4 * self.df * self.n * np.fft.ifft(weighted, self.n)  # complex: covers every phase
        # 4. Divide by how loud the template itself is in this noise, so noise gives SNR ~1.
        sigma = np.sqrt(4 * self.df * np.sum(np.abs(h_f[b]) ** 2 / self.S[b]))
        return np.abs(z[self.valid]) / sigma


def peak(t: np.ndarray, snr: np.ndarray, centre: float, half: float) -> tuple[float, float]:
    near = np.abs(t - centre) <= half
    k = np.flatnonzero(near)[np.argmax(snr[near])]
    return float(snr[k]), float(t[k])


def coincident_peaks(series: dict[str, tuple[np.ndarray, np.ndarray]]) -> dict[str, tuple[float, float]]:
    """SNR and time per detector: loudest detector near --gps, the others within COINCIDENCE of it."""
    first = {ifo: peak(t, snr, 0.0, EVENT_WINDOW) for ifo, (t, snr) in series.items()}
    ref = max(first.values())[1]
    return {ifo: peak(t, snr, ref, COINCIDENCE) for ifo, (t, snr) in series.items()}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("files", nargs="*", help="strain .hdf5 files named <IFO>-<gps>-<dur>.hdf5")
    p.add_argument("--gps", type=float, required=True, help="event (merger) GPS time")
    p.add_argument("--masses", type=float, nargs=2, required=True, metavar=("M1", "M2"),
                   help="component masses in the source frame (solar masses), as in the catalogue")
    p.add_argument("--redshift", type=float, default=0.0, help="catalogue redshift")
    p.add_argument("--fetch", nargs="+", metavar="IFO", help="fetch these detectors from GWOSC instead")
    p.add_argument("--duration", type=float, default=32.0, help="seconds to fetch with --fetch")
    p.add_argument("--fmin", type=float, default=20.0, help="lowest frequency used")
    p.add_argument("--gate", nargs="+", default=[], metavar="IFO:OFFSET",
                   help="zero 0.5 s of a detector's data around a glitch, e.g. L1:-1.02 (s from --gps)")
    p.add_argument("--label", default="event", help="name used in titles")
    p.add_argument("--out", default="matched_filter", help="output folder for the plot")
    args = p.parse_args()
    if not args.files and not args.fetch:
        p.error("give strain files or --fetch IFO ...")

    # The detectors see masses stretched by the expansion of the universe.
    m1, m2 = (m * (1 + args.redshift) for m in args.masses)
    mc = chirp_mass(m1, m2)
    gates: dict[str, list[float]] = {}
    for spec in args.gate:
        ifo, _, offset = spec.partition(":")
        gates.setdefault(ifo, []).append(float(offset))

    results: dict[str, dict] = {}
    detectors: dict[str, Detector] = {}
    for ifo, raw in load(args).items():
        if np.isnan(raw.value).any():
            results[ifo] = {"error": "data contains NaN (a gap in the public data)"}
            continue
        # The template must fit well inside the data; long signals start at a higher frequency.
        f_low = start_frequency(mc, raw.duration.value / 4, args.fmin)
        det = Detector(raw, f_low, args.gps, gates.get(ifo, []))
        if not det.t.size or not det.t[0] + EVENT_WINDOW <= 0 <= det.t[-1] - EVENT_WINDOW:
            results[ifo] = {"error": f"--gps is not inside the usable part of the {raw.duration.value:.0f} s of data"}
            continue
        detectors[ifo] = det

    best = None
    if detectors:
        # Catalogue masses are rounded medians; a long signal needs a template that stays in step
        # for thousands of cycles, so try a small bank of scaled masses and keep the best.
        f_low = min(det.f_low for det in detectors.values())
        spread = float(np.clip(2 / cycles(mc, f_low), 0.003, 0.1))
        for scale in 1 + spread * np.linspace(-1, 1, BANK_SIZE):
            series = {ifo: (det.t, det.snr(m1 * scale, m2 * scale)) for ifo, det in detectors.items()}
            peaks = coincident_peaks(series)
            combined = float(np.sqrt(sum(s ** 2 for s, _ in peaks.values())))
            if best is None or combined > best[0]:
                best = (combined, scale, series, peaks)

    summary: dict = {"event": args.label, "gps": args.gps, "detectors": results}
    if best:
        combined, scale, series, peaks = best
        for ifo, (t, snr) in series.items():
            snr_at, t_at = peaks[ifo]
            away = np.abs(t - t_at) > BACKGROUND_GAP
            results[ifo] = {
                "snr": round(snr_at, 2),
                "time_offset_s": round(t_at, 4),
                "background_max_snr": round(float(snr[away].max()), 2),
                "searched_s": [round(float(t[0]), 2), round(float(t[-1]), 2)],
                "f_low_hz": round(detectors[ifo].f_low, 1),
                **({"gated_s": gates[ifo]} if ifo in gates else {}),
            }
        summary["combined_snr"] = round(combined, 2)
        summary["template"] = {
            "approximant": "IMRPhenomD, no spins",
            "detector_frame_masses": [round(m1 * scale, 3), round(m2 * scale, 3)],
            "bank": f"{BANK_SIZE} mass scalings within ±{spread:.2%}; best {scale - 1:+.3%}",
        }

        fig, (full, zoom) = plt.subplots(2, 1, figsize=(9, 6))
        for ifo, (t, snr) in series.items():
            full.plot(t, snr, lw=0.7, label=ifo)
            near = np.abs(t) <= 0.15
            zoom.plot(t[near], snr[near], lw=1, label=ifo)
        full.set_title(f"{args.label} matched-filter SNR")
        full.legend()
        zoom.set_xlabel(f"Time (s) from GPS {args.gps}")
        for ax in (full, zoom):
            ax.set_ylabel("SNR")
        fig.tight_layout()
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        fig.savefig(out / "snr.png", dpi=120)
        plt.close(fig)
        summary["snr_png"] = str(out / "snr.png")

    json.dump(summary, sys.stdout, indent=2)
    print()
    return 0 if best else 1


if __name__ == "__main__":
    sys.exit(main())
