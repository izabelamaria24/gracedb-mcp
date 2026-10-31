"""Whiten public GWOSC strain around an event and plot its Q-transform.

Reads the .hdf5 files written by the gracedb-mcp ``download_strain_data`` tool, or
fetches the data from GWOSC itself with ``--fetch``. Writes PNG plots and prints a
JSON summary (loudest Q-transform tile per detector) to stdout.

    python qscan.py --gps 1126259462.4 data/GW150914/strain/*.hdf5
    python qscan.py --gps 1126259462.4 --fetch H1 L1 --label GW150914

Needs gwpy (which brings matplotlib): ``pip install gwpy`` or
``uv run --with gwpy python qscan.py ...``.
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
from gwpy.timeseries import TimeSeries

# Fewer seconds than this either side of the plotted window leaves whitening edge effects.
PAD = 2.0


def load(args: argparse.Namespace) -> dict[str, TimeSeries]:
    if args.fetch:
        half = args.duration / 2
        return {
            ifo: TimeSeries.fetch_open_data(ifo, args.gps - half, args.gps + half, sample_rate=4096)
            for ifo in args.fetch
        }
    return {Path(f).name.split("-")[0]: TimeSeries.read(f) for f in args.files}


def analyse(ifo: str, raw: TimeSeries, args: argparse.Namespace, out: Path) -> dict:
    start, end = raw.t0.value, raw.t0.value + raw.duration.value
    if np.isnan(raw.value).any():
        return {"error": "data contains NaN (a gap in the public data); pick another detector or time"}
    if not (start + PAD <= args.gps - args.before and args.gps + args.after <= end - PAD):
        return {"error": f"data spans GPS {start:.1f}-{end:.1f}; too short for the requested window"}

    q = raw.q_transform(
        outseg=(args.gps - args.before, args.gps + args.after),
        frange=(args.fmin, args.fmax),
        qrange=(4, 64),
    )
    energy = q.value
    i, j = np.unravel_index(np.argmax(energy), energy.shape)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    mesh = ax.pcolormesh(
        q.times.value - args.gps, q.frequencies.value, energy.T,
        shading="auto", vmin=0, vmax=min(25, energy.max()), cmap="viridis",
    )
    ax.set_yscale("log")
    ax.set_xlabel(f"Time (s) from GPS {args.gps}")
    ax.set_ylabel("Frequency (Hz)")
    ax.set_title(f"{args.label} {ifo} Q-transform")
    fig.colorbar(mesh, ax=ax, label="Normalised energy")
    fig.tight_layout()
    q_png = out / f"{ifo}_qtransform.png"
    fig.savefig(q_png, dpi=120)
    plt.close(fig)

    white = raw.whiten(fftlength=4, overlap=2).bandpass(args.fmin, min(args.fmax, 400))
    return {
        "gps_span": [start, end],
        "sample_rate": raw.sample_rate.value,
        "peak_time_offset_s": round(float(q.times.value[i] - args.gps), 4),
        "peak_frequency_hz": round(float(q.frequencies.value[j]), 1),
        "peak_normalised_energy": round(float(energy[i, j]), 1),
        "qtransform_png": str(q_png),
        "_white": white.crop(args.gps - args.before, args.gps + args.after),
        "_asd": raw.asd(fftlength=4, overlap=2),
    }


def overview_plots(results: dict[str, dict], args: argparse.Namespace, out: Path) -> dict[str, str]:
    good = {ifo: r for ifo, r in results.items() if "error" not in r}
    if not good:
        return {}
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for ifo, r in good.items():
        asd = r.pop("_asd")
        ax.loglog(asd.frequencies.value, asd.value, label=ifo, lw=0.8)
    ax.set_xlim(10, 2000)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel(r"Strain ASD (1/$\sqrt{\mathrm{Hz}}$)")
    ax.set_title(f"{args.label} noise spectrum")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "asd.png", dpi=120)
    plt.close(fig)

    fig, axes = plt.subplots(len(good), 1, figsize=(9, 2.2 * len(good) + 0.6), sharex=True, squeeze=False)
    for ax, (ifo, r) in zip(axes[:, 0], good.items()):
        white = r.pop("_white")
        ax.plot(white.times.value - args.gps, white.value, lw=0.6)
        ax.set_ylabel(f"{ifo} whitened")
    axes[-1, 0].set_xlabel(f"Time (s) from GPS {args.gps}")
    axes[0, 0].set_title(f"{args.label} whitened, band-passed strain")
    fig.tight_layout()
    fig.savefig(out / "whitened.png", dpi=120)
    plt.close(fig)
    return {"asd_png": str(out / "asd.png"), "whitened_png": str(out / "whitened.png")}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("files", nargs="*", help="strain .hdf5 files named <IFO>-<gps>-<dur>.hdf5")
    p.add_argument("--gps", type=float, required=True, help="event (merger) GPS time")
    p.add_argument("--fetch", nargs="+", metavar="IFO", help="fetch these detectors from GWOSC instead")
    p.add_argument("--duration", type=float, default=32.0, help="seconds to fetch with --fetch")
    p.add_argument("--before", type=float, default=1.0, help="seconds shown before --gps")
    p.add_argument("--after", type=float, default=0.3, help="seconds shown after --gps")
    p.add_argument("--fmin", type=float, default=20.0)
    p.add_argument("--fmax", type=float, default=512.0)
    p.add_argument("--label", default="event", help="name used in titles")
    p.add_argument("--out", default="qscan", help="output folder for the plots")
    args = p.parse_args()
    if not args.files and not args.fetch:
        p.error("give strain files or --fetch IFO ...")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for ifo, raw in load(args).items():
        try:
            results[ifo] = analyse(ifo, raw, args, out)
        except Exception as exc:  # noqa: BLE001 - one bad detector should not hide the others
            results[ifo] = {"error": f"{type(exc).__name__}: {exc}"}
    summary = {"event": args.label, "gps": args.gps, "detectors": results, **overview_plots(results, args, out)}
    json.dump(summary, sys.stdout, indent=2)
    print()
    return 0 if any("error" not in r for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
