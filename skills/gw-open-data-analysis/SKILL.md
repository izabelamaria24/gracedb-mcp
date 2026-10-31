---
name: gw-open-data-analysis
description: Look at a gravitational-wave event in public LIGO/Virgo/KAGRA data. Finds the event, checks which detectors have open data, downloads the strain from GWOSC, whitens it and plots the chirp in a Q-transform (time-frequency map), then matched-filters it with a waveform for the catalogue masses to measure the signal-to-noise ratio, and explains what is and is not seen. Use when asked to "show the chirp", "plot GW150914", "analyse the strain of an event", "make a Q-transform/spectrogram", "matched filter", "measure the SNR", or "start a notebook on LIGO open data". Needs the gracedb-mcp server and local Python with gwpy (plus lalsuite for the matched filter).
---

# Open-data analysis starter

Takes one event from name to plots: event, then data availability, strain, whitening,
Q-transform, and matched filter. A short report follows. Uses the `gracedb-mcp` tools
for lookup and download. The signal processing is done by two scripts next to this
file, [`scripts/qscan.py`](scripts/qscan.py) and
[`scripts/matched_filter.py`](scripts/matched_filter.py).

## Requirements

- The `gracedb-mcp` MCP server.
- Python with gwpy, which brings numpy, scipy and matplotlib. Check with
  `python -c "import gwpy"`. If it is missing, either ask before running
  `pip install gwpy`, or use `uv run --with gwpy python scripts/qscan.py ...`.
- For the matched filter, lalsuite as well (it provides the waveform model). Check with
  `python -c "import lalsimulation"`; otherwise use `pip install lalsuite`, or
  `uv run --with gwpy --with lalsuite ...`.

## Steps

### 1. Identify the event

Call `get_catalog_event` with the name the user gave. It accepts catalogue names
(`GW150914`) and GraceDB ids (`S190814bv`). Keep these fields:

- GPS time;
- chirp mass;
- component masses (`mass_1_source`, `mass_2_source`) and redshift, for the matched
  filter;
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
Q-transform, plot, whiten, ASD, then the matched filter. Use the two scripts as the
reference, then run it.

### 5. Matched filter

This is how LIGO actually detects signals. The script takes a predicted waveform for
the catalogue masses (the template) and slides it along the data. At each moment it
measures how well the template fits, as a signal-to-noise ratio (SNR). The template
has no spins. Catalogue masses are rounded, so the script tries 21 slightly scaled
templates and keeps the best fit.

```bash
python scripts/matched_filter.py --gps <GPS> --label <NAME> --out <NAME>_mf \
    --masses <mass_1_source> <mass_2_source> --redshift <redshift> <files or --fetch H1 L1 ...>
```

The script needs at least four times the signal's duration in data. The 30 s files are
enough for black-hole mergers. Neutron-star systems last minutes in band, so pass
`--fetch <IFOs> --duration 512`, which takes about a minute to download.

The script writes `snr.png`: the SNR over time, plus a zoom of ±0.15 s around the
event. It also prints JSON with:

- for each detector: `snr` near the event, `time_offset_s`, and `background_max_snr`
  (the loudest SNR elsewhere in the data, which is what noise reaches);
- `combined_snr`: the detector SNRs added in quadrature, to compare with the
  catalogue's network SNR.

### 6. Look, then report

Look at every Q-transform image before describing it. Do not rely on the numbers
alone. A chirp is a track that rises in frequency and ends within about 0.1 s of
offset 0. The offset is not exactly 0 because catalogue GPS times are rounded.
Report:

1. The event, GPS time, detectors used, and the window.
2. For each detector: whether a chirp is visible, plus its loudest tile.
3. The matched filter: SNR in each detector against its background, the combined SNR
   against the catalogue's, and the arrival-time differences between detectors.
4. Anything odd: glitches, gaps, or a detector that shows nothing.
5. The plot paths, embedded as images where the client allows.
6. Credit: "Data from the Gravitational Wave Open Science Center (gwosc.org)". Add
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
  and suggest a louder event for a demonstration. The matched filter still finds
  them: it adds up the signal over every cycle, which the eye cannot.
- **Matched-filter SNR: compare with the background, not with zero.** In noise, the
  loudest SNR across a few seconds is typically 4 to 5. A detector at 5 or below is
  not a detection on its own. Checked against published values:

  | Event | This script | Published |
  | --- | --- | --- |
  | GW150914 | H1 19.5, L1 13.6 | H1 about 20, L1 about 13 |
  | GW190814 | H1 10.4, L1 21.3, V1 4.7 | network SNR 25.3 |
  | GW190521 | combined 14.2 | 14.3 |
  | GW170817 (L1 gated) | H1 17.9, L1 24.2 | H1 18.8, L1 26.4 |

  Values a few percent under the catalogue are expected: the template has no spins
  and the masses are rounded.
- **Arrival times are physical.** At GW150914, L1's peak comes about 7 ms before
  H1's; that delay helps locate the source on the sky. Detectors more than 30 ms
  apart cannot be the same signal.
- **Glitches fool the matched filter too.** If a detector's `background_max_snr` is
  far above 5, look at its Q-transform for a glitch. For GW170817, L1's glitch at
  -1.02 s raises the background to about 15 for the whole template length (about
  2 minutes). Rerun with `--gate L1:-1.02`, which zeroes 0.5 s around the glitch, as
  LVK did. The background then drops to 5. Give the glitch's offset from the
  Q-transform, and say in the report that the data was gated. Real searches also
  run consistency tests (chi-squared) that this script lacks, so a peak alone is not
  proof of a signal.
- **Do not estimate masses or distances from these plots.** Quote the catalogue values
  from step 1 instead, labelled as the published results.
