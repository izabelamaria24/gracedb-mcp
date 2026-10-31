---
name: gw-open-data-analysis
description: Look at a gravitational-wave event in public LIGO/Virgo/KAGRA data. Finds the event, checks which detectors have open data, downloads the strain from GWOSC, whitens it and plots the chirp in a Q-transform (time-frequency map), then explains what is and is not visible. Use when asked to "show the chirp", "plot GW150914", "analyse the strain of an event", "make a Q-transform/spectrogram", or "start a notebook on LIGO open data". Needs the gracedb-mcp server and local Python with gwpy.
---

# Open-data analysis starter

Takes one event from name to plots: event, then data availability, strain, whitening,
Q-transform. A short report follows. Uses the `gracedb-mcp` tools for lookup and
download, and runs [`scripts/qscan.py`](scripts/qscan.py) (next to this file) for the
signal processing.

## Requirements

- The `gracedb-mcp` MCP server.
- Python with gwpy, which brings numpy and matplotlib. Check with
  `python -c "import gwpy"`. If it is missing, either ask before running
  `pip install gwpy`, or use `uv run --with gwpy python scripts/qscan.py ...`.

## Steps

### 1. Identify the event

Call `get_catalog_event` with the name the user gave. It accepts catalogue names
(`GW150914`) and GraceDB ids (`S190814bv`). Keep these fields:

- GPS time;
- chirp mass;
- network SNR (`network_matched_filter_snr`);
- GraceDB id.

If the user has no event in mind, use `search_catalog_events`. Loud, well-known
events show the clearest chirps: GW150914, GW170814, GW190521_074359, GW190814
(in L1), GW170817 (in H1).

If the event is not in a catalogue (a recent alert), stop. Public strain is
released in blocks, typically about 18 months after the data were taken.

### 2. Check the data

Call `check_data_availability` with `event` set to the catalogue name. Use only
the detectors in `available`. Optionally call `check_data_quality` with the same
event and mention any flags it raises.

### 3. Get the strain

Call `download_strain_data` with:

- `graceid` set to the catalogue name, which names the output folder;
- `gps_time` set to the catalogue GPS time;
- the available detectors.

The default 30 s window is enough for everything below. Check that the paths in
`saved` exist on this machine. If the server runs in Docker or on another host, they
will not. In that case skip the files and let the script fetch the data itself (step 4).

### 4. Run the Q-scan

Choose the window from the catalogue's source chirp mass, in solar masses. Lighter
systems stay in band for longer.

| Chirp mass | Example | `--before` | `--after` | Other |
| --- | --- | --- | --- | --- |
| above 40 | GW190521 (63) | 0.5 | 0.2 | |
| 15 to 40 | GW150914 (28) | 1 | 0.3 | |
| 5 to 15 | GW190814 (6.1) | 3 | 0.3 | |
| 2 to 5 | neutron star + black hole | 6 | 0.3 | |
| below 2 | GW170817 (1.19) | 10 | 0.5 | `--fmin 30` |

From files saved by the server:

```bash
python scripts/qscan.py --gps <GPS> --label <NAME> --out <NAME>_qscan \
    --before 1 --after 0.3 <path/H1-...hdf5> <path/L1-...hdf5>
```

Or fetching directly from GWOSC:

```bash
python scripts/qscan.py --gps <GPS> --label <NAME> --out <NAME>_qscan --fetch H1 L1
```

The script writes these plots:

- `<IFO>_qtransform.png` for each detector;
- `whitened.png`, the whitened and band-passed strain;
- `asd.png`, the noise spectrum.

It then prints JSON giving each detector's loudest tile: offset from the GPS time,
frequency, and normalised energy. A detector with bad data gets an `error`, and the
others still run.

If the user asked for a notebook, write one whose cells follow the same steps: load,
Q-transform, plot, whiten, ASD. Use `qscan.py` as the reference, then run it.

### 5. Look, then report

Look at every Q-transform image before describing it. Do not rely on the numbers
alone. A chirp is a track that rises in frequency and ends within about 0.1 s of
offset 0. The offset is not exactly 0 because catalogue GPS times are rounded.
Report:

1. The event, GPS time, detectors used, and the window.
2. For each detector: whether a chirp is visible, plus its loudest tile.
3. Anything odd: glitches, gaps, or a detector that shows nothing.
4. The plot paths, embedded as images where the client allows.
5. Credit: "Data from the Gravitational Wave Open Science Center (gwosc.org)". Add
   the run's DOI from `list_observing_runs`.

## How to read the results

- **A chirp's visibility follows each detector's SNR, not the network SNR.** Tested on
  GW190814 (network SNR 25):
  - L1 shows a clear 3 s chirp.
  - H1 shows only a faint trace.
  - V1 shows nothing (its peak is a noise tile at 427 Hz).

  Saying "not visible in V1" is correct, not a failure.
- **Heavy systems do not look like a chirp.** GW190521 merges below 100 Hz, so it shows
  as a short blob of about 0.1 s around 60 Hz.
- **Normalised energy is not a detection statistic.** The colour scale stops at 25.
  Noise tiles reach about 15 to 25, so a peak in that range proves nothing alone.
  GW150914's peak is about 125 in H1.
- **Glitches can outshine the signal.** GW170817's L1 data has a loud glitch at
  -1.0 s (energy about 258,000). It swamps the L1 plot, while H1 shows the
  neutron-star chirp rising over the last 2 s. If the peak is huge, broadband and
  short, call it a glitch, not the event. The published analyses removed this glitch.
- **Most catalogue events are too quiet to see by eye.** If nothing shows, say so,
  and suggest a louder event for a demonstration.
- **Do not estimate masses or distances from these plots.** Quote the catalogue values
  from step 1 instead, labelled as the published results.
