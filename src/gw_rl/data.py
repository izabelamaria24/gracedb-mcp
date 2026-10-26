"""Turn strain files into labelled spectrograms for the chirp/glitch environment.

Only a few dozen real chirps exist, so training injects synthetic signals into
real detector noise and the real events are kept for evaluation. The
sine-Gaussian glitch is a placeholder for a real glitch catalogue such as
Gravity Spy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import spectrogram

NOISE, CHIRP, GLITCH = 0, 1, 2
LABELS = ("noise", "chirp", "glitch")

SAMPLE_RATE = 4096
WINDOW = SAMPLE_RATE  # 1 s windows
F_LOW, F_HIGH = 20.0, 500.0
_SUN_SECONDS = 4.925491e-6  # G * M_sun / c^3


@dataclass
class Segment:
    """Whitened, band-passed strain from one detector around one event."""

    name: str
    x: np.ndarray
    event_index: int
    asd_freqs: np.ndarray
    asd: np.ndarray


def load_segment(path: str | Path, crop: float = 2.0) -> Segment:
    """Load a file written by ``download_strain_data``, which is centred on the event."""
    from gwpy.timeseries import TimeSeries

    raw = TimeSeries.read(str(path))
    if raw.sample_rate.value != SAMPLE_RATE:
        raw = raw.resample(SAMPLE_RATE)
    start, end = raw.t0.value, raw.t0.value + raw.duration.value
    event_gps = (start + end) / 2

    asd = raw.asd(fftlength=2, overlap=1)
    white = raw.whiten(asd=asd)
    # Normalise before band-passing so injected SNRs are relative to unit white noise.
    white = white / np.std(white.crop(start + crop, end - crop).value)
    band = white.bandpass(F_LOW, F_HIGH).crop(start + crop, end - crop)

    path = Path(path)
    name = f"{path.parent.parent.name}/{path.name.split('-')[0]}"
    index = int(round((event_gps - band.t0.value) * SAMPLE_RATE))
    return Segment(
        name=name,
        x=np.asarray(band.value, dtype=np.float32),
        event_index=index,
        asd_freqs=np.asarray(asd.frequencies.value),
        asd=np.asarray(asd.value),
    )


def toy_chirp(rng: np.random.Generator, n: int = WINDOW, fs: int = SAMPLE_RATE) -> np.ndarray:
    """Leading-order inspiral sweeping up in frequency, then a short ringdown."""
    chirp_mass = rng.uniform(15.0, 45.0)
    # Rough peak frequency; Mc ~ 30 lands near GW150914's ~250 Hz.
    f_peak = min(450.0, 7500.0 / chirp_mass * rng.uniform(0.8, 1.2))
    t_c = rng.uniform(0.6, 0.95) * n / fs

    t = np.arange(n) / fs
    tau = np.clip(t_c - t, 1e-6, None)
    f = (5.0 / (256.0 * tau)) ** 0.375 * (chirp_mass * _SUN_SECONDS) ** -0.625 / np.pi
    inspiral = f < f_peak
    f = np.where(inspiral, f, f_peak)
    t_peak = t[inspiral][-1]

    ringdown = f_peak ** (2 / 3) * np.exp(-np.clip(t - t_peak, 0, None) / 0.004)
    amp = np.where(inspiral, f ** (2 / 3), ringdown)
    amp[f < F_LOW] = 0.0
    return amp * np.sin(2 * np.pi * np.cumsum(f) / fs)


def imr_chirp(rng: np.random.Generator, n: int = WINDOW, fs: int = SAMPLE_RATE) -> np.ndarray:
    """IMRPhenomD binary-black-hole waveform (inspiral, merger, ringdown) from LALSimulation."""
    import lal
    import lalsimulation as ls

    m1 = rng.uniform(8.0, 80.0)
    m2 = rng.uniform(max(3.0, m1 / 10), m1)
    s1z, s2z = rng.uniform(-0.8, 0.8, size=2)
    hp, hc = ls.SimInspiralChooseTDWaveform(
        m1 * lal.MSUN_SI, m2 * lal.MSUN_SI, 0.0, 0.0, s1z, 0.0, 0.0, s2z,
        1e6 * lal.PC_SI, np.arccos(rng.uniform(-1, 1)), 0.0, 0.0, 0.0, 0.0,
        1.0 / fs, F_LOW, F_LOW, None, ls.IMRPhenomD,
    )
    # A detector sees a mix of the two polarisations; overall scale is set later by the SNR.
    angle = rng.uniform(0, 2 * np.pi)
    h = np.cos(angle) * hp.data.data + np.sin(angle) * hc.data.data
    merger = int(round(-float(hp.epoch) * fs))

    shift = int(rng.uniform(0.6, 0.95) * n) - merger  # window index = waveform index + shift
    out = np.zeros(n)
    lo, hi = max(0, shift), min(n, shift + len(h))
    out[lo:hi] = h[lo - shift : hi - shift]
    return out


CHIRP_MODELS = {"toy": toy_chirp, "imr": imr_chirp}


def sine_gaussian(rng: np.random.Generator, n: int = WINDOW, fs: int = SAMPLE_RATE) -> np.ndarray:
    """Short sine-Gaussian burst, the usual stand-in for 'blip' glitches."""
    f0 = rng.uniform(40.0, 400.0)
    sigma = rng.uniform(3.0, 20.0) / (2 * np.pi * f0)
    t = np.arange(n) / fs - rng.uniform(0.2, 0.8) * n / fs
    return np.exp(-(t**2) / (2 * sigma**2)) * np.sin(2 * np.pi * f0 * t + rng.uniform(0, 2 * np.pi))


def inject(noise: np.ndarray, signal: np.ndarray, snr: float) -> np.ndarray:
    """Add ``signal`` scaled to optimal SNR ``snr`` in unit-variance white noise."""
    return noise + signal * (snr / np.sqrt(np.sum(signal**2)))


def whiten_like(signal: np.ndarray, seg: Segment, fs: int = SAMPLE_RATE, taper_s: float = 0.1) -> np.ndarray:
    """Filter an injection the way the segment's noise was whitened and band-passed."""
    x = signal.copy()
    k = int(taper_s * fs)
    x[:k] *= np.sin(np.linspace(0, np.pi / 2, k)) ** 2  # soften signals cut off at the window edge
    n = len(x)
    padded = np.concatenate([np.zeros(n), x, np.zeros(n)])
    f = np.fft.rfftfreq(len(padded), 1 / fs)
    spec = np.fft.rfft(padded) / np.interp(f, seg.asd_freqs, seg.asd)
    spec[(f < F_LOW) | (f > F_HIGH)] = 0
    return np.fft.irfft(spec, n=len(padded))[n : 2 * n]


def features(x: np.ndarray, fs: int = SAMPLE_RATE) -> np.ndarray:
    """Standardised log-power spectrogram of the 20-500 Hz band, shape (freq, time)."""
    f, _, power = spectrogram(x, fs=fs, nperseg=256, noverlap=128)
    s = np.log(power[(f >= F_LOW) & (f <= F_HIGH)] + 1e-12)
    return ((s - s.mean()) / (s.std() + 1e-8)).astype(np.float32)


def _noise_starts(seg: Segment, exclude: int) -> np.ndarray:
    starts = np.arange(len(seg.x) - WINDOW)
    clear = (starts + WINDOW < seg.event_index - exclude) | (starts > seg.event_index + exclude)
    return starts[clear]


def make_dataset(
    segments: list[Segment],
    n_per_class: int,
    rng: np.random.Generator,
    chirp: str = "imr",
    glitches: list[Segment] | None = None,
    snr_range: tuple[float, float] = (8.0, 30.0),
    exclude_s: float = 3.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Labelled windows: off-source noise, noise plus an injected chirp, and glitches.

    Glitch windows come from ``glitches`` (real glitches, centred in their segments)
    when given, otherwise from sine-Gaussians injected into noise.
    """
    make_chirp = CHIRP_MODELS[chirp]
    pools = [(seg, _noise_starts(seg, int(exclude_s * SAMPLE_RATE))) for seg in segments]
    pools = [(seg, starts) for seg, starts in pools if len(starts)]
    xs, ys = [], []
    for label in (NOISE, CHIRP, GLITCH):
        for _ in range(n_per_class):
            if label == GLITCH and glitches:
                glitch = glitches[rng.integers(len(glitches))]
                start = glitch.event_index - int(rng.uniform(0.2, 0.8) * WINDOW)
                xs.append(features(glitch.x[start : start + WINDOW]))
                ys.append(label)
                continue
            seg, starts = pools[rng.integers(len(pools))]
            start = starts[rng.integers(len(starts))]
            window = seg.x[start : start + WINDOW]
            if label != NOISE:
                signal = make_chirp(rng) if label == CHIRP else sine_gaussian(rng)
                window = inject(window, whiten_like(signal, seg), rng.uniform(*snr_range))
            xs.append(features(window))
            ys.append(label)
    order = rng.permutation(len(ys))
    return np.stack(xs)[order], np.asarray(ys)[order]


def event_windows(segments: list[Segment], before_s: float = 0.8) -> tuple[list[str], np.ndarray]:
    """One window per segment starting ``before_s`` before its event (merger or glitch)."""
    names, xs = [], []
    for seg in segments:
        start = seg.event_index - int(before_s * SAMPLE_RATE)
        if 0 <= start and start + WINDOW <= len(seg.x):
            names.append(seg.name)
            xs.append(features(seg.x[start : start + WINDOW]))
    return names, np.stack(xs)
