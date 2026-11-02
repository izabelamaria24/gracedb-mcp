"""Search public GWOSC strain for a neutron-star merger around an external trigger.

For a gamma-ray burst or neutrino alert (found through GCN), this asks whether the open
data around it holds a compact-binary signal. Like the LVK gamma-ray-burst searches, it
matched-filters the data with a bank of templates and takes the loudest network SNR in
the on-source window around the trigger. It then compares that with the same statistic
in many off-source windows of the surrounding data. The false-alarm probability is the
fraction of off-source windows that are at least as loud.

    python targeted_search.py --gps 1187008884.47 --fetch H1 L1 --gate L1:-3.06 --label "GRB 170817A"

Needs gwpy and lalsuite: ``uv run --with gwpy --with lalsuite python targeted_search.py ...``.
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
from matched_filter import COINCIDENCE, SUN_SECONDS, Detector, chirp_mass, cycles, template
from qscan import load
from scipy.ndimage import maximum_filter1d

# Cycle-based spacing; 1.5 loses at most ~4% of the SNR between neighbouring templates.
BANK_DENSITY = 1.5
EXCLUDE = 1.0  # s kept free of off-source windows around the on-source window


def duration_from(mc: float, f_low: float) -> float:
    """Seconds from ``f_low`` to merger (leading order)."""
    return 5 / 256 * (np.pi * f_low) ** (-8 / 3) * (mc * SUN_SECONDS) ** (-5 / 3)


def bank(m1_min: float, m1_max: float, m2: float, f_low: float) -> list[float]:
    """Heavier-component masses spaced so neighbouring templates stay in step."""
    masses, m1 = [], m1_min
    while m1 <= m1_max:
        masses.append(m1)
        dlnmc = 1 / (BANK_DENSITY * cycles(chirp_mass(m1, m2), f_low))
        m1 *= 1 + dlnmc / (0.6 - 0.2 * m1 / (m1 + m2))  # d ln(Mc) / d ln(m1)
    return masses


def network_snr(snrs: list[np.ndarray], fs: float) -> np.ndarray:
    """First detector's SNR combined with each other detector's loudest SNR within COINCIDENCE."""
    size = 2 * int(COINCIDENCE * fs) + 1
    total = snrs[0] ** 2
    for other in snrs[1:]:
        total = total + maximum_filter1d(other, size=size) ** 2
    return np.sqrt(total)


def window_maxima(t: np.ndarray, stat: np.ndarray, on: tuple[float, float]) -> tuple[int, list[float]]:
    """Index of the on-source maximum, and the maxima of equal-length off-source windows."""
    on_mask = (t >= on[0]) & (t <= on[1])
    k_on = int(np.flatnonzero(on_mask)[np.argmax(stat[on_mask])])
    length = on[1] - on[0]
    off, start = [], t[0]
    while start + length <= t[-1]:
        end = start + length
        if end < on[0] - EXCLUDE or start > on[1] + EXCLUDE:
            mask = (t >= start) & (t < end)
            off.append(float(stat[mask].max()))
        start = end
    return k_on, off


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--gps", type=float, required=True, help="trigger time (GPS) of the GRB or neutrino")
    p.add_argument("--fetch", nargs="+", required=True, metavar="IFO", help="detectors to fetch from GWOSC")
    p.add_argument("--duration", type=float, default=512.0, help="seconds of data centred on the trigger")
    p.add_argument("--window", type=float, nargs=2, default=[-5.0, 1.0], metavar=("BEFORE", "AFTER"),
                   help="on-source window in s around the trigger (short GRBs: -5 1)")
    p.add_argument("--m1", type=float, nargs=2, default=[1.0, 2.5], metavar=("MIN", "MAX"),
                   help="range of the heavier mass in solar masses (up to ~10 adds black hole + neutron star)")
    p.add_argument("--m2", type=float, default=1.4, help="lighter mass (a neutron star)")
    p.add_argument("--fmin", type=float, default=40.0, help="lowest frequency (lower is more sensitive but slower)")
    p.add_argument("--sample-rate", type=int, default=2048)
    p.add_argument("--gate", nargs="+", default=[], metavar="IFO:OFFSET",
                   help="zero 0.5 s of a detector's data around a glitch, e.g. L1:-3.06 (s from --gps)")
    p.add_argument("--label", default="trigger", help="name used in titles")
    p.add_argument("--out", default="targeted_search", help="output folder for the plot")
    args = p.parse_args()
    args.files = []

    gates: dict[str, list[float]] = {}
    for spec in args.gate:
        ifo, _, offset = spec.partition(":")
        gates.setdefault(ifo, []).append(float(offset))

    masses = bank(args.m1[0], args.m1[1], args.m2, args.fmin)
    lead = duration_from(chirp_mass(masses[0], args.m2), args.fmin) + 1
    detectors, errors = {}, {}
    for ifo, raw in load(args).items():
        if np.isnan(raw.value).any():
            errors[ifo] = "data contains NaN (a gap in the public data)"
            continue
        if raw.sample_rate.value != args.sample_rate:
            raw = raw.resample(args.sample_rate)
        detectors[ifo] = Detector(raw, args.fmin, args.gps, gates.get(ifo, []), lead=lead)
    usable = [d for d in detectors.values() if d.t.size and d.t[0] <= args.window[0] and args.window[1] <= d.t[-1]]
    if len(usable) != len(detectors) or not detectors:
        print(json.dumps({"error": "data too short for the template length and on-source window; raise --duration or --fmin",
                          "detectors": errors}))
        return 1

    ifos = list(detectors)
    first = detectors[ifos[0]]
    t = first.t
    best = np.zeros_like(t)
    best_template = np.zeros(t.size, dtype=int)
    for i, m1 in enumerate(masses):
        h_f = template(m1, args.m2, args.fmin, first.df, len(first.S))
        stat = network_snr([detectors[ifo].filter(h_f) for ifo in ifos], args.sample_rate)
        louder = stat > best
        best[louder], best_template[louder] = stat[louder], i

    k_on, off = window_maxima(t, best, tuple(args.window))
    on_snr, m1_on = float(best[k_on]), masses[best_template[k_on]]
    per_ifo = {}
    for j, ifo in enumerate(ifos):
        snr = detectors[ifo].snr(m1_on, args.m2)
        half = 0 if j == 0 else int(COINCIDENCE * args.sample_rate)
        per_ifo[ifo] = round(float(snr[max(0, k_on - half): k_on + half + 1].max()), 2)
    off_arr = np.array(off)
    louder_off = int((off_arr >= on_snr).sum())

    summary = {
        "trigger": args.label,
        "gps": args.gps,
        "detectors": ifos,
        "errors": errors,
        "bank": {
            "templates": len(masses),
            "m1_range": [round(masses[0], 3), round(masses[-1], 3)],
            "m2": args.m2,
            "f_low_hz": args.fmin,
            "approximant": "IMRPhenomD, no spins",
        },
        "on_source": {
            "window_s": args.window,
            "network_snr": round(on_snr, 2),
            "time_offset_s": round(float(t[k_on]), 4),
            "detector_snr": per_ifo,
            "template_masses": [round(m1_on, 3), args.m2],
        },
        "off_source": {
            "windows": len(off),
            "median": round(float(np.median(off_arr)), 2),
            "max": round(float(off_arr.max()), 2),
            "louder_than_on_source": louder_off,
        },
        "false_alarm_probability": round((louder_off + 1) / (len(off) + 1), 4),
    }
    if not louder_off:
        summary["note"] = (
            f"No off-source window was as loud, so the false-alarm probability is only bounded by the "
            f"{len(off)} windows; a longer --duration gives a tighter bound."
        )

    fig, (series, hist) = plt.subplots(2, 1, figsize=(9, 6.5))
    series.plot(t, best, lw=0.4, color="0.4")
    series.axvspan(*args.window, color="tab:red", alpha=0.25, label="on-source")
    series.set_xlabel(f"Time (s) from GPS {args.gps}")
    series.set_ylabel("Network SNR (best template)")
    series.set_title(f"{args.label}: targeted compact-binary search")
    series.legend()
    hist.hist(off_arr, bins=20, color="0.6", label="off-source windows")
    hist.axvline(on_snr, color="tab:red", label=f"on-source {on_snr:.1f}")
    hist.set_xlabel("Loudest network SNR in window")
    hist.set_ylabel("Windows")
    hist.legend()
    fig.tight_layout()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "targeted_search.png", dpi=120)
    plt.close(fig)
    summary["plot_png"] = str(out / "targeted_search.png")

    json.dump(summary, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
