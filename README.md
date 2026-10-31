# GraceDB MCP Server

[![PyPI](https://img.shields.io/pypi/v/gracedb-mcp)](https://pypi.org/project/gracedb-mcp/)
[![CI](https://github.com/izabelamaria24/gracedb-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/izabelamaria24/gracedb-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/gracedb-mcp)](https://github.com/izabelamaria24/gracedb-mcp/blob/main/LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23810872.svg)](https://doi.org/10.5281/zenodo.23810872)
[![GraceDB MCP on mcprush](https://mcprush.com/badge/gracedb-mcp/score.svg)](https://mcprush.com/to/gracedb-mcp?ref=badge)

<!-- mcp-name: io.github.izabelamaria24/gracedb-mcp -->

An open-source [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that
exposes **LIGO/Virgo/KAGRA gravitational-wave alerts** from
[GraceDB](https://gracedb.ligo.org) to LLM agents. It bridges the official
[`ligo-gracedb`](https://ligo-gracedb.readthedocs.io) client with the MCP standard so an
agent can query, classify, and fetch data for gravitational-wave candidate events.

Intended as the **data-ingestion layer** for a downstream signal-processing / anomaly-detection
pipeline (e.g. isolating astrophysical "chirps" from instrumental glitches).

## Tools

| Tool | What it does |
| --- | --- |
| `search_superevents` | Search recent superevents by time window and significance (FAR / astrophysical probability). |
| `get_event_metadata` | Full superevent record + source classification (BBH / BNS / NSBH / Terrestrial). |
| `download_event_files` | Download GraceDB artifacts: skymaps, `p_astro.json`, PE results, logs. |
| `download_strain_data` | Fetch **public** strain time series from GWOSC around the event GPS time. |
| `utc_to_gps` | Convert an ISO-8601 UTC timestamp to GPS seconds. |
| `search_catalog_events` | Search published GWOSC catalogue events (GWTC-1 to GWTC-5) by masses, distance, SNR, p_astro. |
| `get_catalog_event` | Source parameters with 90% intervals for one event, by GW name or GraceDB id. |
| `get_event_labels` | GraceDB labels (data quality, review, retraction, products ready) explained in plain English. |
| `check_data_availability` | Which detectors have public GWOSC strain for an event or GPS span, before you download. |
| `check_data_quality` | Per detector: which data-quality vetoes (CBC, Burst, CW, stochastic) the data passes, and any hardware injections. |
| `list_observing_runs` | Released observing runs with dates, detectors and DOIs; which run (if any) covers a given time. |

## Resources

Every file the server downloads (up to 50 MB) is also an MCP resource, so an agent can read it
without filesystem access, which is useful when the server runs in Docker or on another machine:

```text
gracedb://files/S190814bv/p_astro.json                       (application/json)
gracedb://files/S190814bv/bayestar.png                       (image/png)
gracedb://files/GW150914/strain/H1-1126259458-8.hdf5         (application/x-hdf5)
```

The download tools return these URIs, clients are notified when new files appear, and files
already in the data directory are listed at startup. Larger files, such as full
parameter-estimation posteriors, stay on disk only.

## Prompts

Ready-made workflows that chain the tools. Most clients show them as slash commands.

| Prompt | Arguments | What you get |
| --- | --- | --- |
| `alert_briefing` | `hours` (24), `max_far` (3.8e-7 Hz, about one false alarm per month), `category` (`Production`, or `MDC` for the simulated alerts GraceDB creates about hourly) | New alerts triaged: retractions and data-quality flags first, then class, significance and sky map. |
| `event_dossier` | `event` (`S190814bv` or `GW190814`), `audience` (`scientist`, `student`, `public`) | One event end to end: significance, review history, alert vs. final parameters, sky map, public data. |
| `alert_vs_catalog` | `event`, or `catalog` + `max_events` (15) | How well the low-latency classification matched the final published masses. |

### ⚠️ Important: where strain data actually comes from

GraceDB does **not** host raw strain data. It stores *event artifacts* (skymaps, classifications,
parameter-estimation results). The public strain time series lives at
**[GWOSC](https://gwosc.org)** and is fetched by GPS time via `gwpy`. Public strain is only
available for **released observing runs** — fresh low-latency alerts will not have public strain
yet. `download_strain_data` reflects this: it reads `t_0` from GraceDB, then pulls open data from
GWOSC (and cleanly reports when data isn't public).

## Requirements

- Python 3.10+
- `mcp`, `ligo-gracedb`, `gwosc`, `python-dotenv`
- Optional: `gwpy` (for `download_strain_data`) — installed via the `strain` extra.

## Install

Run straight from PyPI with [uv](https://docs.astral.sh/uv/):

```bash
uvx --from "gracedb-mcp[strain]" gracedb-mcp
```

Or from source:

```bash
git clone https://github.com/izabelamaria24/gracedb-mcp.git
cd gracedb-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[strain]"     # omit [strain] if you don't need strain download
```

## Configuration

Copy `.env.example` to `.env` and adjust. Anonymous access is enough for **public** superevents:

```bash
cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `GRACEDB_SERVICE_URL` | API base URL (production or playground). |
| `GRACEDB_FORCE_NOAUTH` | `true` for anonymous public access. |
| `GRACEDB_PROXY` / `GRACEDB_CERT` + `GRACEDB_KEY` | LVK member authentication (X.509). |
| `GRACEDB_MCP_DATA_DIR` | Where downloaded files are written (default `~/.cache/gracedb-mcp`; `/data` in Docker). |

## Run locally

```bash
gracedb-mcp          # speaks MCP JSON-RPC over stdio
```

## Development

Requires Python 3.11+ (the `mcp` 2.x SDK needs 3.10+).

```bash
pip install -e ".[dev]"
pytest -q            # tests mock the GraceDB client, no network needed
```

## Run with Docker

A prebuilt image (linux/amd64 and arm64) is published to GHCR:

```bash
docker run --rm -i -v gracedb-data:/data ghcr.io/izabelamaria24/gracedb-mcp:latest
```

| Tag | Download | Contents |
| --- | --- | --- |
| `latest`, `X.Y.Z` | ~165 MB | Everything, including strain download (`gwpy`). |
| `latest-slim`, `X.Y.Z-slim` | ~60 MB | All tools except `download_strain_data`. |

Or build it yourself (add `--build-arg EXTRAS=` for the slim variant):

```bash
docker build -t gracedb-mcp .
docker run --rm -i -v gracedb-data:/data gracedb-mcp
```

## Connect to an MCP client

### Claude Desktop / VS Code (`mcp.json`)

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "gracedb": {
      "command": "gracedb-mcp",
      "env": {
        "GRACEDB_FORCE_NOAUTH": "true",
        "GRACEDB_MCP_DATA_DIR": "./data"
      }
    }
  }
}
```

Or run the container directly:

```json
{
  "mcpServers": {
    "gracedb": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-v", "gracedb-data:/data", "ghcr.io/izabelamaria24/gracedb-mcp:latest"]
    }
  }
}
```

## Example agent prompts

- "Search for superevents from 2023-05-01 to 2023-06-01 with a FAR below 1e-8."
- "What's the source classification for S230518h? Is it more likely a BBH or an NSBH?"
- "Download the skymap and p_astro file for S230518h."
- "Fetch H1 and L1 strain around GW150914."
- "What are the masses of GW190814, and how certain are they?"
- "List GWTC-3 events with total mass under 10 solar masses."
- "Was S190814bv checked by a human? What do its labels mean?"
- "Is there public strain for GW190814 from every detector?"

## Demo

A real session (tool results trimmed to the key fields).

> **You:** Tell me about S190814bv. Was it real, what was it, and can I get its data?

| Tool call | Result |
| --- | --- |
| `get_event_metadata("S190814bv")` | FAR 2.0e-33 Hz; alert classification NSBH 0.998, MassGap 0.002; 63 files |
| `get_event_labels("S190814bv")` | DQOK, SKYMAP_READY, PASTRO_READY, ADVOK (human advocate approved), PE_READY; not retracted |
| `download_event_files("S190814bv", ["bayestar.png"])` | `gracedb://files/S190814bv/bayestar.png` (sky map, viewable as an image) |
| `get_catalog_event("S190814bv")` | GW190814 in GWTC-2.1: 23.3 (21.9–24.7) and 2.6 (2.5–2.7) solar masses, 230 (180–270) Mpc, SNR 25.3 |
| `check_data_availability(event="S190814bv")` | Public strain from H1, L1 and Virgo (H1 only via the event's own file) |
| `download_strain_data("S190814bv", ["H1", "L1", "V1"], duration=30)` | `gracedb://files/S190814bv/strain/L1-1249852242-30.hdf5`, and the same for H1 and V1 |

> **Agent:** Yes, it's real and extremely significant (false-alarm rate about 2×10⁻³³ Hz). It
> passed data-quality checks, a human advocate approved it, and it was never retracted. The
> alert called it a neutron star–black hole merger (99.8%). The final analysis (GW190814)
> found a 23-solar-mass black hole and a 2.6-solar-mass companion, which is too heavy for
> most neutron-star models and too light for known black holes, so its nature is still open.
> It was 230 Mpc away. Here's the sky map, and I've saved 30 s of data from both LIGO
> detectors and Virgo around the merger.

## RL environment (experimental)

`gw_rl` is a Gymnasium environment built on strain fetched by this server. Each episode shows the
agent a 1 s spectrogram and it answers **noise**, **chirp** or **glitch** (+1 correct, -1 wrong).

Real chirps are scarce, so training injects synthetic chirps into real detector noise; the real
catalogue events are kept for evaluation. Chirps are IMRPhenomD waveforms from LALSimulation
(`--chirp imr`, default) or a simple analytic sweep (`--chirp toy`). Glitches are real LIGO
glitches labelled by [Gravity Spy](https://zenodo.org/records/1486046) (`--glitch real`, default)
or sine-Gaussian bursts (`--glitch synthetic`).

It is not part of the PyPI package; run it from a clone of this repository (needs pip 25.1+):

```bash
pip install -e . --group rl
python scripts/fetch_training_strain.py --catalog GWTC-3-confident  # small files, a few MB
python scripts/fetch_glitches.py --files 30  # ~3 GB of GWOSC hour files, cached by gwpy
python scripts/train_agent.py --algo ppo mlp cnn --verbose
```

## Roadmap

- Streamable-HTTP transport (in addition to stdio) for remote deployments.

## Disclaimer

Unofficial; not affiliated with or endorsed by LIGO, Virgo, KAGRA or GraceDB. Respect the
[GWOSC](https://gwosc.org) and GraceDB data-use policies.

If you publish results using data fetched by this server, cite the data sources:
[GWOSC](https://gwosc.org/acknowledge/) (CC BY 4.0) and, for glitch labels,
[Gravity Spy](https://zenodo.org/records/1486046).

## Citing

If this server helped your work, please cite it: [doi:10.5281/zenodo.23810872](https://doi.org/10.5281/zenodo.23810872)
(all versions; Zenodo also lists a DOI per release). GitHub's **Cite this repository** button
(from [`CITATION.cff`](https://github.com/izabelamaria24/gracedb-mcp/blob/main/CITATION.cff))
gives APA and BibTeX.

## License

[GPL-3.0-or-later](LICENSE), matching its core dependencies `ligo-gracedb` and `gwpy`.
